import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import IntegrityError, close_old_connections, transaction
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from .asset_registry import AssetRegistryError, create_asset_for_endpoint, endpoint_asset_payload, link_asset_to_endpoint
from .identity import meaningful_serial
from .models import AgentMachine, AssetAgentLink, AssetRecord, AuditEvent, InventorySnapshot, PhysicalAsset


class AssetRegistryModelAndServiceTests(TestCase):
    def setUp(self):
        self.operator = get_user_model().objects.create_superuser('asset-registry-operator', password='synthetic-only')
        self.endpoint, _ = AgentMachine.create_with_token(
            hostname='ASSET-ONE', machine_id=str(uuid.uuid4()), serial_number='SN-001',
            manufacturer='Example', model='Workstation X', status=AgentMachine.STATUS_OFFLINE,
        )
        self.inventory(self.endpoint, serial='SN-001')

    def inventory(self, endpoint, *, serial=None, manufacturer='Example', model='Workstation X'):
        InventorySnapshot.objects.create(
            machine=endpoint, hostname=endpoint.hostname, collected_at=timezone.now(),
            raw_payload={'collections': {'system': {
                'manufacturer': manufacturer, 'model': model,
                **({'serial_number': serial} if serial is not None else {}),
            }, 'hardware': {'bios': {'serial_number': serial} if serial is not None else {}}}},
        )

    def test_serial_normalization_rejects_duplicate_active_assets_and_ignores_invalid_values(self):
        first = PhysicalAsset.objects.create(serial_number='  S-N 1234 ', manufacturer='Example')
        self.assertEqual(first.serial_normalized, meaningful_serial('sn1234'))
        with self.assertRaises(IntegrityError), self.captureOnCommitCallbacks(execute=True):
            with transaction.atomic():
                PhysicalAsset.objects.create(serial_number='sn-1234')
        empty = PhysicalAsset.objects.create(serial_number='To be filled by O.E.M.')
        repeated = PhysicalAsset.objects.create(serial_number='Unknown')
        self.assertIsNone(empty.serial_normalized)
        self.assertIsNone(repeated.serial_normalized)

    def test_generated_codes_are_unique_and_asset_identity_is_independent_from_endpoint(self):
        asset = create_asset_for_endpoint(self.endpoint.pk, self.endpoint.machine_id, self.operator,
                                          identity_confirmed=True)
        asset.identity_status = PhysicalAsset.IDENTITY_REVIEW
        asset.save(update_fields=['identity_status'])
        with self.assertRaises(AssetRegistryError) as error:
            link_asset_to_endpoint(self.endpoint.pk, self.endpoint.machine_id, asset.pk, self.operator,
                                   identity_confirmed=True)
        self.assertEqual(error.exception.code, 'identity_resolution_required')
        link_asset_to_endpoint(self.endpoint.pk, self.endpoint.machine_id, asset.pk, self.operator,
                               identity_confirmed=True, reason='Identidade revisada pelo operador')
        self.assertEqual(PhysicalAsset.objects.get(pk=asset.pk).identity_status, PhysicalAsset.IDENTITY_REVIEW)
        self.assertTrue(asset.asset_code.startswith('PROV-'))
        self.assertEqual(len(asset.asset_code), 37)
        self.assertNotEqual(asset.pk, self.endpoint.pk)
        self.assertEqual(AssetAgentLink.objects.filter(endpoint=self.endpoint, ended_at__isnull=True).count(), 1)
        self.assertEqual(AuditEvent.objects.filter(event_type='asset.created', endpoint=self.endpoint).count(), 1)

    def test_inventory_serial_used_for_display_and_uniqueness_is_the_same_evidence(self):
        snapshot = self.endpoint.inventory_snapshots.get()
        snapshot.serial_number = 'SN 001'
        snapshot.raw_payload = {'collections': {'system': {'serial_number': 'Unknown'}}}
        snapshot.save(update_fields=['serial_number', 'raw_payload'])
        asset = create_asset_for_endpoint(self.endpoint.pk, self.endpoint.machine_id, self.operator,
                                          identity_confirmed=True)
        self.assertEqual(asset.serial_number, 'SN 001')
        self.assertEqual(asset.serial_normalized, 'sn001')
        self.assertEqual(asset.agent_links.get().evidence['serial_normalized'], asset.serial_normalized)
        asset.serial_number = 'SN 002'
        asset.save(update_fields=['serial_number'])
        asset.refresh_from_db()
        self.assertEqual(asset.serial_normalized, 'sn002')

    def test_flat_snapshot_serial_participates_in_conflict_detection(self):
        snapshot = self.endpoint.inventory_snapshots.get()
        snapshot.serial_number = 'OTHER-SERIAL'
        snapshot.save(update_fields=['serial_number'])
        payload = endpoint_asset_payload(self.endpoint)
        self.assertEqual(payload['state'], 'conflict')
        self.assertIsNone(payload['inventory']['serial_normalized'])
        with self.assertRaisesMessage(AssetRegistryError, 'inventory_serial_conflict'):
            create_asset_for_endpoint(self.endpoint.pk, self.endpoint.machine_id, self.operator,
                                      identity_confirmed=True)

    def test_deleted_agent_link_is_preserved_and_requires_explicit_transfer(self):
        asset = create_asset_for_endpoint(self.endpoint.pk, self.endpoint.machine_id, self.operator,
                                          identity_confirmed=True)
        previous_uuid = self.endpoint.pk
        previous_machine_id = self.endpoint.machine_id
        record = AssetRecord.objects.create(asset=asset, record_type=AssetRecord.TYPE_FORMATTED,
            description='Formatação', occurred_at=timezone.now())
        self.endpoint.delete()
        orphan = asset.agent_links.get()
        self.assertIsNone(orphan.endpoint_id)
        self.assertEqual(orphan.endpoint_uuid_snapshot, previous_uuid)
        self.assertEqual(orphan.machine_id_snapshot, previous_machine_id)
        replacement, _ = AgentMachine.create_with_token(
            hostname='ASSET-REPLACEMENT', machine_id=str(uuid.uuid4()), serial_number='SN-001',
        )
        suggestion = endpoint_asset_payload(replacement)
        self.assertEqual(suggestion['suggestion_active_link']['state'], 'removed')
        self.assertTrue(suggestion['suggestion_requires_transfer'])
        self.client.force_login(self.operator)
        search = self.client.get(reverse('api-endpoint-asset-records', args=[replacement.pk]),
                                 {'asset_q': asset.asset_code}).json()
        reference = search['asset_search_results'][0]['active_endpoint']
        self.assertEqual((reference['id'], reference['state']), (str(previous_uuid), 'removed'))
        with self.assertRaisesMessage(AssetRegistryError, 'link_transfer_confirmation_required'):
            link_asset_to_endpoint(replacement.pk, replacement.machine_id, asset.pk, self.operator,
                                   identity_confirmed=True, reason='Reinstalação conferida')
        link_asset_to_endpoint(replacement.pk, replacement.machine_id, asset.pk, self.operator,
                               identity_confirmed=True, transfer_confirmed=True, reason='Reinstalação conferida')
        payload = endpoint_asset_payload(replacement)
        self.assertEqual(payload['asset']['id'], str(asset.pk))
        self.assertEqual(payload['records'][0]['id'], str(record.pk))
        old, new = payload['links']
        self.assertEqual(old['endpoint_id'], str(previous_uuid))
        self.assertEqual(old['machine_id'], previous_machine_id)
        self.assertIsNotNone(old['ended_at'])
        self.assertFalse(old['current'])
        self.assertTrue(new['current'])
        self.assertEqual(new['machine_id'], replacement.machine_id)
        event = AuditEvent.objects.get(event_type='asset.agent_linked', endpoint=replacement)
        self.assertEqual(event.metadata['previous_endpoint_id'], str(previous_uuid))

    def test_changed_machine_id_requires_explicit_relink_and_keeps_previous_snapshot(self):
        asset = create_asset_for_endpoint(self.endpoint.pk, self.endpoint.machine_id, self.operator,
                                          identity_confirmed=True)
        old_link = asset.agent_links.get()
        self.endpoint.machine_id = str(uuid.uuid4())
        self.endpoint.save(update_fields=['machine_id'])
        payload = endpoint_asset_payload(self.endpoint)
        self.assertEqual(payload['state'], 'conflict')
        self.assertTrue(payload['link_identity_changed'])
        self.assertIsNone(payload['asset'])
        self.assertNotIn('records', payload)
        self.assertEqual(payload['suggestion_active_link']['state'], 'identity_changed')
        self.assertTrue(payload['suggestion_requires_transfer'])
        self.client.force_login(self.operator)
        search = self.client.get(reverse('api-endpoint-asset-records', args=[self.endpoint.pk]),
                                 {'asset_q': asset.asset_code}).json()
        self.assertEqual(search['asset_search_results'][0]['active_endpoint']['state'], 'identity_changed')
        with self.assertRaisesMessage(AssetRegistryError, 'link_transfer_confirmation_required'):
            link_asset_to_endpoint(self.endpoint.pk, self.endpoint.machine_id, asset.pk, self.operator,
                                   identity_confirmed=True, reason='Identidade conferida')
        link_asset_to_endpoint(self.endpoint.pk, self.endpoint.machine_id, asset.pk, self.operator,
                               identity_confirmed=True, transfer_confirmed=True, reason='Identidade conferida')
        old_link.refresh_from_db()
        self.assertIsNotNone(old_link.ended_at)
        new_link = asset.agent_links.get(ended_at__isnull=True)
        self.assertNotEqual(old_link.machine_id_snapshot, new_link.machine_id_snapshot)
        self.assertEqual(new_link.machine_id_snapshot, self.endpoint.machine_id)
        self.assertEqual(endpoint_asset_payload(self.endpoint)['state'], 'linked')

    def test_review_identity_requires_reason_before_link_and_audits_explicit_resolution(self):
        asset = PhysicalAsset.objects.create(
            serial_number='SN-001', identity_status=PhysicalAsset.IDENTITY_REVIEW,
        )
        with self.assertRaises(AssetRegistryError) as error:
            link_asset_to_endpoint(self.endpoint.pk, self.endpoint.machine_id, asset.pk, self.operator,
                                   identity_confirmed=True)
        self.assertEqual(error.exception.code, 'identity_resolution_required')
        link_asset_to_endpoint(self.endpoint.pk, self.endpoint.machine_id, asset.pk, self.operator,
                               identity_confirmed=True, reason='Conflito conferido em inspeção física')
        asset.refresh_from_db()
        self.assertEqual(asset.identity_status, PhysicalAsset.IDENTITY_CONFIRMED)
        event = AuditEvent.objects.get(event_type='asset.agent_linked', endpoint=self.endpoint)
        self.assertIn('Conflito conferido', event.description)

    def test_database_constraints_allow_only_one_active_link_per_endpoint_and_asset(self):
        first = PhysicalAsset.objects.create()
        second = PhysicalAsset.objects.create()
        AssetAgentLink.objects.create(asset=first, endpoint=self.endpoint,
            endpoint_uuid_snapshot=self.endpoint.pk, machine_id_snapshot=self.endpoint.machine_id)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AssetAgentLink.objects.create(asset=second, endpoint=self.endpoint,
                    endpoint_uuid_snapshot=self.endpoint.pk, machine_id_snapshot=self.endpoint.machine_id)
        another_endpoint, _ = AgentMachine.create_with_token(hostname='ASSET-TWO', machine_id=str(uuid.uuid4()))
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AssetAgentLink.objects.create(asset=first, endpoint=another_endpoint,
                    endpoint_uuid_snapshot=another_endpoint.pk, machine_id_snapshot=another_endpoint.machine_id)

    def test_endpoint_identity_ambiguity_requires_operator_resolution_and_hostname_is_not_a_key(self):
        other, _ = AgentMachine.create_with_token(
            hostname=self.endpoint.hostname, domain=self.endpoint.domain, machine_id=self.endpoint.machine_id,
            serial_number='DIFFERENT-SERIAL',
        )
        self.inventory(other, serial='DIFFERENT-SERIAL')
        payload = endpoint_asset_payload(self.endpoint)
        self.assertIn('machine_id_ambiguous', [warning['code'] for warning in payload['identity_warnings']])
        self.assertIn('hostname_duplicated', [warning['code'] for warning in payload['identity_warnings']])
        self.assertIsNone(payload['suggestion'])
        with self.assertRaises(AssetRegistryError) as error:
            create_asset_for_endpoint(self.endpoint.pk, self.endpoint.machine_id, self.operator,
                                       identity_confirmed=True)
        self.assertEqual(error.exception.code, 'identity_resolution_required')
        asset = create_asset_for_endpoint(self.endpoint.pk, self.endpoint.machine_id, self.operator,
                                          identity_confirmed=True, reason='Conferido pelo operador no UUID')
        self.assertEqual(asset.identity_status, PhysicalAsset.IDENTITY_REVIEW)

    def test_serial_suggestion_is_explicit_and_transfer_preserves_agent_history_and_records(self):
        asset = create_asset_for_endpoint(self.endpoint.pk, self.endpoint.machine_id, self.operator,
                                          identity_confirmed=True)
        record = AssetRecord.objects.create(asset=asset, record_type=AssetRecord.TYPE_FORMATTED,
            description='Formatação registrada manualmente', occurred_at=timezone.now(),
            author=self.operator, author_label=self.operator.get_username())
        replacement, _ = AgentMachine.create_with_token(
            hostname='ASSET-REINSTALLED', machine_id=str(uuid.uuid4()), serial_number='SN-001',
            manufacturer='Example', model='Workstation X',
        )
        self.inventory(replacement, serial='SN-001')
        suggestion = endpoint_asset_payload(replacement)
        self.assertEqual(suggestion['suggestion']['id'], str(asset.pk))
        with self.assertRaises(AssetRegistryError) as error:
            link_asset_to_endpoint(replacement.pk, replacement.machine_id, asset.pk, self.operator,
                                   identity_confirmed=True, reason='Reinstalação conferida')
        self.assertEqual(error.exception.code, 'link_transfer_confirmation_required')
        link_asset_to_endpoint(replacement.pk, replacement.machine_id, asset.pk, self.operator,
                               identity_confirmed=True, transfer_confirmed=True,
                               reason='Reinstalação conferida em bancada')
        old_link = AssetAgentLink.objects.get(endpoint=self.endpoint)
        new_link = AssetAgentLink.objects.get(endpoint=replacement)
        self.assertIsNotNone(old_link.ended_at)
        self.assertIsNone(new_link.ended_at)
        self.assertEqual(AssetRecord.objects.get(pk=record.pk).asset_id, asset.pk)
        self.assertEqual(AssetAgentLink.objects.filter(asset=asset).count(), 2)
        self.assertTrue(AuditEvent.objects.filter(event_type='asset.agent_linked', endpoint=replacement).exists())

    def test_record_creation_requires_active_endpoint_link_and_keeps_author_and_dates(self):
        asset = create_asset_for_endpoint(self.endpoint.pk, self.endpoint.machine_id, self.operator,
                                          identity_confirmed=True)
        occurred = timezone.now() - timedelta(days=1)
        from .asset_registry import add_asset_record
        record = add_asset_record(self.endpoint.pk, self.endpoint.machine_id, self.operator,
            record_type=AssetRecord.TYPE_RESPONSIBILITY_CHANGE, description='Responsável atualizado',
            occurred_at=occurred, previous_responsible='Pessoa A', new_responsible='Pessoa B')
        self.assertEqual(record.asset_id, asset.pk)
        self.assertEqual(record.author_label, self.operator.get_username())
        self.assertEqual(record.previous_responsible, 'Pessoa A')
        self.assertTrue(AuditEvent.objects.filter(event_type='asset.record_created', endpoint=self.endpoint).exists())

    def test_linked_asset_surfaces_inventory_evidence_conflicts_without_reassigning(self):
        asset = create_asset_for_endpoint(self.endpoint.pk, self.endpoint.machine_id, self.operator,
                                          identity_confirmed=True)
        snapshot = self.endpoint.inventory_snapshots.order_by('-received_at', '-pk').first()
        snapshot.raw_payload = {'collections': {'full_inventory': {
            'system': {'serial_number': 'DIFFERENT-SERIAL', 'manufacturer': 'Other Vendor', 'model': 'Other Model'},
            'hardware': {'bios': {'serial_number': 'DIFFERENT-SERIAL'}},
        }}}
        snapshot.save(update_fields=['raw_payload'])
        payload = endpoint_asset_payload(self.endpoint)
        self.assertEqual(payload['state'], 'linked')
        self.assertEqual(payload['asset']['id'], str(asset.pk))
        self.assertEqual({item['code'] for item in payload['evidence_conflicts']},
                         {'inventory_serial_conflict', 'asset_manufacturer_mismatch', 'asset_model_mismatch'})
        self.assertEqual(AssetAgentLink.objects.filter(endpoint=self.endpoint, ended_at__isnull=True).count(), 1)


