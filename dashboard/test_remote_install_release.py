import base64
import hashlib
import io
import json
import zipfile
from unittest import mock

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from agents.models import AgentRelease, AgentReleaseSigningKey
from dashboard import remote_install_release as contract
from dashboard.installer_contract import InstallerContractFailure


BASE = 'https://nightowl.example.test'


@override_settings(NIGHTOWL_AGENT_PUBLIC_SERVER_URL=BASE)
class ReleaseContractTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        numbers = self.private.public_key().public_numbers()
        def encoded(value):
            return base64.b64encode(value.to_bytes((value.bit_length() + 7) // 8, 'big')).decode()
        self.key = AgentReleaseSigningKey.objects.create(key_id='trusted-test',
            public_key_xml='<RSAKeyValue><Modulus>' + encoded(numbers.n) +
            '</Modulus><Exponent>' + encoded(numbers.e) + '</Exponent></RSAKeyValue>')
        self.version = '0.1.1.0-rc44'
        self.installer = b'param([string]$ServerUrl)\n# frozen historical installer'
        self.metadata = {'version': self.version, 'channel': 'development',
                         'git_commit': 'a' * 40, 'build_id': 'b' * 32}
        self.zip_extra = {}
        self.base = BASE + '/downloads/nightowl-agent/releases/' + self.version + '/'
        self.release = AgentRelease.objects.create(version=self.version, channel='development',
            package_url=self.base + 'NightOwl.Agent.Windows.zip', sha256='a' * 64,
            signature_key_id=self.key.key_id, source_channel='development')
        self.override = override_settings(REMOTE_INSTALL_RELEASE_ID=str(self.release.pk))
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.artifacts = {}
        self.rebuild()

    def rebuild(self):
        package = io.BytesIO()
        with zipfile.ZipFile(package, 'w') as archive:
            archive.writestr(contract.INSTALLER, self.installer)
            archive.writestr('agent.version.json', json.dumps(self.metadata))
            for name, content in self.zip_extra.items():
                archive.writestr(name, content)
        blob = package.getvalue()
        self.manifest = {**self.metadata, 'key_id': self.key.key_id, 'legacy_unsigned': False,
            'package': {'sha256': hashlib.sha256(blob).hexdigest(), 'size': len(blob)},
            'required_zip_entries': [contract.INSTALLER, 'agent.version.json']}
        self.artifacts['NightOwl.Agent.Windows.zip'] = blob
        sha = hashlib.sha256(self.installer).hexdigest()
        self.artifacts[contract.INSTALLER] = self.installer
        self.artifacts['checksums.json'] = json.dumps({contract.INSTALLER: sha, 'files': [
            {'name': contract.INSTALLER, 'sha256': sha, 'size': len(self.installer)}]}).encode()
        self.sign()

    def sign(self, private=None):
        manifest = json.dumps(self.manifest).encode()
        signature = base64.b64encode((private or self.private).sign(manifest,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32), hashes.SHA256()))
        self.artifacts['release-manifest.json'] = manifest
        self.artifacts['release-manifest.sig'] = signature
        AgentRelease.objects.filter(pk=self.release.pk).update(status='paused', rollout_paused=True,
            rollout_percentage=0, legacy_unsigned=False, signature_valid=True,
            sha256=self.manifest['package']['sha256'], size=self.manifest['package']['size'],
            checksum_url=self.base + 'checksums.json', manifest_url=self.base + 'release-manifest.json',
            signature_url=self.base + 'release-manifest.sig',
            manifest_sha256=hashlib.sha256(manifest).hexdigest(),
            signature_sha256=hashlib.sha256(signature).hexdigest())

    def validate(self):
        session = mock.MagicMock()
        session.__enter__.return_value = session
        def response(url, **kwargs):
            self.assertEqual(kwargs, {'timeout': (5, 10), 'stream': True,
                                     'allow_redirects': False, 'verify': True})
            self.assertTrue(url.startswith(self.base))
            result = mock.MagicMock()
            result.__enter__.return_value = result
            result.status_code = 200
            result.iter_content.return_value = [self.artifacts[url.rsplit('/', 1)[1]]]
            return result
        session.get.side_effect = response
        with mock.patch.object(contract.requests, 'Session', return_value=session):
            result = contract.validate_install_release()
        self.assertFalse(session.trust_env)
        return result

    def fails(self):
        with self.assertRaisesMessage(InstallerContractFailure, 'INSTALL_RELEASE_INVALID'):
            self.validate()

    def test_historical_installer_independent_of_canonical_and_paused_rollout(self):
        with mock.patch('dashboard.installer_contract.canonical_installer', side_effect=ValueError):
            result = self.validate()
        self.assertEqual(result['installer_sha256'], hashlib.sha256(self.installer).hexdigest())
        self.assertEqual(result['release_id'], str(self.release.pk))
        self.assertEqual(result['trusted_public_keys']['keys'][0]['public_key_xml'], self.key.public_key_xml)
        self.assertTrue(result['release_validated'])

    def test_explicit_installer_digest_when_present(self):
        self.manifest['installer'] = {'sha256': hashlib.sha256(self.installer).hexdigest()}
        self.sign()
        self.validate()
        self.manifest['installer']['sha256'] = '0' * 64
        self.sign()
        self.fails()

    def test_manifest_mismatch(self):
        for field in ('version', 'channel', 'git_commit', 'build_id', 'key_id'):
            with self.subTest(field=field):
                original = self.manifest[field]
                self.manifest[field] = 'invalid'
                self.sign()
                self.fails()
                self.manifest[field] = original
        self.sign()

    def test_redirect_never_followed(self):
        session = mock.MagicMock()
        session.__enter__.return_value = session
        response = session.get.return_value.__enter__.return_value
        response.status_code = 302
        with mock.patch.object(contract.requests, 'Session', return_value=session):
            with self.assertRaises(InstallerContractFailure):
                contract.validate_install_release()
        self.assertEqual(session.get.call_count, 1)
        self.assertFalse(session.get.call_args.kwargs['allow_redirects'])

    def test_selection_change_during_download_fails(self):
        selected = contract.selected_install_release
        count = 0
        def changing():
            nonlocal count
            count += 1
            if count == 2:
                raise ValueError
            return selected()
        with mock.patch.object(contract, 'selected_install_release', side_effect=changing):
            self.fails()

    def test_display_read_only_and_independent_of_canonical(self):
        with mock.patch('dashboard.installer_contract.canonical_installer', side_effect=ValueError):
            self.assertEqual(contract.installation_release_display(),
                             {'version': self.version, 'channel': 'development'})


    def test_external_installer_tamper(self):
        self.artifacts[contract.INSTALLER] += b'tamper'
        self.fails()

    def test_package_tamper(self):
        self.artifacts['NightOwl.Agent.Windows.zip'] += b'tamper'
        self.fails()

    def test_checksum_tamper(self):
        self.artifacts['checksums.json'] = b'{}'
        self.fails()

    def test_signature_untrusted_mathematically_valid(self):
        self.sign(rsa.generate_private_key(public_exponent=65537, key_size=2048))
        self.fails()

    def test_unknown_key(self):
        AgentReleaseSigningKey.objects.all().delete()
        self.fails()

    def test_revoked_key(self):
        AgentReleaseSigningKey.objects.filter(pk=self.key.pk).update(status='revoked')
        self.fails()

    def test_expired_key(self):
        AgentReleaseSigningKey.objects.filter(pk=self.key.pk).update(valid_until=timezone.now())
        self.fails()

    def test_revoked_release(self):
        AgentRelease.objects.filter(pk=self.release.pk).update(revoked=True)
        self.fails()

    def test_version_metadata_mismatch(self):
        self.metadata['version'] = 'wrong'
        self.rebuild()
        self.fails()

    def test_zip_member_traversal_and_ambiguity(self):
        for name in ('../escape.ps1', 'C:/escape.ps1', 'sub\\..\\escape.ps1', contract.INSTALLER.upper()):
            with self.subTest(name=name):
                self.zip_extra = {name: b'bad'}
                self.rebuild()
                self.fails()

    def test_cross_origin_package_rejected_without_network(self):
        AgentRelease.objects.filter(pk=self.release.pk).update(package_url='https://evil.test/package.zip')
        with mock.patch.object(contract.requests, 'Session') as session:
            self.fails()
        session.assert_not_called()

    def test_unset_selection_fails_without_network(self):
        with override_settings(REMOTE_INSTALL_RELEASE_ID=''), mock.patch.object(contract.requests, 'Session') as session:
            self.fails()
        session.assert_not_called()

    def test_browser_overrides_rejected_before_any_probe_or_job(self):
        user = get_user_model().objects.create_user('release-admin', is_staff=True)
        self.client.force_login(user)
        for field in ('release_id', 'version', 'package_url', 'installer_url', 'package_sha', 'installer_sha', 'channel'):
            with self.subTest(field=field), mock.patch('dashboard.ad_install_views.run_remote_install_preflight') as probe, \
                    mock.patch('dashboard.ad_install_views.create_remote_install_job') as create:
                payload = {'fqdn': 'lab.control.local', 'username': 'synthetic', 'password': 'synthetic', field: 'override'}
                response = self.client.post(reverse('agent-install-ad-preflight'), json.dumps(payload), content_type='application/json')
                self.assertEqual(response.status_code, 400)
                response = self.client.post(reverse('agent-install-ad-install'),
                    {**payload, 'csrfmiddlewaretoken': 'synthetic'}, secure=True)
                self.assertEqual(response.status_code, 400)
                probe.assert_not_called()
                create.assert_not_called()

    def test_safe_errors_never_expose_credentials(self):
        with mock.patch.object(contract.requests, 'Session', side_effect=ValueError('SUPER_SECRET_TEST_PASSWORD_91827')):
            with self.assertRaises(InstallerContractFailure) as caught:
                contract.validate_install_release()
        self.assertEqual(str(caught.exception), 'INSTALL_RELEASE_INVALID')
