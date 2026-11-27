import hashlib
import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest import skipUnless
from uuid import UUID

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from agents.models import AgentJob, AgentMachine, AuditEvent, InventorySnapshot, SoftwarePackageRecord
from agents.software_operation_contract import SoftwareOperationBlocked, prepare_software_operation_snapshot
from agents.software_execution_profiles import package_profile_binding_matches


def msi_bytes(size=512):
    data = bytearray(max(size, 512))
    data[:8] = bytes.fromhex('D0CF11E0A1B11AE1')
    data[26:28] = (3).to_bytes(2, 'little')
    data[28:30] = b'\xfe\xff'
    data[30:32] = (9).to_bytes(2, 'little')
    data[32:34] = (6).to_bytes(2, 'little')
    offset = 64
    for name in ('_Tables', '_Columns', '_StringPool', '_StringData'):
        encoded = name.encode('utf-16le')
        data[offset:offset + len(encoded)] = encoded
        offset += len(encoded) + 1
    return bytes(data)


def exe_bytes(size=512):
    data = bytearray(max(size, 512))
    data[:2] = b'MZ'
    data[0x3C:0x40] = (128).to_bytes(4, 'little')
    data[128:132] = b'PE\0\0'
    data[134:136] = (1).to_bytes(2, 'little')
    data[150:152] = (2).to_bytes(2, 'little')
    return bytes(data)


def valid_msi_profile():
    product_code = '11111111-1111-4111-8111-111111111111'
    return {
        'schema_version': 1,
        'install': {'method': 'msi', 'arguments': [], 'product_code': product_code},
        'update': None,
        'uninstall': {'method': 'msi_product_code', 'product_code': product_code},
        'detection': {'method': 'msi_product_code', 'product_code': product_code},
        'expected_version': '1.2.3',
        'reboot_behavior': 'defer_to_policy',
    }


class SoftwarePackageApiTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nightowl-private-packages-')
        self.settings_override = override_settings(
            NIGHTOWL_SOFTWARE_PACKAGE_STORAGE_ROOT=Path(self.temp.name) / 'private-store',
            NIGHTOWL_SOFTWARE_PACKAGE_STORAGE_CONFIGURED=True,
            NIGHTOWL_SOFTWARE_PACKAGE_MAX_UPLOAD_BYTES=1024 * 1024,
            MEDIA_ROOT=Path(self.temp.name) / 'public-media',
            STATIC_ROOT=Path(self.temp.name) / 'public-static',
        )
        self.settings_override.enable()
        self.operator = get_user_model().objects.create_superuser('software-package-admin')
        self.client.force_login(self.operator)

    def tearDown(self):
        self.settings_override.disable()
        self.temp.cleanup()

    def upload(self, filename='setup.msi', content=None, friendly_name='Installer', client=None):
        client = client or self.client
        content = content if content is not None else msi_bytes()
        return client.post(reverse('software-package-create'), {
            'friendly_name': friendly_name,
            'version': '1.2.3',
            'description': 'Pilot installer',
            'file': SimpleUploadedFile(filename, content, content_type='application/octet-stream'),
        })

    def create_package(self, **kwargs):
        response = self.upload(**kwargs)
        self.assertEqual(response.status_code, 201, response.content)
        return SoftwarePackageRecord.objects.get(pk=response.json()['item']['id']), response

    def configure_msi_profile(self, package, revision=None, *, client=None, profile=None):
        return (client or self.client).post(reverse('software-package-profile', kwargs={'pk': package.pk}),
            data=json.dumps({'revision': revision if revision is not None else package.revision,
                             'reason': 'Perfil documentado pelo fornecedor',
                             'execution_profile': profile or valid_msi_profile()}),
            content_type='application/json')

    def approve_package(self, package, client=None):
        return (client or self.client).post(reverse('software-package-approve', kwargs={'pk': package.pk}),
            data=json.dumps({'revision': package.revision, 'reason': 'Revisão de artefato'}),
            content_type='application/json')

    def approve_profile(self, package, client=None):
        return (client or self.client).post(reverse('software-package-profile-approve', kwargs={'pk': package.pk}),
            data=json.dumps({'revision': package.revision, 'reason': 'Revisão do perfil de execução'}),
            content_type='application/json')

    def test_create_msi_and_exe_streams_hashes_to_private_storage_and_downloads_authenticated(self):
        msi, response = self.create_package()
        self.assertEqual(msi.package_type, 'msi')
        self.assertEqual(msi.sha256, hashlib.sha256(msi_bytes()).hexdigest())
        self.assertEqual(msi.size_bytes, 512)
        self.assertEqual(response.json()['item']['approval_status'], 'draft')
        self.assertEqual(response.json()['item']['signature_status'], 'unknown')
        self.assertNotIn('private-store', response.json()['item']['download_url'])
        self.assertNotIn('artifact_key', response.json()['item'])
        private_path = Path(self.temp.name) / 'private-store' / msi.artifact_key
        self.assertTrue(private_path.is_file())
        self.assertNotIn('setup.msi', private_path.name)
        self.assertEqual(self.client.get('/media/' + msi.artifact_key).status_code, 404)

        exe_response = self.upload(filename='setup.exe', content=exe_bytes(), friendly_name='Client EXE')
        self.assertEqual(exe_response.status_code, 201, exe_response.content)
        exe = SoftwarePackageRecord.objects.get(pk=exe_response.json()['item']['id'])
        self.assertEqual(exe.package_type, 'exe')

        download = self.client.get(reverse('software-package-download', kwargs={'pk': msi.pk}))
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download['Content-Type'], 'application/octet-stream')
        self.assertEqual(download['X-Content-Type-Options'], 'nosniff')
        self.assertIn('attachment', download['Content-Disposition'])
        self.assertEqual(b''.join(download.streaming_content), msi_bytes())
        download.close()

    def test_requires_file_and_rejects_fake_or_mismatched_binary_type(self):
        no_file = self.client.post(reverse('software-package-create'), {'friendly_name': 'No binary'})
        self.assertEqual(no_file.status_code, 400)
        self.assertEqual(no_file.json()['error'], 'file_required')
        fake = self.upload(filename='fake.exe', content=b'MZ but not a PE executable')
        self.assertEqual(fake.status_code, 400)
        self.assertEqual(fake.json()['error'], 'unrecognized_binary_format')
        generic_compound = bytearray(msi_bytes())
        generic_compound[64:160] = bytes(96)
        invalid_msi = self.upload(filename='not-an-msi.msi', content=bytes(generic_compound))
        self.assertEqual(invalid_msi.status_code, 400)
        self.assertEqual(invalid_msi.json()['error'], 'invalid_msi_compound_file')
        mismatch = self.upload(filename='renamed.exe', content=msi_bytes())
        self.assertEqual(mismatch.status_code, 400)
        self.assertEqual(mismatch.json()['error'], 'extension_content_mismatch')
        self.assertEqual(SoftwarePackageRecord.objects.count(), 0)
        self.assertFalse(any((Path(self.temp.name) / 'private-store' / '.staging').glob('*')))

    def test_enforces_configured_upload_limit_without_persisting_partial_record(self):
        response = self.upload(content=msi_bytes(1024 * 1024 + 1))
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()['error'], 'upload_too_large')
        self.assertEqual(SoftwarePackageRecord.objects.count(), 0)
        self.assertEqual(list((Path(self.temp.name) / 'private-store' / '.staging').glob('*')), [])

    def test_csrf_is_required_for_multipart_mutation(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.operator)
        client.get(reverse('software-inventory'))
        upload = {'friendly_name': 'CSRF installer', 'file': SimpleUploadedFile('csrf.msi', msi_bytes())}
        denied = client.post(reverse('software-package-create'), upload)
        self.assertEqual(denied.status_code, 403)
        token = client.cookies['csrftoken'].value
        allowed = client.post(reverse('software-package-create'), {
            'friendly_name': 'CSRF installer', 'file': SimpleUploadedFile('csrf.msi', msi_bytes()),
        }, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(allowed.status_code, 201, allowed.content)

    def test_technical_user_without_package_permissions_cannot_mutate(self):
        user = get_user_model().objects.create_user('software-uploader')
        with override_settings(NIGHTOWL_TECHNICAL_USERNAMES={'software-uploader'}):
            client = Client()
            client.force_login(user)
            self.assertEqual(client.get(reverse('software-packages-data')).status_code, 403)
            package, _ = self.create_package()
            self.assertEqual(client.get(reverse('software-package-detail', kwargs={'pk': package.pk})).status_code, 403)
            user.user_permissions.add(Permission.objects.get(codename='view_softwarepackagerecord'))
            self.assertEqual(client.get(reverse('software-packages-data')).status_code, 200)
            self.assertEqual(client.get(reverse('software-package-detail', kwargs={'pk': package.pk})).status_code, 200)
            denied = self.upload(client=client)
            self.assertEqual(denied.status_code, 403)
            user.user_permissions.add(Permission.objects.get(codename='add_softwarepackagerecord'))
            created = self.upload(client=client)
            self.assertEqual(created.status_code, 201, created.content)
            package_id = created.json()['item']['id']
            self.assertEqual(client.get(reverse('software-package-download', kwargs={'pk': package_id})).status_code, 403)
            user.user_permissions.add(Permission.objects.get(codename='download_softwarepackage'))
            download = client.get(reverse('software-package-download', kwargs={'pk': package_id}))
            self.assertEqual(download.status_code, 200)
            download.close()

    def test_approval_fails_closed_when_storage_points_to_a_public_root(self):
        package, _ = self.create_package()
        with override_settings(NIGHTOWL_SOFTWARE_PACKAGE_STORAGE_ROOT=Path(self.temp.name) / 'public-media'):
            response = self.client.post(reverse('software-package-approve', kwargs={'pk': package.pk}),
                data=json.dumps({'revision': 1, 'reason': 'Attempt approval without private storage'}),
                content_type='application/json')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['error'], 'storage_unconfigured')
        package.refresh_from_db()
        self.assertEqual(package.approval_status, SoftwarePackageRecord.STATUS_DRAFT)
        self.assertIsNone(package.approved_at)

    def test_approval_rejects_artifact_modified_after_upload(self):
        package, _ = self.create_package()
        stored = Path(self.temp.name) / 'private-store' / package.artifact_key
        stored.write_bytes(msi_bytes(1024))
        response = self.client.post(reverse('software-package-approve', kwargs={'pk': package.pk}),
            data=json.dumps({'revision': 1, 'reason': 'Detect altered artifact'}), content_type='application/json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error'], 'artifact_missing_or_integrity_failed')
        package.refresh_from_db()
        self.assertEqual(package.approval_status, SoftwarePackageRecord.STATUS_DRAFT)

    def test_edit_approval_revocation_and_delete_use_distinct_permissions(self):
        package, _ = self.create_package()
        user = get_user_model().objects.create_user('software-lifecycle-operator')
        with override_settings(NIGHTOWL_TECHNICAL_USERNAMES={'software-lifecycle-operator'}):
            client = Client()
            client.force_login(user)
            update_url = reverse('software-package-update', kwargs={'pk': package.pk})
            replace_url = reverse('software-package-replace', kwargs={'pk': package.pk})
            approve_url = reverse('software-package-approve', kwargs={'pk': package.pk})
            revoke_url = reverse('software-package-revoke', kwargs={'pk': package.pk})
            delete_url = reverse('software-package-delete', kwargs={'pk': package.pk})
            self.assertEqual(client.post(update_url, data=json.dumps({'revision': 1, 'friendly_name': 'Denied'}),
                                         content_type='application/json').status_code, 403)
            self.assertEqual(client.post(replace_url, {'revision': 1, 'reason': 'Denied',
                'file': SimpleUploadedFile('replacement.msi', msi_bytes())}).status_code, 403)
            for url in (approve_url, revoke_url, delete_url):
                self.assertEqual(client.post(url, data=json.dumps({'revision': 1, 'reason': 'Denied'}),
                                             content_type='application/json').status_code, 403)

            user.user_permissions.add(Permission.objects.get(codename='change_softwarepackagerecord'))
            updated = client.post(update_url, data=json.dumps({'revision': 1, 'friendly_name': 'Edited'}),
                                  content_type='application/json')
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(updated.json()['item']['revision'], 2)
            replaced = client.post(replace_url, {'revision': 2, 'reason': 'Controlled replacement',
                'file': SimpleUploadedFile('replacement.msi', msi_bytes(1024))})
            self.assertEqual(replaced.status_code, 200, replaced.content)
            self.assertEqual(replaced.json()['item']['revision'], 3)

            user.user_permissions.add(Permission.objects.get(codename='approve_softwarepackage'))
            approved = client.post(approve_url, data=json.dumps({'revision': 3, 'reason': 'Operator approval'}),
                                  content_type='application/json')
            self.assertEqual(approved.status_code, 200, approved.content)
            self.assertEqual(approved.json()['item']['revision'], 4)
            user.user_permissions.add(Permission.objects.get(codename='revoke_softwarepackage'))
            revoked = client.post(revoke_url, data=json.dumps({'revision': 4, 'reason': 'Withdrawn'}),
                                 content_type='application/json')
            self.assertEqual(revoked.status_code, 200, revoked.content)
            self.assertEqual(revoked.json()['item']['revision'], 5)
            user.user_permissions.add(Permission.objects.get(codename='delete_softwarepackagerecord'))
            deleted = client.post(delete_url, data=json.dumps({'revision': 5, 'reason': 'Unused test record'}),
                                 content_type='application/json')
            self.assertEqual(deleted.status_code, 200, deleted.content)

    def test_metadata_update_approval_revoke_and_audit_snapshots(self):
        package, _ = self.create_package()
        update = self.client.post(reverse('software-package-update', kwargs={'pk': package.pk}),
            data=json.dumps({'revision': 1, 'friendly_name': 'Installer Renamed', 'version': '2.0', 'description': 'Reviewed'}),
            content_type='application/json')
        self.assertEqual(update.status_code, 200, update.content)
        self.assertEqual(update.json()['item']['revision'], 2)
        stale = self.client.post(reverse('software-package-update', kwargs={'pk': package.pk}),
            data=json.dumps({'revision': 1, 'friendly_name': 'Stale'}), content_type='application/json')
        self.assertEqual(stale.status_code, 409)

        approve = self.client.post(reverse('software-package-approve', kwargs={'pk': package.pk}),
            data=json.dumps({'revision': 2, 'reason': 'Assinatura e origem revisadas pelo operador'}),
            content_type='application/json')
        self.assertEqual(approve.status_code, 200, approve.content)
        self.assertEqual(approve.json()['item']['approval_status'], 'approved')
        revoke = self.client.post(reverse('software-package-revoke', kwargs={'pk': package.pk}),
            data=json.dumps({'revision': 3, 'reason': 'Retirado do catálogo'}), content_type='application/json')
        self.assertEqual(revoke.status_code, 200, revoke.content)
        self.assertEqual(revoke.json()['item']['approval_status'], 'revoked')
        events = list(AuditEvent.objects.filter(metadata__software_package_id=str(package.pk)).order_by('created_at'))
        self.assertEqual([event.event_type for event in events], [
            'software_package.created', 'software_package.metadata_updated',
            'software_package.approved', 'software_package.revoked',
        ])
        self.assertEqual(events[1].metadata['before']['friendly_name'], 'Installer')
        self.assertEqual(events[1].metadata['after']['friendly_name'], 'Installer Renamed')
        self.assertEqual(events[2].description, 'Assinatura e origem revisadas pelo operador')

    def test_replacement_changes_hash_removes_old_binary_and_invalidates_approval(self):
        package, _ = self.create_package()
        approve = self.client.post(reverse('software-package-approve', kwargs={'pk': package.pk}),
            data=json.dumps({'revision': 1, 'reason': 'Aprovado para catálogo'}), content_type='application/json')
        self.assertEqual(approve.status_code, 200)
        old_key, old_hash = package.artifact_key, package.sha256
        with self.captureOnCommitCallbacks(execute=True):
            replaced = self.client.post(reverse('software-package-replace', kwargs={'pk': package.pk}), {
                'revision': 2, 'reason': 'Nova compilação validada',
                'file': SimpleUploadedFile('setup-v2.exe', exe_bytes(), content_type='application/octet-stream'),
            })
        self.assertEqual(replaced.status_code, 200, replaced.content)
        package.refresh_from_db()
        self.assertEqual(package.pk, UUID(replaced.json()['item']['id']))
        self.assertEqual(package.approval_status, 'draft')
        self.assertIsNone(package.approved_at)
        self.assertEqual(package.package_type, 'exe')
        self.assertNotEqual(package.sha256, old_hash)
        self.assertFalse((Path(self.temp.name) / 'private-store' / old_key).exists())
        replacement_event = AuditEvent.objects.get(event_type='software_package.artifact_replaced')
        self.assertEqual(replacement_event.metadata['before']['sha256'], old_hash)
        self.assertEqual(replacement_event.metadata['after']['sha256'], package.sha256)

    def test_profile_save_binds_hash_and_artifact_revision_and_requires_separate_approval(self):
        package, _ = self.create_package()
        self.assertEqual(self.approve_package(package).status_code, 200)
        package.refresh_from_db()
        configured = self.configure_msi_profile(package)
        self.assertEqual(configured.status_code, 200, configured.content)
        item = configured.json()['item']
        self.assertEqual(item['execution_profile_status'], 'draft')
        self.assertEqual(item['execution_profile_artifact_sha256'], package.sha256)
        self.assertEqual(item['execution_profile_artifact_revision'], package.artifact_revision)
        self.assertTrue(item['execution_profile_sha256'])
        package.refresh_from_db()
        self.assertEqual(package.approval_status, SoftwarePackageRecord.STATUS_APPROVED)
        self.assertEqual(package.execution_profile_status, 'draft')
        self.assertEqual(package.execution_profile_revision, 1)
        self.assertEqual(self.approve_profile(package).status_code, 200)
        package.refresh_from_db()
        self.assertEqual(package.execution_profile_status, 'approved')
        self.assertTrue(package_profile_binding_matches(package))
        self.assertTrue(package.execution_profile_approved_by_id)
        detail = self.client.get(reverse('software-package-detail', kwargs={'pk': package.pk})).json()['item']
        self.assertTrue(detail['operation_readiness']['install'])
        self.assertFalse(detail['operation_readiness']['update'])
        events = list(AuditEvent.objects.filter(metadata__software_package_id=str(package.pk)).order_by('created_at'))
        self.assertIn('software_package.profile_updated', [event.event_type for event in events])
        self.assertIn('software_package.profile_approved', [event.event_type for event in events])
        profile_event = next(event for event in events if event.event_type == 'software_package.profile_updated')
        self.assertEqual(profile_event.metadata['after']['execution_profile_sha256'], package.execution_profile_sha256)

        revised_profile = valid_msi_profile()
        revised_profile['expected_version'] = '1.2.4'
        changed = self.configure_msi_profile(package, profile=revised_profile)
        self.assertEqual(changed.status_code, 200, changed.content)
        package.refresh_from_db()
        self.assertEqual(package.approval_status, SoftwarePackageRecord.STATUS_APPROVED)
        self.assertEqual(package.execution_profile_status, 'draft')
        self.assertIsNone(package.execution_profile_approved_at)
        self.assertIsNone(package.execution_profile_approved_by_id)
        self.assertEqual(changed.json()['item']['operation_readiness'], {
            'install': False, 'update': False, 'uninstall': False,
        })

    def test_profile_cannot_be_configured_until_a_binary_is_associated(self):
        package = SoftwarePackageRecord.objects.create(
            friendly_name='Metadata-only record', package_type=SoftwarePackageRecord.TYPE_MSI,
            created_by=self.operator,
        )
        response = self.configure_msi_profile(package)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error'], 'file_required')
        package.refresh_from_db()
        self.assertEqual(package.execution_profile, {})
        self.assertEqual(package.execution_profile_revision, 0)

    def test_profile_approval_fails_closed_when_private_storage_is_unavailable(self):
        package, _ = self.create_package()
        self.assertEqual(self.approve_package(package).status_code, 200)
        package.refresh_from_db()
        self.assertEqual(self.configure_msi_profile(package).status_code, 200)
        package.refresh_from_db()
        with override_settings(NIGHTOWL_SOFTWARE_PACKAGE_STORAGE_CONFIGURED=False):
            response = self.approve_profile(package)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['error'], 'storage_unconfigured')
        package.refresh_from_db()
        self.assertEqual(package.execution_profile_status, 'draft')
        self.assertIsNone(package.execution_profile_approved_at)

    def test_profile_configuration_has_separate_permissions_and_csrf(self):
        package, _ = self.create_package()
        user = get_user_model().objects.create_user('software-profile-operator')
        with override_settings(NIGHTOWL_TECHNICAL_USERNAMES={'software-profile-operator'}):
            client = Client(enforce_csrf_checks=True)
            client.force_login(user)
            client.get(reverse('software-inventory'))
            body = {'revision': 1, 'reason': 'Profile config', 'execution_profile': valid_msi_profile()}
            url = reverse('software-package-profile', kwargs={'pk': package.pk})
            self.assertEqual(client.post(url, data=json.dumps(body), content_type='application/json').status_code, 403)
            user.user_permissions.add(Permission.objects.get(codename='configure_softwarepackageprofile'))
            token = client.cookies['csrftoken'].value
            self.assertEqual(client.post(url, data=json.dumps(body), content_type='application/json').status_code, 403)
            configured = client.post(url, data=json.dumps(body), content_type='application/json', HTTP_X_CSRFTOKEN=token)
            self.assertEqual(configured.status_code, 200, configured.content)
            approve_url = reverse('software-package-profile-approve', kwargs={'pk': package.pk})
            self.assertEqual(client.post(approve_url, data=json.dumps({'revision': 2, 'reason': 'No grant'}),
                content_type='application/json', HTTP_X_CSRFTOKEN=token).status_code, 403)
            user.user_permissions.add(Permission.objects.get(codename='approve_softwarepackageprofile'))
            package.refresh_from_db()
            denied_by_package_state = client.post(approve_url,
                data=json.dumps({'revision': package.revision, 'reason': 'Package remains draft'}),
                content_type='application/json', HTTP_X_CSRFTOKEN=token)
            self.assertEqual(denied_by_package_state.status_code, 409)

    def test_invalid_shell_like_or_msi_arguments_are_rejected_without_profile_change(self):
        package, _ = self.create_package()
        invalid = valid_msi_profile()
        invalid['install']['arguments'] = ['/qn & powershell.exe']
        response = self.configure_msi_profile(package, profile=invalid)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error'], 'msi_arguments_not_supported')
        package.refresh_from_db()
        self.assertEqual(package.execution_profile, {})
        self.assertEqual(package.execution_profile_revision, 0)
        registry_detection = valid_msi_profile()
        registry_detection['detection'] = {
            'method': 'registry_uninstall',
            'registry_key': r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Example',
            'display_name': 'Example',
        }
        rejected_detection = self.configure_msi_profile(package, profile=registry_detection)
        self.assertEqual(rejected_detection.status_code, 400)
        self.assertEqual(rejected_detection.json()['error'], 'unsupported_detection_method')

    def test_exe_profile_requires_structured_arguments_and_constrained_paths(self):
        package, _ = self.create_package(filename='setup.exe', content=exe_bytes())
        profile = {
            'schema_version': 1,
            'install': {'method': 'exe', 'arguments': ['/S', '/norestart']},
            'update': {'method': 'vendor_exe', 'arguments': ['/upgrade']},
            'uninstall': {'method': 'vendor_exe', 'executable_path': r'%ProgramFiles%\Vendor\App\uninstall.exe', 'arguments': ['/quiet']},
            'detection': {'method': 'file_version', 'path': r'%ProgramFiles%\Vendor\App\app.exe'},
            'expected_version': '5.4.0', 'reboot_behavior': 'report_only',
        }
        saved = self.configure_msi_profile(package, profile=profile)
        self.assertEqual(saved.status_code, 200, saved.content)
        package.refresh_from_db()
        self.assertEqual(package.execution_profile['install']['arguments'], ['/S', '/norestart'])
        invalid = dict(profile)
        invalid['uninstall'] = {'method': 'vendor_exe', 'executable_path': r'%ProgramFiles%\..\cmd.exe', 'arguments': []}
        stale_revision = package.revision
        rejected = self.configure_msi_profile(package, revision=stale_revision, profile=invalid)
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(rejected.json()['error'], 'uninstall_path_invalid')

    def test_profile_approval_is_invalidated_by_artifact_replacement_and_revocation(self):
        package, _ = self.create_package()
        self.assertEqual(self.approve_package(package).status_code, 200)
        package.refresh_from_db()
        self.assertEqual(self.configure_msi_profile(package).status_code, 200)
        package.refresh_from_db()
        self.assertEqual(self.approve_profile(package).status_code, 200)
        package.refresh_from_db()
        old_artifact_revision = package.artifact_revision
        replaced = self.client.post(reverse('software-package-replace', kwargs={'pk': package.pk}), {
            'revision': package.revision, 'reason': 'Novo binário',
            'file': SimpleUploadedFile('v2.msi', msi_bytes(1024)),
        })
        self.assertEqual(replaced.status_code, 200, replaced.content)
        package.refresh_from_db()
        self.assertEqual(package.artifact_revision, old_artifact_revision + 1)
        self.assertEqual(package.execution_profile_status, 'draft')
        self.assertFalse(package_profile_binding_matches(package))
        self.assertEqual(self.approve_profile(package).status_code, 409)
        self.assertEqual(self.approve_package(package).status_code, 200)
        package.refresh_from_db()
        configured = self.configure_msi_profile(package)
        self.assertEqual(configured.status_code, 200)
        package.refresh_from_db()
        self.assertEqual(self.approve_profile(package).status_code, 200)
        package.refresh_from_db()
        revoked = self.client.post(reverse('software-package-revoke', kwargs={'pk': package.pk}),
            data=json.dumps({'revision': package.revision, 'reason': 'Revogar pacote'}), content_type='application/json')
        self.assertEqual(revoked.status_code, 200)
        package.refresh_from_db()
        self.assertEqual(package.execution_profile_status, 'draft')

    def _approved_profile_package(self):
        package, _ = self.create_package()
        self.assertEqual(self.approve_package(package).status_code, 200)
        package.refresh_from_db()
        self.assertEqual(self.configure_msi_profile(package).status_code, 200)
        package.refresh_from_db()
        self.assertEqual(self.approve_profile(package).status_code, 200)
        package.refresh_from_db()
        return package

    def _endpoint_with_software_capability(self, capability=True):
        from django.utils import timezone
        endpoint = AgentMachine.objects.create(
            hostname='SOFTWARE-CAP-TEST', machine_id='software-cap-machine-01', agent_token_hash='c' * 64,
            status=AgentMachine.STATUS_ONLINE, last_seen_at=timezone.now(), agent_version='03c-a-test',
        )
        capabilities = ['software_install_profile:1'] if capability else []
        InventorySnapshot.objects.create(
            machine=endpoint, hostname=endpoint.hostname, collected_at=timezone.now(), received_at=timezone.now(),
            raw_payload={'snapshot_source': 'heartbeat', 'machine_id': endpoint.machine_id,
                         'agent_version': endpoint.agent_version, 'agent': {'capabilities': capabilities}},
        )
        return endpoint

    def test_operation_snapshot_binds_endpoint_package_profile_and_request_without_creating_job(self):
        from uuid import uuid4
        package = self._approved_profile_package()
        endpoint = self._endpoint_with_software_capability()
        request_id = str(uuid4())
        before = AgentJob.objects.count()
        with override_settings(NIGHTOWL_SOFTWARE_OPERATION_DISPATCH_ENABLED=True):
            first = prepare_software_operation_snapshot(self.operator, endpoint.pk, package.pk, 'install', request_id)
            replay = prepare_software_operation_snapshot(self.operator, endpoint.pk, package.pk, 'install', request_id)
        self.assertEqual(first['request_id'], request_id)
        self.assertEqual(replay['request_id'], request_id)
        self.assertEqual(first['endpoint'], {'uuid': str(endpoint.pk), 'machine_id': endpoint.machine_id})
        self.assertEqual(first['package']['uuid'], str(package.pk))
        self.assertEqual(first['package']['artifact_sha256'], package.sha256)
        self.assertEqual(first['package']['artifact_revision'], package.artifact_revision)
        self.assertEqual(first['package']['profile_revision'], package.execution_profile_revision)
        self.assertEqual(first['package']['profile_sha256'], package.execution_profile_sha256)
        self.assertEqual(first['package']['profile_status'], 'approved')
        self.assertTrue(first['package']['profile_approved_at'])
        self.assertEqual(first['package']['profile_approved_by'], str(self.operator.pk))
        self.assertEqual(first['operator']['id'], str(self.operator.pk))
        self.assertEqual(first['operator']['authorization'], 'agents.execute_softwarepackageoperation')
        self.assertEqual(AgentJob.objects.count(), before)

    def test_operation_snapshot_requires_explicit_permission_and_configured_operation(self):
        from uuid import uuid4
        package = self._approved_profile_package()
        endpoint = self._endpoint_with_software_capability()
        user = get_user_model().objects.create_user('software-operation-operator')
        with override_settings(NIGHTOWL_TECHNICAL_USERNAMES={'software-operation-operator'}):
            with self.assertRaises(PermissionDenied):
                prepare_software_operation_snapshot(user, endpoint.pk, package.pk, 'install', str(uuid4()))
            user.user_permissions.add(Permission.objects.get(codename='execute_softwarepackageoperation'))
            user = get_user_model().objects.get(pk=user.pk)
            with override_settings(NIGHTOWL_SOFTWARE_OPERATION_DISPATCH_ENABLED=True):
                with self.assertRaises(SoftwareOperationBlocked) as unconfigured:
                    prepare_software_operation_snapshot(user, endpoint.pk, package.pk, 'update', str(uuid4()))
                with self.assertRaises(SoftwareOperationBlocked) as unsupported:
                    prepare_software_operation_snapshot(user, endpoint.pk, package.pk, 'shell', str(uuid4()))
        self.assertEqual(unconfigured.exception.code, 'operation_not_configured')
        self.assertEqual(unsupported.exception.code, 'operation_unsupported')

    def test_operation_snapshot_fails_closed_for_missing_capability_or_dispatch_gate(self):
        from uuid import uuid4
        package = self._approved_profile_package()
        endpoint = self._endpoint_with_software_capability(capability=False)
        before = AgentJob.objects.count()
        with override_settings(NIGHTOWL_SOFTWARE_OPERATION_DISPATCH_ENABLED=True):
            with self.assertRaises(SoftwareOperationBlocked) as blocked:
                prepare_software_operation_snapshot(self.operator, endpoint.pk, package.pk, 'install', str(uuid4()))
        self.assertEqual(blocked.exception.code, 'agent_capability_unconfirmed')
        with self.assertRaises(SoftwareOperationBlocked) as disabled:
            prepare_software_operation_snapshot(self.operator, endpoint.pk, package.pk, 'install', str(uuid4()))
        self.assertEqual(disabled.exception.code, 'software_dispatch_not_enabled')
        self.assertEqual(AgentJob.objects.count(), before)

    def test_operation_snapshot_rejects_ambiguous_identity_and_active_job(self):
        from uuid import uuid4
        package = self._approved_profile_package()
        endpoint = self._endpoint_with_software_capability()
        duplicate = AgentMachine.objects.create(
            hostname='OTHER-HOST', machine_id=endpoint.machine_id, agent_token_hash='d' * 64,
        )
        with override_settings(NIGHTOWL_SOFTWARE_OPERATION_DISPATCH_ENABLED=True):
            with self.assertRaises(SoftwareOperationBlocked) as blocked:
                prepare_software_operation_snapshot(self.operator, endpoint.pk, package.pk, 'install', str(uuid4()))
            self.assertEqual(blocked.exception.code, 'endpoint_identity_invalid')
            duplicate.delete()
            AgentJob.objects.create(endpoint=endpoint, job_type=AgentJob.TYPE_PING, status=AgentJob.STATUS_RUNNING)
            with self.assertRaises(SoftwareOperationBlocked) as conflict:
                prepare_software_operation_snapshot(self.operator, endpoint.pk, package.pk, 'install', str(uuid4()))
        self.assertEqual(conflict.exception.code, 'endpoint_job_conflict')
        self.assertEqual(AgentJob.objects.filter(endpoint=endpoint).count(), 1)

    def test_active_or_historical_install_job_prevents_replacement_and_permanent_delete(self):
        package, _ = self.create_package()
        endpoint = AgentMachine.objects.create(hostname='PKG-TEST', machine_id='pkg-test-machine', agent_token_hash='a' * 64)
        job = AgentJob.objects.create(endpoint=endpoint, job_type=AgentJob.TYPE_INSTALL_SOFTWARE,
            status=AgentJob.STATUS_RUNNING, payload={'software_package_id': str(package.pk)})
        old_key = package.artifact_key
        replacement = self.client.post(reverse('software-package-replace', kwargs={'pk': package.pk}), {
            'revision': 1, 'reason': 'Replace during operation',
            'file': SimpleUploadedFile('new.msi', msi_bytes(1024)),
        })
        self.assertEqual(replacement.status_code, 409)
        denied_delete = self.client.post(reverse('software-package-delete', kwargs={'pk': package.pk}),
            data=json.dumps({'revision': 1, 'reason': 'Delete during operation'}), content_type='application/json')
        self.assertEqual(denied_delete.status_code, 409)
        self.assertTrue((Path(self.temp.name) / 'private-store' / old_key).is_file())
        job.status = AgentJob.STATUS_FAILED
        job.save(update_fields=['status'])
        historical = self.client.post(reverse('software-package-delete', kwargs={'pk': package.pk}),
            data=json.dumps({'revision': 1, 'reason': 'Delete used installer'}), content_type='application/json')
        self.assertEqual(historical.status_code, 409)
        self.assertEqual(historical.json()['error'], 'package_has_operational_history')
        package.refresh_from_db()
        self.assertTrue((Path(self.temp.name) / 'private-store' / package.artifact_key).is_file())

    def test_unused_record_can_be_deleted_but_immutable_audit_snapshot_remains(self):
        package, _ = self.create_package()
        package_id, artifact_key, old_hash = str(package.pk), package.artifact_key, package.sha256
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('software-package-delete', kwargs={'pk': package.pk}),
                data=json.dumps({'revision': 1, 'reason': 'Cadastro criado por engano'}), content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(SoftwarePackageRecord.objects.filter(pk=package.pk).exists())
        self.assertFalse((Path(self.temp.name) / 'private-store' / artifact_key).exists())
        event = AuditEvent.objects.get(event_type='software_package.deleted')
        self.assertEqual(event.metadata['software_package_id'], package_id)
        self.assertEqual(event.metadata['before']['sha256'], old_hash)
        self.assertEqual(event.description, 'Cadastro criado por engano')


