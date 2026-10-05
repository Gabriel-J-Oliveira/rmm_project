"""Validate public installer bytes against the versioned, locally reviewed source."""

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import requests
from django.conf import settings


SOURCE = Path('NightOwl.Agent.Windows/scripts/Install-NightOwlAgentDotNet.ps1')
REQUIRED_PARAMETERS = ('ServerUrl', 'InstallAsService', 'RunCheck', 'NoGui', 'NonInteractive')
MAX_INSTALLER_BYTES = 512 * 1024


class InstallerContractFailure(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def canonical_installer():
    try:
        content = (Path(settings.BASE_DIR) / SOURCE).read_bytes()
        text = content.decode('utf-8-sig')
    except (OSError, UnicodeError):
        raise InstallerContractFailure('INSTALLER_CONTRACT_MISMATCH') from None
    # This is a pinned source, not a parser for arbitrary PowerShell from the network.
    if not text.lstrip().startswith('param(') or not 0 < len(content) <= MAX_INSTALLER_BYTES:
        raise InstallerContractFailure('INSTALLER_CONTRACT_MISMATCH')
    for name in REQUIRED_PARAMETERS:
        if not re.search(r'\[(?:string|switch)\]\$' + name + r'\b', text, re.IGNORECASE):
            raise InstallerContractFailure('INSTALLER_CONTRACT_MISMATCH')
    return content, hashlib.sha256(content).hexdigest()


def validate_published_installer():
    content, expected_hash = canonical_installer()
    url = str(settings.NIGHTOWL_AGENT_INSTALLER_URL)
    base = urlsplit(str(settings.NIGHTOWL_AGENT_PUBLIC_SERVER_URL))
    parsed = urlsplit(url)
    for item in (base, parsed):
        if (item.scheme != 'https' or not item.hostname or item.username or item.password or
                item.query or item.fragment or any(c in item.geturl() for c in "'\"`\r\n ")):
            raise InstallerContractFailure('INSTALLER_CONTRACT_MISMATCH')
    if (parsed.netloc != base.netloc or
            not parsed.path.endswith('/Install-NightOwlAgentDotNet.ps1')):
        raise InstallerContractFailure('INSTALLER_CONTRACT_MISMATCH')
    try:
        # No redirects, ambient proxy, cookies or credential forwarding.
        with requests.Session() as session:
            session.trust_env = False
            with session.get(url, timeout=(5, 10), allow_redirects=False, stream=True,
                             verify=True) as response:
                if response.status_code != 200:
                    raise InstallerContractFailure('INSTALLER_DOWNLOAD_FAILED')
                downloaded = bytearray()
                for chunk in response.iter_content(16384):
                    downloaded.extend(chunk)
                    if len(downloaded) > MAX_INSTALLER_BYTES:
                        raise InstallerContractFailure('INSTALLER_CONTRACT_MISMATCH')
            actual_hash = hashlib.sha256(downloaded).hexdigest()
            if actual_hash != expected_hash or bytes(downloaded) != content:
                raise InstallerContractFailure('INSTALLER_CONTRACT_MISMATCH')
            checksum_url = url.rsplit('/', 1)[0] + '/checksums.json'
            with session.get(checksum_url, timeout=(5, 10), allow_redirects=False,
                             stream=True, verify=True) as response:
                if response.status_code != 200:
                    raise InstallerContractFailure('INSTALLER_DOWNLOAD_FAILED')
                metadata = bytearray()
                for chunk in response.iter_content(16384):
                    metadata.extend(chunk)
                    if len(metadata) > MAX_INSTALLER_BYTES:
                        raise InstallerContractFailure('INSTALLER_CONTRACT_MISMATCH')
            try:
                checksums = json.loads(metadata.decode('utf-8-sig'))
                name = SOURCE.name
                entries = [entry for entry in checksums['files'] if entry['name'] == name]
                if (checksums[name] != actual_hash or len(entries) != 1 or
                        entries[0]['sha256'] != actual_hash or entries[0]['size'] != len(content)):
                    raise ValueError
            except (KeyError, TypeError, ValueError, UnicodeError):
                raise InstallerContractFailure('INSTALLER_CONTRACT_MISMATCH') from None
    except InstallerContractFailure:
        raise
    except Exception:
        raise InstallerContractFailure('INSTALLER_DOWNLOAD_FAILED') from None
    return {'installer_sha256': actual_hash, 'installer_contract_valid': True,
            'installer_source': SOURCE.as_posix()}
