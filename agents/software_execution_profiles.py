"""Strict, non-executing contracts for per-installer software operations."""
from __future__ import annotations

import hashlib
import json
import re
import uuid


class ExecutionProfileError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


_VERSION_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9.+_ -]{0,119}$')
_MACRO_PATH_RE = re.compile(r'^%(?:ProgramFiles|ProgramFiles\(x86\)|ProgramData|LOCALAPPDATA)%\\[^\x00\r\n]+$')
_REGISTRY_KEY_RE = re.compile(
    r'^SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\[^\\]{1,128}$', re.IGNORECASE,
)
_EXE_REGISTRY_KEY_RE = re.compile(r'^SOFTWARE(?:\\[A-Za-z0-9 _().-]{1,128}){1,12}$', re.IGNORECASE)
_SERVICE_NAME_RE = re.compile(r'^[A-Za-z0-9_.-]{1,256}$')
_REGISTRY_VALUE_RE = re.compile(r'^[A-Za-z0-9 _().-]{1,128}$')
_EXIT_CODE_MAX = 65535
_OPERATION_TIMEOUT_MIN = 30
_OPERATION_TIMEOUT_MAX = 1800


def _arguments(value, field):
    if not isinstance(value, list) or len(value) > 64:
        raise ExecutionProfileError(field + '_must_be_array')
    result = []
    total = 0
    for item in value:
        if not isinstance(item, str) or len(item) > 512 or any(ord(ch) < 32 or ord(ch) == 127 for ch in item):
            raise ExecutionProfileError(field + '_invalid_argument')
        total += len(item)
        result.append(item)
    if total > 4096:
        raise ExecutionProfileError(field + '_too_long')
    return result


def _guid(value, field):
    if not isinstance(value, str):
        raise ExecutionProfileError(field + '_invalid')
    try:
        parsed = uuid.UUID(value.strip('{}'))
    except (ValueError, AttributeError):
        raise ExecutionProfileError(field + '_invalid') from None
    return str(parsed).upper()


def _exe_exit_codes(value, field):
    if (not isinstance(value, list) or not 1 <= len(value) <= 32
            or any(type(code) is not int or not 0 <= code <= _EXIT_CODE_MAX for code in value)
            or len(value) != len(set(value)) or 0 not in value or 1641 in value):
        raise ExecutionProfileError(field + '_invalid')
    return sorted(value)


def _exe_operation(value, method, field, *, uninstall=False):
    allowed = {'method', 'arguments', 'timeout_seconds', 'accepted_exit_codes', 'reboot_exit_codes'}
    if uninstall:
        allowed.add('executable_path')
    if not isinstance(value, dict) or set(value) != allowed or value.get('method') != method:
        raise ExecutionProfileError(field + '_invalid')
    timeout = value.get('timeout_seconds')
    if type(timeout) is not int or not _OPERATION_TIMEOUT_MIN <= timeout <= _OPERATION_TIMEOUT_MAX:
        raise ExecutionProfileError(field + '_timeout_invalid')
    accepted = _exe_exit_codes(value.get('accepted_exit_codes'), field + '_accepted_exit_codes')
    reboot = value.get('reboot_exit_codes')
    if (not isinstance(reboot, list) or any(type(code) is not int for code in reboot)
            or len(reboot) != len(set(reboot)) or not set(reboot).issubset(accepted)
            or 0 in reboot or 1641 in reboot):
        raise ExecutionProfileError(field + '_reboot_exit_codes_invalid')
    result = {
        'method': method,
        'arguments': _arguments(value.get('arguments'), field + '_arguments'),
        'timeout_seconds': timeout,
        'accepted_exit_codes': accepted,
        'reboot_exit_codes': sorted(reboot),
    }
    if uninstall:
        path = value.get('executable_path')
        safe_path = isinstance(path, str) and _MACRO_PATH_RE.fullmatch(path) and '..' not in path.split('\\')
        # A service process cannot safely resolve an interactive user's LOCALAPPDATA.
        if not safe_path or path.upper().startswith('%LOCALAPPDATA%') or not path.lower().endswith('.exe'):
            raise ExecutionProfileError('uninstall_path_invalid')
        result['executable_path'] = path
    return result


