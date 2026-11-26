import hashlib
import json
import subprocess
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


KEYS = (
    'NIGHTOWL_TERMINAL_ENABLED', 'NIGHTOWL_TERMINAL_SINGLE_PROCESS',
    'NIGHTOWL_TERMINAL_CONPTY_ENABLED', 'NIGHTOWL_TERMINAL_BROWSER_ENABLED',
    'NIGHTOWL_TERMINAL_ADMIN_AUTH_ENABLED', 'NIGHTOWL_TERMINAL_GRANT_ADMIN_USERS',
    'NIGHTOWL_SERVICE_COLLECTION_ENABLED', 'NIGHTOWL_SERVICE_COLLECTION_ALLOWED_ENDPOINT_IDS',
    'NIGHTOWL_OPERATIONAL_ACTIONS_ENABLED', 'NIGHTOWL_OPERATIONAL_ACTION_SCOPED_ONLY',
    'NIGHTOWL_OPERATIONAL_ACTION_SCOPES', 'NIGHTOWL_ADMIN_ACTIONS_ENABLED',
    'NIGHTOWL_ADMIN_ACTION_SCOPES', 'NIGHTOWL_SERVICE_CONTROL_ENABLED',
    'NIGHTOWL_APPROVED_SCRIPTS_ENABLED', 'NIGHTOWL_REVOKED_SCRIPT_VERSIONS',
)
UNITS = ('nightowl.service', 'nightowl-terminal.service', 'nightowl-maintenance.timer')
PROPERTIES = ('ActiveState', 'UnitFileState', 'Restart', 'LoadState')


def snapshot(include_systemd):
    def digest(value):
        encoded = json.dumps(value, sort_keys=True, default=lambda v: sorted(str(x) for x in v))
        return hashlib.sha256(encoded.encode()).hexdigest()

    values = {key: digest(getattr(settings, key)) for key in KEYS}
    if include_systemd:
        for unit in UNITS:
            process = subprocess.run(
                ['systemctl', 'show', '--property=' + ','.join(PROPERTIES), unit],
                capture_output=True, text=True, check=True, timeout=10,
            )
            state = dict(line.split('=', 1) for line in process.stdout.splitlines() if '=' in line)
            if state.get('LoadState') != 'loaded' or not {'ActiveState', 'UnitFileState'} <= state.keys():
                raise ValueError(f'Unit state unavailable: {unit}')
            values[unit] = {key: state[key] for key in PROPERTIES if key in state}
    return {'schema': 1, 'systemd': include_systemd, 'values': values}


class Command(BaseCommand):
    help = 'Emit a redacted operational baseline, or fail on changes against a prior baseline.'

    def add_arguments(self, parser):
        parser.add_argument('--compare', type=Path)
        parser.add_argument('--settings-only', action='store_true')

    def handle(self, *args, **options):
        try:
            current = snapshot(not options['settings_only'])
            if options['compare'] is None:
                self.stdout.write(json.dumps(current, sort_keys=True))
                return
            baseline = json.loads(options['compare'].read_text(encoding='utf-8'))
            if (not isinstance(baseline, dict) or baseline.get('schema') != 1
                    or baseline.get('systemd') is not current['systemd']
                    or not isinstance(baseline.get('values'), dict)
                    or baseline['values'].keys() != current['values'].keys()):
                raise ValueError('Incompatible continuity baseline.')
            changed = [key for key in current['values'] if current['values'][key] != baseline['values'][key]]
            if changed:
                raise CommandError('Operational continuity changed: ' + ', '.join(changed))
            self.stdout.write('Operational continuity verified.')
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise CommandError('Could not verify operational continuity: ' + type(exc).__name__) from None
