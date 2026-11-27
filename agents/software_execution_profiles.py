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


def normalize_execution_profile(value, package_type):
    """Validate a complete structured profile; strings are never shell commands."""
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
    if operation == 'install':
        return isinstance(profile, dict) and isinstance(profile.get('install'), dict)
    if operation == 'update':
        return isinstance(profile, dict) and isinstance(profile.get('update'), dict)
    if operation == 'uninstall':
        return isinstance(profile, dict) and isinstance(profile.get('uninstall'), dict)
    return False


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
        and operation_is_configured(package.execution_profile, operation)
    )