@skipUnless(connection.vendor == 'postgresql', 'Row-lock concurrency requires the PostgreSQL CI gate.')
class SoftwarePackageConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nightowl-package-concurrency-')
        self.settings_override = override_settings(
            NIGHTOWL_SOFTWARE_PACKAGE_STORAGE_ROOT=Path(self.temp.name) / 'private-store',
            NIGHTOWL_SOFTWARE_PACKAGE_STORAGE_CONFIGURED=True,
            NIGHTOWL_SOFTWARE_PACKAGE_MAX_UPLOAD_BYTES=1024 * 1024,
            MEDIA_ROOT=Path(self.temp.name) / 'public-media', STATIC_ROOT=Path(self.temp.name) / 'public-static',
        )
        self.settings_override.enable()
        self.operator = get_user_model().objects.create_superuser('package-race-admin')
        client = Client()
        client.force_login(self.operator)
        response = client.post(reverse('software-package-create'), {
            'friendly_name': 'Concurrent installer',
            'file': SimpleUploadedFile('base.msi', msi_bytes()),
        })
        self.assertEqual(response.status_code, 201, response.content)
        self.package_id = response.json()['item']['id']

    def tearDown(self):
        self.settings_override.disable()
        self.temp.cleanup()

    def test_two_replacements_with_same_revision_are_serialized_and_one_is_rejected_stale(self):
        gate = Barrier(2)

        def replace(name, content):
            client = Client()
            client.force_login(self.operator)
            gate.wait(timeout=10)
            return client.post(reverse('software-package-replace', kwargs={'pk': self.package_id}), {
                'revision': 1, 'reason': 'Concurrent replacement test',
                'file': SimpleUploadedFile(name, content),
            }).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(replace, 'a.exe', exe_bytes()), pool.submit(replace, 'b.msi', msi_bytes())]
            statuses = sorted(future.result(timeout=40) for future in futures)
        self.assertEqual(statuses, [200, 409])
        package = SoftwarePackageRecord.objects.get(pk=self.package_id)
        self.assertEqual(package.revision, 2)
        self.assertIn(package.package_type, {'msi', 'exe'})
        self.assertEqual(AuditEvent.objects.filter(event_type='software_package.artifact_replaced').count(), 1)
        file_path = Path(self.temp.name) / 'private-store' / package.artifact_key
        self.assertTrue(file_path.is_file())
        self.assertEqual(hashlib.sha256(file_path.read_bytes()).hexdigest(), package.sha256)

    def test_concurrent_profile_saves_with_same_revision_commit_only_one_snapshot(self):
        gate = Barrier(2)

        def save_profile():
            client = Client()
            client.force_login(self.operator)
            gate.wait(timeout=10)
            return client.post(reverse('software-package-profile', kwargs={'pk': self.package_id}),
                data=json.dumps({'revision': 1, 'reason': 'Concurrent profile review',
                                 'execution_profile': valid_msi_profile()}),
                content_type='application/json').status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = sorted(future.result(timeout=40) for future in [pool.submit(save_profile), pool.submit(save_profile)])
        self.assertEqual(statuses, [200, 409])
        package = SoftwarePackageRecord.objects.get(pk=self.package_id)
        self.assertEqual(package.revision, 2)
        self.assertEqual(package.execution_profile_revision, 1)
        self.assertEqual(AuditEvent.objects.filter(event_type='software_package.profile_updated').count(), 1)