def _exe_detection(value):
    if not isinstance(value, dict):
        raise ExecutionProfileError('detection_profile_required')
    method = value.get('method')
    if method == 'registry_value':
        if set(value) != {'method', 'hive', 'view', 'key', 'value_name'}:
            raise ExecutionProfileError('invalid_detection_profile')
        key, name = value.get('key'), value.get('value_name')
        if (value.get('hive') != 'HKLM' or value.get('view') not in {'registry32', 'registry64'}
                or not isinstance(key, str) or not _EXE_REGISTRY_KEY_RE.fullmatch(key)
                or not isinstance(name, str) or not _REGISTRY_VALUE_RE.fullmatch(name)
                or len('/'.join(('HKLM', value.get('view', ''), key, name))) > 512):
            raise ExecutionProfileError('registry_detection_invalid')
        return {'method': method, 'hive': 'HKLM', 'view': value['view'], 'key': key, 'value_name': name}
    if method == 'file_version':
        if set(value) != {'method', 'path', 'version_field'}:
            raise ExecutionProfileError('invalid_detection_profile')
        path = value.get('path')
        if (not isinstance(path, str) or len(path) > 512 or not _MACRO_PATH_RE.fullmatch(path)
                or '..' in path.split('\\') or not path.lower().endswith('.exe')
                or value.get('version_field') not in {'file_version', 'product_version'}):
            raise ExecutionProfileError('detection_path_invalid')
        return {'method': method, 'path': path, 'version_field': value['version_field']}
    if method == 'windows_service':
        if set(value) != {'method', 'service_name', 'expected_status'}:
            raise ExecutionProfileError('invalid_detection_profile')
        name = value.get('service_name')
        if (not isinstance(name, str) or not _SERVICE_NAME_RE.fullmatch(name)
                or value.get('expected_status') not in {'any', 'running', 'stopped'}):
            raise ExecutionProfileError('service_detection_invalid')
        return {'method': method, 'service_name': name, 'expected_status': value['expected_status']}
    raise ExecutionProfileError('unsupported_detection_method')


def _normalize_exe_v2(value):
    allowed = {'schema_version', 'install', 'update', 'uninstall', 'detection', 'expected_version', 'reboot_behavior'}
    if set(value) != allowed:
        raise ExecutionProfileError('invalid_profile_fields')
    reboot_behavior = value.get('reboot_behavior')
    if reboot_behavior not in {'report_only', 'defer_to_policy', 'require_operator'}:
        raise ExecutionProfileError('invalid_reboot_behavior')
    install = None if value.get('install') is None else _exe_operation(value['install'], 'exe', 'install')
    update = None if value.get('update') is None else _exe_operation(value['update'], 'vendor_exe', 'update')
    uninstall = None if value.get('uninstall') is None else _exe_operation(
        value['uninstall'], 'vendor_exe', 'uninstall', uninstall=True)
    if install is None and update is None and uninstall is None:
        raise ExecutionProfileError('operation_profile_empty')
    detection = _exe_detection(value.get('detection'))
    expected = str(value.get('expected_version') or '').strip()
    needs_version = detection['method'] in {'registry_value', 'file_version'} and (install is not None or update is not None)
    if needs_version and not _VERSION_RE.fullmatch(expected):
        raise ExecutionProfileError('expected_version_required_or_invalid')
    if update is not None and detection['method'] == 'windows_service':
        # Service presence can verify installation, but carries no version evidence
        # and therefore cannot prove an update reached its approved target.
        raise ExecutionProfileError('update_version_unverifiable')
    if expected and not _VERSION_RE.fullmatch(expected):
        raise ExecutionProfileError('expected_version_required_or_invalid')
    return {
        'schema_version': 2, 'install': install, 'update': update, 'uninstall': uninstall,
        'detection': detection, 'expected_version': expected, 'reboot_behavior': reboot_behavior,
    }


