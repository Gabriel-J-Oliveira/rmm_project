"""Private, bounded, streaming storage for software installer artifacts."""
from __future__ import annotations

import hashlib
import os
import stat
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


MAX_INITIAL_UPLOAD_BYTES = 1024 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024
MAX_PE_HEADER_OFFSET = 1024 * 1024
MSI_REQUIRED_DIRECTORY_ENTRIES = tuple(
    name.encode('utf-16le') for name in ('_Tables', '_Columns', '_StringPool', '_StringData')
)


class PackageUploadError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class StagedPackage:
    path: Path
    package_type: str
    original_filename: str
    size_bytes: int
    sha256: str
    detected_format: str


def private_root() -> Path:
    if not settings.DEBUG and not getattr(settings, 'NIGHTOWL_SOFTWARE_PACKAGE_STORAGE_CONFIGURED', False):
        raise ImproperlyConfigured('NIGHTOWL_SOFTWARE_PACKAGE_STORAGE_ROOT is required outside DEBUG.')
    root = Path(settings.NIGHTOWL_SOFTWARE_PACKAGE_STORAGE_ROOT).expanduser().resolve()
    public_roots = [Path(settings.MEDIA_ROOT).resolve(), Path(settings.STATIC_ROOT).resolve()]
    public_roots.extend(Path(item).resolve() for item in getattr(settings, 'STATICFILES_DIRS', []))
    if any(root == public or public in root.parents for public in public_roots):
        raise ImproperlyConfigured('Software package storage must be outside public file roots.')
    root.mkdir(mode=0o750, parents=True, exist_ok=True)
    if os.name != 'nt' and stat.S_IMODE(root.stat().st_mode) & 0o007:
        raise ImproperlyConfigured('Software package storage must not be accessible to other users.')
    return root


def configured_upload_limit() -> int:
    limit = int(settings.NIGHTOWL_SOFTWARE_PACKAGE_MAX_UPLOAD_BYTES)
    if limit < 1 or limit > MAX_INITIAL_UPLOAD_BYTES:
        raise ImproperlyConfigured('NIGHTOWL_SOFTWARE_PACKAGE_MAX_UPLOAD_BYTES must be between 1 byte and 1 GiB.')
    return limit


def _safe_original_filename(name: str) -> str:
    leaf = str(name or '').replace('\\', '/').rsplit('/', 1)[-1]
    leaf = ''.join(char for char in leaf if char >= ' ' and char not in '\x7f')
    leaf = leaf.strip()
    if not leaf or len(leaf) > 255:
        raise PackageUploadError('invalid_filename')
    return leaf


def _detect_package_type(header: bytes, filename: str, msi_entries_found: set[bytes]) -> tuple[str, str]:
    extension = Path(filename).suffix.casefold()
    if extension not in {'.msi', '.exe'}:
        raise PackageUploadError('unsupported_extension')
    if (len(header) >= 512 and header.startswith(bytes.fromhex('D0CF11E0A1B11AE1'))
            and header[28:30] == b'\xfe\xff' and int.from_bytes(header[30:32], 'little') in {9, 12}
            and int.from_bytes(header[32:34], 'little') == 6):
        if not set(MSI_REQUIRED_DIRECTORY_ENTRIES).issubset(msi_entries_found):
            raise PackageUploadError('invalid_msi_compound_file')
        detected, description = 'msi', 'ole_msi_directory'
    elif len(header) >= 64 and header[:2] == b'MZ':
        pe_offset = int.from_bytes(header[0x3C:0x40], 'little')
        if (pe_offset < 64 or pe_offset > MAX_PE_HEADER_OFFSET or len(header) < pe_offset + 24
                or header[pe_offset:pe_offset + 4] != b'PE\0\0'
                or int.from_bytes(header[pe_offset + 6:pe_offset + 8], 'little') == 0
                or not int.from_bytes(header[pe_offset + 22:pe_offset + 24], 'little') & 0x0002):
            raise PackageUploadError('invalid_executable_header')
        detected, description = 'exe', 'pe_coff'
    else:
        raise PackageUploadError('unrecognized_binary_format')
    if extension != '.' + detected:
        raise PackageUploadError('extension_content_mismatch')
    return detected, description


def stage_upload(uploaded_file) -> StagedPackage:
    limit = configured_upload_limit()
    filename = _safe_original_filename(getattr(uploaded_file, 'name', ''))
    root = private_root()
    staging = root / '.staging'
    staging.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != 'nt' and stat.S_IMODE(staging.stat().st_mode) & 0o077:
        raise ImproperlyConfigured('Package staging must be accessible only to its owner.')
    fd, raw_path = tempfile.mkstemp(prefix='upload-', suffix='.part', dir=staging)
    stage_path = Path(raw_path)
    size = 0
    digest = hashlib.sha256()
    header = bytearray()
    msi_entries_found = set()
    scan_tail = b''
    try:
        with os.fdopen(fd, 'wb') as output:
            for chunk in uploaded_file.chunks(UPLOAD_CHUNK_BYTES):
                if not chunk:
                    continue
                size += len(chunk)
                if size > limit:
                    raise PackageUploadError('upload_too_large')
                header_limit = MAX_PE_HEADER_OFFSET + 24
                if len(header) < header_limit:
                    header.extend(chunk[:header_limit - len(header)])
                searchable = scan_tail + chunk
                for marker in MSI_REQUIRED_DIRECTORY_ENTRIES:
                    if marker in searchable:
                        msi_entries_found.add(marker)
                scan_tail = searchable[-(max(map(len, MSI_REQUIRED_DIRECTORY_ENTRIES)) - 1):]
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if size == 0:
            raise PackageUploadError('empty_file')
        package_type, detected_format = _detect_package_type(bytes(header), filename, msi_entries_found)
        return StagedPackage(stage_path, package_type, filename, size, digest.hexdigest(), detected_format)
    except Exception:
        try:
            stage_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def promote_staged(staged: StagedPackage, package_id) -> str:
    root = private_root()
    relative = Path(str(package_id)) / f'{uuid.uuid4().hex}.{staged.package_type}'
    destination = root / relative
    destination.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    # The relative key is generated exclusively here; resolve/check before the atomic rename.
    resolved_parent = destination.parent.resolve()
    if root != resolved_parent and root not in resolved_parent.parents:
        raise ImproperlyConfigured('Invalid private package destination.')
    os.replace(staged.path, destination)
    return relative.as_posix()


def unlink_private(key: str) -> bool:
    if not key:
        return True
    root = private_root()
    candidate = root / key
    resolved = candidate.resolve(strict=False)
    if root != resolved and root not in resolved.parents:
        raise ImproperlyConfigured('Stored package key escapes private storage.')
    try:
        if candidate.is_symlink():
            raise ImproperlyConfigured('Refusing to follow a package storage symlink.')
        candidate.unlink(missing_ok=True)
        parent = candidate.parent
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
        return True
    except FileNotFoundError:
        return True


def open_private(key: str):
    if not key:
        return None
    root = private_root()
    candidate = root / key
    resolved = candidate.resolve(strict=True)
    if root != resolved and root not in resolved.parents:
        raise ImproperlyConfigured('Stored package key escapes private storage.')
    if candidate.is_symlink() or not resolved.is_file():
        return None
    return resolved.open('rb')
