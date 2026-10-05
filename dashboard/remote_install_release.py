"""Read-only validation of a server-selected, immutable installation release.

The signed ZIP authenticates its embedded installer, even for manifests without
a separate installer digest. Canonical installers belong to another contract.
"""

import base64
import hashlib
import json
import re
import tempfile
import zipfile
from pathlib import PurePosixPath
from urllib.parse import urlsplit
from xml.etree import ElementTree

import requests
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.conf import settings

from agents.models import AgentRelease, AgentReleaseSigningKey
from agents.services import ensure_release_signature_policy
from dashboard.installer_contract import InstallerContractFailure, MAX_INSTALLER_BYTES


INSTALLER = 'Install-NightOwlAgentDotNet.ps1'
MAX_PACKAGE_BYTES = 256 * 1024 * 1024


def selected_install_release():
    release = AgentRelease.objects.get(pk=settings.REMOTE_INSTALL_RELEASE_ID)
    if (release.revoked or release.legacy_unsigned or release.status not in ('published', 'paused')):
        raise ValueError
    ensure_release_signature_policy(release)
    return release


def installation_release_display():
    try:
        release = selected_install_release()
        return {'version': release.version, 'channel': release.channel}
    except Exception:
        return None


def _download(session, url, destination, limit):
    digest, size = hashlib.sha256(), 0
    with session.get(url, timeout=(5, 10), stream=True, allow_redirects=False, verify=True) as response:
        if response.status_code != 200:
            raise ValueError
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > limit:
                raise ValueError
            digest.update(chunk)
            destination.write(chunk)
    destination.seek(0)
    return digest.hexdigest(), size


def validate_install_release():
    try:
        return _validate_install_release()
    except Exception:
        # Network/crypto/XML errors must not expose responses, URLs or credentials.
        raise InstallerContractFailure('INSTALL_RELEASE_INVALID') from None


def _validate_install_release():
    import io

    release = selected_install_release()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.+-]{0,49}', release.version):
        raise ValueError
    base = str(settings.NIGHTOWL_AGENT_PUBLIC_SERVER_URL).rstrip('/')
    parsed = urlsplit(base)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or
            parsed.query or parsed.fragment or any(c in base for c in "'\"`\r\n ")):
        raise ValueError
    artifact_base = base + '/downloads/nightowl-agent/releases/' + release.version
    urls = {name: artifact_base + '/' + name for name in (
        'NightOwl.Agent.Windows.zip', 'release-manifest.json', 'release-manifest.sig',
        'checksums.json', INSTALLER)}
    if (release.package_url != urls['NightOwl.Agent.Windows.zip'] or
            release.manifest_url != urls['release-manifest.json'] or
            release.signature_url != urls['release-manifest.sig'] or
            release.checksum_url != urls['checksums.json'] or not 0 < release.size <= MAX_PACKAGE_BYTES):
        raise ValueError
    key = AgentReleaseSigningKey.objects.get(key_id=release.signature_key_id, status='active')
    if key.algorithm != 'RSA-PSS-SHA256':
        raise ValueError
    xml = ElementTree.fromstring(key.public_key_xml)
    if xml.tag != 'RSAKeyValue' or {child.tag for child in xml} != {'Modulus', 'Exponent'} or len(xml) != 2:
        raise ValueError
    public = rsa.RSAPublicNumbers(
        int.from_bytes(base64.b64decode(xml.findtext('Exponent'), validate=True), 'big'),
        int.from_bytes(base64.b64decode(xml.findtext('Modulus'), validate=True), 'big')).public_key()
    if public.key_size < 2048:
        raise ValueError
    with requests.Session() as session, tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as package:
        session.trust_env = False
        small = {}
        for name in ('release-manifest.json', 'release-manifest.sig', 'checksums.json', INSTALLER):
            buf = io.BytesIO()
            digest, _ = _download(session, urls[name], buf, MAX_INSTALLER_BYTES)
            small[name] = buf.getvalue()
            expected = {'release-manifest.json': release.manifest_sha256,
                        'release-manifest.sig': release.signature_sha256}.get(name)
            if expected and digest != expected:
                raise ValueError
        public.verify(base64.b64decode(small['release-manifest.sig'].strip(), validate=True),
                      small['release-manifest.json'],
                      padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32), hashes.SHA256())
        manifest = json.loads(small['release-manifest.json'])
        if (manifest['version'] != release.version or manifest['key_id'] != key.key_id or
                manifest['channel'] != (release.source_channel or release.channel) or
                manifest['package']['sha256'] != release.sha256 or
                manifest['package']['size'] != release.size or
                manifest.get('legacy_unsigned') is not False or
                not re.fullmatch('[a-f0-9]{40}', manifest['git_commit']) or
                not re.fullmatch('[a-f0-9]{32}', manifest['build_id'])):
            raise ValueError
        digest, size = _download(session, release.package_url, package, release.size)
        if digest != release.sha256 or size != release.size:
            raise ValueError
        with zipfile.ZipFile(package) as archive:
            names = archive.namelist()
            folded = [name.replace('\\', '/').casefold() for name in names]
            if len(folded) != len(set(folded)) or len(names) > 4096:
                raise ValueError
            for member in archive.infolist():
                name = member.filename.replace('\\', '/')
                if (':' in name or '\x00' in name or
                        PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts or
                        (member.external_attr >> 16) & 0o170000 == 0o120000):
                    raise ValueError
            members = {name.replace('\\', '/'): name for name in names}
            def read_member(name):
                member = members[name]
                if archive.getinfo(member).file_size > MAX_INSTALLER_BYTES:
                    raise ValueError
                return archive.read(member)
            for name in manifest['required_zip_entries']:
                archive.getinfo(members[name.replace('\\', '/')])
            frozen = read_member(INSTALLER)
            if frozen != small[INSTALLER]:
                raise ValueError
            installer_sha = hashlib.sha256(frozen).hexdigest()
            if manifest.get('installer') and manifest['installer']['sha256'] != installer_sha:
                raise ValueError
            checksums = json.loads(small['checksums.json'])
            entries = [item for item in checksums['files'] if item['name'] == INSTALLER]
            if (checksums[INSTALLER] != installer_sha or len(entries) != 1 or
                    entries[0]['sha256'] != installer_sha or entries[0]['size'] != len(frozen)):
                raise ValueError
            metadata = json.loads(read_member('agent.version.json'))
            for field in ('version', 'git_commit', 'build_id', 'channel'):
                if metadata[field] != manifest[field]:
                    raise ValueError
        # Recheck revocation/expiry after I/O, before constructing any remote command.
        current = selected_install_release()
        current_key = AgentReleaseSigningKey.objects.get(pk=key.pk)
        if (current.pk != release.pk or current.sha256 != release.sha256 or
                current_key.public_key_xml != key.public_key_xml or
                current_key.algorithm != key.algorithm):
            raise ValueError
    return {'release_id': str(release.pk), 'version': release.version,
            'channel': manifest['channel'], 'package_sha256': release.sha256,
            'installer_sha256': installer_sha, 'installer_contract_valid': True,
            'release_validated': True, 'installer_source': urls[INSTALLER],
            'package_url': release.package_url, 'git_commit': manifest['git_commit'],
            'build_id': manifest['build_id'], 'signing_key_id': key.key_id,
            'trusted_public_keys': {'keys': [{'key_id': key.key_id, 'status': 'active',
                'algorithm': key.algorithm, 'public_key_xml': key.public_key_xml}]}}
