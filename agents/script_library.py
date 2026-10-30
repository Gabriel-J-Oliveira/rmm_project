"""Reviewed code registry, not operator-supplied content or an upload mechanism."""
import hashlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType

from django.conf import settings
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from config.authz import is_nightowl_technical_user


@dataclass(frozen=True)
class ScriptVersion:
    script_id: str
    version: int
    sha256: str
    language: str = 'powershell'
    runtime: str = 'windows_powershell_5_1'
    description: str = 'Read-only UTC time and PowerShell version; no input parameters.'
    approved: bool = False
    permission: str = 'agents.view_agentmachine'
    timeout_seconds: int = 15
    max_output_bytes: int = 4096


DEMO = ScriptVersion('environment_diagnostic', 1,
    '8791bd2ddb422912abfa6c8909062b7c386fc5645bd7fc801992c4f816293521', approved=True)
CATALOG = MappingProxyType({(DEMO.script_id, DEMO.version): DEMO})
SOURCE = Path(__file__).parent / 'approved_scripts' / 'environment_diagnostic_v1.ps1'


def resolve(script_id, version):
    if not isinstance(script_id, str) or type(version) is not int:
        raise ValueError('invalid_script_identity')
    script = CATALOG.get((script_id, version))
    if script is None:
        raise ValueError('unknown_script_version')
    if not script.approved:
        raise ValueError('script_not_approved')
    if f'{script_id}:{version}' in getattr(settings, 'NIGHTOWL_REVOKED_SCRIPT_VERSIONS', ()):
        raise ValueError('script_revoked')
    try:
        digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    except OSError:
        raise ValueError('script_content_unavailable') from None
    if script != DEMO or digest != script.sha256:
        raise ValueError('script_integrity_mismatch')
    return script


def validate_reference(value):
    if not isinstance(value, dict) or set(value) != {'script_id', 'script_version', 'sha256', 'parameters'}:
        raise ValueError('invalid_script_reference')
    script = resolve(value['script_id'], value['script_version'])
    if value['sha256'] != script.sha256:
        raise ValueError('script_integrity_mismatch')
    if not isinstance(value['parameters'], dict) or value['parameters']:
        raise ValueError('invalid_script_parameters')
    return dict(value)


def validate_dispatch(job, value):
    script = resolve(value['script_id'], value['script_version'])
    if not getattr(settings, 'NIGHTOWL_APPROVED_SCRIPTS_ENABLED', False):
        raise ValueError('approved_scripts_disabled')
    from .automation import endpoint_identity
    from .internal_jobs import internal_capability
    endpoint_identity(job.endpoint)
    if not internal_capability(job.endpoint, capability(script)):
        raise ValueError('script_capability_unconfirmed')


def capability(script):
    return f'approved_script:{script.script_id}:{script.version}:{script.sha256}'


def validate_completion(value, output):
    # Receipt of an already executed result remains possible after revocation.
    # Approval is rechecked before delivery/start, not retroactively on evidence.
    if (not isinstance(value, dict) or set(value) != {'script_id', 'script_version', 'sha256', 'parameters'}
            or value['script_id'] != DEMO.script_id or type(value['script_version']) is not int
            or value['script_version'] != DEMO.version or value['sha256'] != DEMO.sha256
            or not isinstance(value['parameters'], dict) or value['parameters']):
        raise ValueError('invalid_script_reference')
    if (not isinstance(output, dict) or set(output) != {'schema_version', 'action_id', 'action_version',
            'success', 'script_id', 'script_version', 'sha256', 'runtime', 'diagnostic'}
            or type(output['schema_version']) is not int or output['schema_version'] != 1
            or output['action_id'] != 'run_approved_script' or type(output['action_version']) is not int
            or output['action_version'] != 1 or output['success'] is not True
            or output['script_id'] != value['script_id'] or type(output['script_version']) is not int
            or output['script_version'] != value['script_version'] or output['sha256'] != value['sha256']
            or output['runtime'] != DEMO.runtime):
        raise ValueError('invalid_script_confirmation')
    diagnostic = output['diagnostic']
    if (not isinstance(diagnostic, dict) or set(diagnostic) != {'utc', 'powershell_version'}
            or not isinstance(diagnostic['utc'], str) or len(diagnostic['utc']) > 40
            or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{7}Z', diagnostic['utc'])
            or not isinstance(diagnostic['powershell_version'], str)
            or not re.fullmatch(r'5\.1(?:\.\d{1,10}){0,2}', diagnostic['powershell_version'])):
        raise ValueError('invalid_script_diagnostic')
    from datetime import datetime
    try:
        datetime.fromisoformat(diagnostic['utc'])
    except ValueError:
        raise ValueError('invalid_script_diagnostic') from None


class ApprovedScriptCatalogView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if (not request.user.is_active or not is_nightowl_technical_user(request.user)
                or not request.user.has_perm('agents.view_agentmachine')):
            return Response({'error': 'script_catalog_forbidden'}, status=403)
        return Response({'scripts': [{**asdict(script),
            'revoked': f'{script.script_id}:{script.version}' in getattr(settings, 'NIGHTOWL_REVOKED_SCRIPT_VERSIONS', ()),
            'parameter_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False}}
            for script in CATALOG.values()]})
