"""Republish only the canonical installer in the existing public download root."""

import json
import os
import tempfile
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from dashboard.installer_contract import canonical_installer


def atomic_write(path, data):
    fd, name = tempfile.mkstemp(prefix='installer-publish-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(name, 0o644)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class Command(BaseCommand):
    help = 'Publish the reviewed installer/checksum only; never rebuild or modify agent packages.'

    def add_arguments(self, parser):
        parser.add_argument('--destination', required=True)
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        root = Path(options['destination']).resolve()
        # The shared legacy root is separate from immutable, signed release directories.
        if (not root.is_dir() or root.name != 'windows' or root.parent.name != 'agent' or
                (root / 'release-manifest.json').exists() or (root / 'release-manifest.sig').exists()):
            raise CommandError('INSTALLER_PUBLICATION_LAYOUT_UNSUPPORTED')
        name = 'Install-NightOwlAgentDotNet.ps1'
        content, sha = canonical_installer()
        try:
            checksums = json.loads((root / 'checksums.json').read_text(encoding='utf-8-sig'))
            entries = [item for item in checksums['files'] if item['name'] == name]
            if len(entries) != 1 or name not in checksums:
                raise ValueError
        except (OSError, KeyError, TypeError, ValueError):
            raise CommandError('INSTALLER_CHECKSUM_LAYOUT_UNSUPPORTED') from None
        checksums[name] = sha
        entries[0].update(sha256=sha, size=len(content))
        if options['apply']:
            atomic_write(root / name, content)
            atomic_write(root / 'checksums.json', (json.dumps(checksums, indent=2) + '\n').encode())
        self.stdout.write(json.dumps({'applied': options['apply'], 'installer_sha256': sha,
                                     'installer_size': len(content)}))