def normalize_execution_profile(value, package_type):
    """Validate a complete structured profile; strings are never shell commands."""
    if (isinstance(value, dict) and package_type == 'exe'
            and type(value.get('schema_version')) is int and value.get('schema_version') == 2):
        return _normalize_exe_v2(value)
    allowed_top = {'schema_version', 'install', 'update', 'uninstall', 'detection', 'expected_version', 'reboot_behavior'}
    if not isinstance(value, dict) or set(value) != allowed_top or type(value.get('schema_version')) is not int or value['schema_version'] != 1:
        raise ExecutionProfileError('invalid_profile_fields')
    if package_type not in {'msi', 'exe'}:
        raise ExecutionProfileError('unsupported_package_type')

    expected_version = str(value.get('expected_version') or '').strip()
    if not _VERSION_RE.fullmatch(expected_version):
        raise ExecutionProfileError('expected_version_required_or_invalid')
    reboot_behavior = value.get('reboot_behavior')
    if reboot_behavior not in {'report_only', 'defer_to_policy', 'require_operator'}:
        raise ExecutionProfileError('invalid_reboot_behavior')

    install = value.get('install')
    if not isinstance(install, dict):
        raise ExecutionProfileError('install_profile_required')
    install_allowed = {'method', 'arguments', 'product_code'} if package_type == 'msi' else {'method', 'arguments'}
    if set(install) != install_allowed or install.get('method') != package_type:
        raise ExecutionProfileError('install_method_mismatch')
    install_args = _arguments(install.get('arguments'), 'install_arguments')
    normalized_install = {'method': package_type, 'arguments': install_args}
    if package_type == 'msi':
        # MSI invocation switches remain controlled by the agent. This phase does
        # not allow arbitrary MSI properties or transform paths.
        if install_args:
            raise ExecutionProfileError('msi_arguments_not_supported')
        normalized_install['product_code'] = _guid(install.get('product_code'), 'product_code')

    update = value.get('update')
    normalized_update = None
    if update is not None:
        if not isinstance(update, dict) or set(update) != {'method', 'arguments'}:
            raise ExecutionProfileError('invalid_update_profile')
        expected_update_method = 'msi_upgrade' if package_type == 'msi' else 'vendor_exe'
        if update.get('method') != expected_update_method:
            raise ExecutionProfileError('update_method_mismatch')
        update_args = _arguments(update.get('arguments'), 'update_arguments')
        if package_type == 'msi' and update_args:
            raise ExecutionProfileError('msi_arguments_not_supported')
        normalized_update = {'method': expected_update_method, 'arguments': update_args}

    uninstall = value.get('uninstall')
    normalized_uninstall = None
    if uninstall is not None:
        if not isinstance(uninstall, dict):
            raise ExecutionProfileError('invalid_uninstall_profile')
        if package_type == 'msi':
            if set(uninstall) != {'method', 'product_code'} or uninstall.get('method') != 'msi_product_code':
                raise ExecutionProfileError('uninstall_method_mismatch')
            normalized_uninstall = {
                'method': 'msi_product_code',
                'product_code': _guid(uninstall.get('product_code'), 'uninstall_product_code'),
            }
        else:
            if set(uninstall) != {'method', 'executable_path', 'arguments'} or uninstall.get('method') != 'vendor_exe':
                raise ExecutionProfileError('uninstall_method_mismatch')
            path = uninstall.get('executable_path')
            if not isinstance(path, str) or not _MACRO_PATH_RE.fullmatch(path) or '..' in path.split('\\'):
                raise ExecutionProfileError('uninstall_path_invalid')
            normalized_uninstall = {
                'method': 'vendor_exe', 'executable_path': path,
                'arguments': _arguments(uninstall.get('arguments'), 'uninstall_arguments'),
            }

    detection = value.get('detection')
    if not isinstance(detection, dict):
        raise ExecutionProfileError('detection_profile_required')
    method = detection.get('method')
    if method == 'msi_product_code' and package_type == 'msi':
        if set(detection) != {'method', 'product_code'}:
            raise ExecutionProfileError('invalid_detection_profile')
        detected_code = _guid(detection.get('product_code'), 'detection_product_code')
        if detected_code != normalized_install['product_code']:
            raise ExecutionProfileError('detection_product_code_mismatch')
        normalized_detection = {'method': method, 'product_code': detected_code}
    elif method == 'registry_uninstall' and package_type == 'exe':
        if set(detection) != {'method', 'registry_key', 'display_name'}:
            raise ExecutionProfileError('invalid_detection_profile')
        key = detection.get('registry_key')
        name = str(detection.get('display_name') or '').strip()
        if not isinstance(key, str) or not _REGISTRY_KEY_RE.fullmatch(key) or not name or len(name) > 180:
            raise ExecutionProfileError('registry_detection_invalid')
        normalized_detection = {'method': method, 'registry_key': key, 'display_name': name}
    elif method == 'file_version' and package_type == 'exe':
        if set(detection) != {'method', 'path'}:
            raise ExecutionProfileError('invalid_detection_profile')
        path = detection.get('path')
        if not isinstance(path, str) or not _MACRO_PATH_RE.fullmatch(path) or '..' in path.split('\\'):
            raise ExecutionProfileError('detection_path_invalid')
        normalized_detection = {'method': method, 'path': path}
    else:
        raise ExecutionProfileError('unsupported_detection_method')

    return {
        'schema_version': 1,
        'install': normalized_install,
        'update': normalized_update,
        'uninstall': normalized_uninstall,
        'detection': normalized_detection,
        'expected_version': expected_version,
        'reboot_behavior': reboot_behavior,
    }


def execution_profile_digest(profile):
    canonical = json.dumps(profile, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def package_profile_binding_matches(package):
    return bool(
        package.execution_profile_sha256
        and package.execution_profile_artifact_sha256 == package.sha256
        and package.execution_profile_artifact_revision == package.artifact_revision
        and execution_profile_digest(package.execution_profile) == package.execution_profile_sha256
    )


def operation_is_configured(profile, operation):
    return operation in {'install', 'update', 'uninstall'} and isinstance(profile, dict) and isinstance(profile.get(operation), dict)


def package_operation_is_ready(package, operation):
    """Read-only readiness indicator; never authorizes or dispatches an operation."""
    from .models import SoftwarePackageRecord

    return bool(
        package.artifact_key and package.sha256 and package.size_bytes is not None
        and package.approval_status == SoftwarePackageRecord.STATUS_APPROVED
        and package.approved_at and package.approved_by_id
        and package.execution_profile_status == 'approved'
        and package.execution_profile_approved_at and package.execution_profile_approved_by_id
        and package_profile_binding_matches(package)
        and (package.package_type != 'exe' or package.execution_profile.get('schema_version') == 2)
        and operation_is_configured(package.execution_profile, operation)
    )