class AssetRegistryApiTests(TestCase):
    def setUp(self):
        self.endpoint, _ = AgentMachine.create_with_token(
            hostname='ASSET-API', machine_id=str(uuid.uuid4()), serial_number='API-001',
            manufacturer='Example', model='API Workstation', status=AgentMachine.STATUS_OFFLINE,
        )
        InventorySnapshot.objects.create(machine=self.endpoint, hostname=self.endpoint.hostname,
            collected_at=timezone.now(), raw_payload={'collections': {'system': {
                'serial_number': 'API-001', 'manufacturer': 'Example', 'model': 'API Workstation',
            }}})
        self.url = reverse('api-endpoint-asset-records', args=[self.endpoint.pk])

    def user_with_permissions(self, username, *codes):
        user = get_user_model().objects.create_user(username, password='synthetic-only')
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label='agents',
            codename__in=['view_agentmachine', *codes]))
        return user

    def test_permissions_are_fail_closed_and_read_only_cannot_mutate(self):
        user = get_user_model().objects.create_user('asset-no-permission', password='synthetic-only', is_staff=True)
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        reader = self.user_with_permissions('asset-reader', 'view_physicalasset')
        reader.is_staff = True
        reader.save(update_fields=['is_staff'])
        self.client.force_login(reader)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['permissions']['create_asset'])
        self.assertEqual(self.client.post(self.url, json.dumps({'action': 'create_asset'}),
            content_type='application/json').status_code, 403)

    @patch('agents.asset_registry._serial_endpoint_conflicts')
    def test_read_does_not_scan_the_fleet_for_legacy_serial_collisions(self, serial_conflicts):
        reader = self.user_with_permissions('asset-reader-bounded', 'view_physicalasset')
        reader.is_staff = True
        reader.save(update_fields=['is_staff'])
        self.client.force_login(reader)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        serial_conflicts.assert_not_called()

    def test_records_tab_is_hidden_for_operator_without_registry_permissions(self):
        operator = get_user_model().objects.create_user(
            'asset-tab-no-permission', password='synthetic-only', is_staff=True,
        )
        self.client.force_login(operator)
        response = self.client.get(reverse('endpoint-detail', args=[self.endpoint.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-endpoint-tab="records"')

    def test_create_link_and_record_use_real_endpoint_and_audit(self):
        operator = get_user_model().objects.create_superuser('asset-api-admin', password='synthetic-only')
        self.client.force_login(operator)
        response = self.client.post(self.url, json.dumps({
            'action': 'create_asset', 'machine_id': self.endpoint.machine_id, 'identity_confirmed': True,
        }), content_type='application/json')
        self.assertEqual(response.status_code, 201, response.content)
        asset = PhysicalAsset.objects.get(pk=response.json()['asset_id'])
        self.assertEqual(asset.serial_normalized, 'api001')
        detail = self.client.get(self.url).json()
        self.assertEqual(detail['state'], 'linked')
        self.assertEqual(detail['asset']['id'], str(asset.pk))
        record = self.client.post(self.url, json.dumps({
            'action': 'add_record', 'machine_id': self.endpoint.machine_id,
            'record_type': 'observation', 'description': 'Revisão física',
            'occurred_at': timezone.now().isoformat(),
        }), content_type='application/json')
        self.assertEqual(record.status_code, 201, record.content)
        self.assertEqual(self.client.get(self.url).json()['records'][0]['description'], 'Revisão física')

    def test_link_rejects_invalid_asset_identifier_without_mutation(self):
        operator = get_user_model().objects.create_superuser('asset-invalid-id-admin', password='synthetic-only')
        self.client.force_login(operator)
        response = self.client.post(self.url, json.dumps({
            'action': 'link_asset', 'machine_id': self.endpoint.machine_id,
            'asset_id': 'not-a-uuid', 'identity_confirmed': True,
        }), content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error'], 'asset_id_invalid')
        self.assertEqual(PhysicalAsset.objects.count(), 0)

    def test_reader_cannot_add_records_without_explicit_add_permission(self):
        asset = PhysicalAsset.objects.create(serial_number='API-001')
        AssetAgentLink.objects.create(asset=asset, endpoint=self.endpoint,
            endpoint_uuid_snapshot=self.endpoint.pk, machine_id_snapshot=self.endpoint.machine_id)
        reader = self.user_with_permissions('asset-record-reader', 'view_physicalasset')
        reader.is_staff = True
        reader.save(update_fields=['is_staff'])
        self.client.force_login(reader)
        response = self.client.post(self.url, json.dumps({
            'action': 'add_record', 'machine_id': self.endpoint.machine_id,
            'record_type': 'observation', 'description': 'Não autorizado',
            'occurred_at': timezone.now().isoformat(),
        }), content_type='application/json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(AssetRecord.objects.count(), 0)

    def test_manual_record_rejects_stale_endpoint_or_changed_link_identity(self):
        operator = get_user_model().objects.create_superuser('asset-stale-identity-admin', password='synthetic-only')
        self.client.force_login(operator)
        create = self.client.post(self.url, json.dumps({
            'action': 'create_asset', 'machine_id': self.endpoint.machine_id, 'identity_confirmed': True,
        }), content_type='application/json')
        self.assertEqual(create.status_code, 201)
        old_machine_id = self.endpoint.machine_id
        self.endpoint.machine_id = str(uuid.uuid4())
        self.endpoint.save(update_fields=['machine_id'])
        payload = {'action': 'add_record', 'machine_id': old_machine_id,
                   'record_type': 'observation', 'description': 'Conflito de identidade',
                   'occurred_at': timezone.now().isoformat()}
        stale = self.client.post(self.url, json.dumps(payload), content_type='application/json')
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()['error'], 'endpoint_identity_changed')
        payload['machine_id'] = self.endpoint.machine_id
        changed_link = self.client.post(self.url, json.dumps(payload), content_type='application/json')
        self.assertEqual(changed_link.status_code, 409)
        self.assertEqual(changed_link.json()['error'], 'asset_link_identity_changed')
        self.assertEqual(AssetRecord.objects.count(), 0)

    def test_csrf_is_required_and_hostname_is_never_used_to_select_endpoint(self):
        operator = get_user_model().objects.create_superuser('asset-csrf-admin', password='synthetic-only')
        client = Client(enforce_csrf_checks=True)
        client.force_login(operator)
        denied = client.post(self.url, json.dumps({'action': 'create_asset', 'machine_id': self.endpoint.machine_id,
            'identity_confirmed': True}), content_type='application/json')
        self.assertEqual(denied.status_code, 403)
        page = client.get(reverse('endpoint-detail', args=[self.endpoint.pk]))
        self.assertEqual(page.status_code, 200)
        csrf = page.cookies.get('csrftoken')
        self.assertIsNotNone(csrf)
        allowed = client.post(self.url, json.dumps({'action': 'create_asset', 'machine_id': self.endpoint.machine_id,
            'identity_confirmed': True}), content_type='application/json', HTTP_X_CSRFTOKEN=csrf.value)
        self.assertEqual(allowed.status_code, 201, allowed.content)

    def test_duplicate_endpoint_serial_requires_explicit_reason_and_preserves_no_false_link(self):
        duplicate, _ = AgentMachine.create_with_token(
            hostname='ASSET-API-DUP', machine_id=str(uuid.uuid4()), serial_number='api001',
            manufacturer='Example', model='API Workstation',
        )
        operator = get_user_model().objects.create_superuser('asset-duplicate-admin', password='synthetic-only')
        self.client.force_login(operator)
        payload = {'action': 'create_asset', 'machine_id': duplicate.machine_id, 'identity_confirmed': True}
        response = self.client.post(reverse('api-endpoint-asset-records', args=[duplicate.pk]),
                                    json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error'], 'duplicate_endpoint_serial_requires_review')
        self.assertEqual(PhysicalAsset.objects.count(), 0)

        payload['reason'] = 'Serial conferido fisicamente pelo operador'
        response = self.client.post(reverse('api-endpoint-asset-records', args=[duplicate.pk]),
                                    json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 201, response.content)
        asset = PhysicalAsset.objects.get(pk=response.json()['asset_id'])
        self.assertEqual(asset.identity_status, PhysicalAsset.IDENTITY_REVIEW)
        self.assertEqual(AssetAgentLink.objects.filter(asset=asset, ended_at__isnull=True).count(), 1)


class AssetCodeConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def test_concurrent_provisional_codes_remain_unique(self):
        def create_one(_):
            close_old_connections()
            try:
                return PhysicalAsset.objects.create().asset_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=4) as pool:
            codes = list(pool.map(create_one, range(12)))
        self.assertEqual(len(codes), len(set(codes)))
        self.assertEqual(PhysicalAsset.objects.count(), 12)
