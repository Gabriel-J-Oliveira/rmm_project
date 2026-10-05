import io
import json
import tempfile
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

from dashboard import installer_contract as contract


@override_settings(NIGHTOWL_AGENT_PUBLIC_SERVER_URL='https://nightowl.example.test',
    NIGHTOWL_AGENT_INSTALLER_URL='https://nightowl.example.test/downloads/nightowl-agent/Install-NightOwlAgentDotNet.ps1')
class InstallerContractTests(SimpleTestCase):
    def response(self, content=None, status=200):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.status_code = status
        response.iter_content.return_value = [contract.canonical_installer()[0] if content is None else content]
        return response

    def validate(self, response, checksums=None):
        session = mock.MagicMock()
        session.__enter__.return_value = session
        content, sha = contract.canonical_installer()
        checksum_response = self.response(json.dumps(checksums or {
            contract.SOURCE.name: sha, 'files': [{'name': contract.SOURCE.name,
                                                'sha256': sha, 'size': len(content)}]}).encode())
        session.get.side_effect = [response, checksum_response]
        with mock.patch.object(contract.requests, 'Session', return_value=session):
            result = contract.validate_published_installer()
        self.assertEqual(session.get.call_count, 2)
        for call in session.get.call_args_list:
            self.assertEqual(call.kwargs, {'timeout': (5, 10), 'allow_redirects': False,
                                          'stream': True, 'verify': True})
        self.assertFalse(session.trust_env)
        return result

    def test_public_content_matches_pinned_source(self):
        result = self.validate(self.response())
        self.assertTrue(result['installer_contract_valid'])
        self.assertEqual(result['installer_sha256'], contract.canonical_installer()[1])

    def test_missing_parameter_drift_malformed_and_oversize_fail_closed(self):
        original = contract.canonical_installer()[0]
        for content in (original.replace(b'[switch]$NonInteractive', b'[switch]$Wrong'),
                        original + b'\n# drift', b'<html>not a script</html>',
                        b'x' * (contract.MAX_INSTALLER_BYTES + 1)):
            with self.subTest(size=len(content)), self.assertRaisesMessage(
                    contract.InstallerContractFailure, 'INSTALLER_CONTRACT_MISMATCH'):
                self.validate(self.response(content))

    def test_http_errors_redirects_and_timeout_are_sanitized(self):
        secret = 'SUPER_SECRET_ENROLLMENT_TOKEN_73192'
        for status in (404, 301, 302, 303, 307, 308, 500):
            with self.subTest(status=status), self.assertRaisesMessage(
                    contract.InstallerContractFailure, 'INSTALLER_DOWNLOAD_FAILED'):
                self.validate(self.response(status=status))
        with mock.patch.object(contract.requests, 'Session', side_effect=TimeoutError(secret)):
            with self.assertRaises(contract.InstallerContractFailure) as caught:
                contract.validate_published_installer()
        self.assertNotIn(secret, str(caught.exception))

    def test_wrong_origin_or_injection_never_connects(self):
        for url in ('http://nightowl.example.test/Install-NightOwlAgentDotNet.ps1',
                    'https://evil.test/Install-NightOwlAgentDotNet.ps1',
                    'https://nightowl.example.test/"/Install-NightOwlAgentDotNet.ps1'):
            with override_settings(NIGHTOWL_AGENT_INSTALLER_URL=url), \
                    mock.patch.object(contract.requests, 'Session') as session:
                with self.assertRaises(contract.InstallerContractFailure):
                    contract.validate_published_installer()
                session.assert_not_called()

    def test_public_checksum_mismatch_fails_closed(self):
        with self.assertRaisesMessage(contract.InstallerContractFailure, 'INSTALLER_CONTRACT_MISMATCH'):
            self.validate(self.response(), checksums={contract.SOURCE.name: 'wrong', 'files': []})

    def test_publisher_only_changes_installer_entry_and_preserves_package(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'agent' / 'windows'
            root.mkdir(parents=True)
            name = 'Install-NightOwlAgentDotNet.ps1'
            old = {'NightOwl.Agent.Windows.zip': 'unchanged', name: 'old',
                   'files': [{'name': name, 'sha256': 'old', 'size': 3},
                             {'name': 'NightOwl.Agent.Windows.zip', 'sha256': 'unchanged', 'size': 123}]}
            (root / name).write_bytes(b'old')
            (root / 'checksums.json').write_text(json.dumps(old))
            (root / 'version.json').write_bytes(b'unchanged-version')
            call_command('publish_remote_installer', destination=str(root), stdout=io.StringIO())
            self.assertEqual((root / name).read_bytes(), b'old')
            call_command('publish_remote_installer', destination=str(root), apply=True, stdout=io.StringIO())
            content, sha = contract.canonical_installer()
            self.assertEqual((root / name).read_bytes(), content)
            new = json.loads((root / 'checksums.json').read_text())
            self.assertEqual(new['files'][1], old['files'][1])
            self.assertEqual(new['NightOwl.Agent.Windows.zip'], 'unchanged')
            self.assertEqual(new[name], sha)
            self.assertEqual((root / 'version.json').read_bytes(), b'unchanged-version')
