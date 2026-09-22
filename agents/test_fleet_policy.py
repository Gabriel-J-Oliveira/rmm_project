import json
import importlib
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.apps import apps
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from .fleet_policy import PolicyContractError, bulk_policy_operation
from .models import AgentJob, AgentMachine, AgentReleaseGroup, AuditEvent, InventorySnapshot
from .services import build_agent_rollout_preview, evaluate_agent_update_eligibility
from . import test_rollout_preview


class FleetPolicyTests(TestCase):
    setUp = test_rollout_preview.AgentRolloutPreviewTests.setUp

    def admin(self):
        user = get_user_model().objects.create_user(username='fleet-admin', is_staff=True, is_superuser=True)
        self.client.force_login(user)
        return user

    def preview_api(self, data=None, validate=False):
        return self.client.post(reverse('api-rollout-preview-validate' if validate else 'api-rollout-preview', kwargs={'pk': self.release.pk}),
                                data=json.dumps(data or {}), content_type='application/json')

    def request_data(self, changes=None):
        return {'endpoint_ids': [str(self.machine.pk)], 'changes': changes or {'update_paused': True},
                'reason': 'Synthetic administrative test', 'apply': False}

    def apply_plan(self, data, user):
        plan = bulk_policy_operation(data, user)
        return bulk_policy_operation({**data, 'apply': True, 'confirmed_count': plan['selected_count'],
                                      'expected_bulk_change_hash': plan['bulk_change_hash']}, user)

    def test_authorization_and_csrf(self):
        self.assertEqual(self.preview_api().status_code, 403)
        self.assertIn(self.client.post(reverse('api-endpoints-bulk-policy'), '{}', content_type='application/json').status_code, [302, 403])
        user = get_user_model().objects.create_user(username='not-allowed', is_staff=True)
        self.client.force_login(user)
        self.assertEqual(self.preview_api().status_code, 403)
        self.assertEqual(self.client.post(reverse('api-endpoints-bulk-policy'), '{}', content_type='application/json').status_code, 403)
        self.client = Client(enforce_csrf_checks=True)
        self.admin()
        self.assertEqual(self.preview_api().status_code, 403)

    def test_preview_and_validate_do_not_write(self):
        self.admin()
        before = list(AgentMachine.objects.values())
        with CaptureQueriesContext(connection) as queries:
            response = self.preview_api()
            data = response.json()
            validation = self.preview_api({'expected_cohort_hash': data['cohort_hash'], 'cohort_schema': 1}, validate=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(validation.status_code, 200)
        self.assertTrue(validation.json()['matches'])
        self.assertEqual(sum(data['reason_counts'].values()), data['total_candidates'])
        self.assertEqual(before, list(AgentMachine.objects.values()))
        self.assertFalse(AgentJob.objects.exists())
        self.assertFalse(AuditEvent.objects.exists())
        self.assertTrue(all(query['sql'].lstrip().upper().startswith('SELECT') for query in queries))

    def test_preview_api_distinguishes_selection_from_execution_readiness(self):
        self.admin()
        self.machine.last_seen_at = timezone.now()
        self.machine.save(update_fields=['last_seen_at'])
        self.release.status = 'paused'
        self.release.rollout_paused = True
        self.release.rollout_percentage = 0
        self.release.save(update_fields=['status', 'rollout_paused', 'rollout_percentage'])

        response = self.preview_api()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['eligible_count'], 1)
        self.assertFalse(response.json()['release_execution_ready'])
        self.assertEqual(response.json()['release_execution_blocker'], 'release_paused')
        script = (Path(__file__).resolve().parent.parent / 'static/js/fleet_policy.js').read_text(encoding='utf-8')
        self.assertIn('elegiveis para selecao', script)
        self.assertIn('execucao bloqueada', script)

    def test_material_changes_invalidate_preview(self):
        self.admin()
        self.machine.last_seen_at = timezone.now()
        self.machine.save()
        for change in ['policy', 'offline', 'group', 'release', 'job', 'updater']:
            with self.subTest(change=change):
                before = self.preview_api().json()
                if change == 'policy':
                    self.machine.update_policy = 'manual'; self.machine.save()
                elif change == 'offline':
                    self.machine.status = 'offline'; self.machine.save()
                elif change == 'group':
                    self.machine.rollout_groups.add(AgentReleaseGroup.objects.get(slug='workstations'))
                elif change == 'release':
                    self.release.rollout_percentage = 50; self.release.save()
                elif change == 'job':
                    self.machine.status = 'online'; self.machine.update_policy = 'automatic'; self.machine.save()
                    AgentJob.objects.create(endpoint=self.machine, job_type='update_agent', status='queued')
                else:
                    self.machine.updater_version = '0.1.0.7'; self.machine.save()
                response = self.preview_api({'cohort_schema': 1, 'expected_cohort_hash': before['cohort_hash']}, validate=True)
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()['error'], 'preview_changed')

    def test_api_bounds_and_invalid_contract(self):
        self.admin()
        for data in [{'freshness_seconds': 0}, {'freshness_seconds': 3601}, {'freshness_seconds': True}, {'target_group_ids': ['not-uuid']}, {'target_group_ids': [str(uuid.uuid4())]}, {'unexpected': True}]:
            self.assertEqual(self.preview_api(data).status_code, 400)
        self.assertEqual(self.preview_api({'cohort_schema': 2, 'expected_cohort_hash': 'a'*64}, validate=True).status_code, 400)

    def test_250_endpoints_deterministic_and_constant_queries(self):
        template = dict(agent_version='0.1.1.0-rc38', updater_version='0.1.1.0-rc38', agent_lifecycle_status='installed',
                        status='online', last_seen_at=self.now, update_channel='development', update_policy='automatic', auto_update_enabled=True)
        with CaptureQueriesContext(connection) as single:
            build_agent_rollout_preview(self.release, now=self.now)
        AgentMachine.objects.bulk_create([AgentMachine(hostname=f'SYNTHETIC-{index}', machine_id=str(uuid.uuid4()), agent_token_hash=f'synthetic-{index}', **template) for index in range(249)])
        with CaptureQueriesContext(connection) as individual:
            for machine in AgentMachine.objects.all():
                evaluate_agent_update_eligibility(machine, explicit_release=self.release, now=self.now, automatic_rollout=True)
                list(machine.rollout_groups.values_list('pk', flat=True))
        with CaptureQueriesContext(connection) as batch:
            first = build_agent_rollout_preview(self.release, now=self.now)
        second = build_agent_rollout_preview(self.release, now=self.now)
        self.assertEqual(first, second)
        self.assertEqual(first['total_candidates'], 250)
        self.assertEqual(first['eligible_count'], 250)
        self.assertLessEqual(len(single), 6)
        self.assertLessEqual(len(batch), 6)
        self.assertGreater(len(individual), 1000)
        self.assertFalse(AgentJob.objects.exists())
        print(f'Synthetic preview: one endpoint queries={len(single)}; 250 endpoints individual queries={len(individual)}; batch queries={len(batch)}')

    def test_dry_run_zero_writes_and_omitted_fields_preserved(self):
        user = self.admin()
        before = list(AgentMachine.objects.values())
        plan = bulk_policy_operation(self.request_data(), user)
        self.assertEqual(before, list(AgentMachine.objects.values()))
        self.assertEqual(plan['targets'][0]['before']['update_channel'], plan['targets'][0]['after']['update_channel'])
        self.assertFalse(AuditEvent.objects.exists())
        self.assertFalse(AgentJob.objects.exists())

    def test_apply_only_explicit_endpoint_and_audit(self):
        user = self.admin()
        other = AgentMachine.objects.create(hostname='UNSELECTED', machine_id=str(uuid.uuid4()), agent_token_hash='fake')
        result = self.apply_plan(self.request_data(), user)
        self.assertTrue(result['applied'])
        self.machine.refresh_from_db(); other.refresh_from_db()
        self.assertTrue(self.machine.update_paused)
        self.assertFalse(other.update_paused)
        self.assertEqual(AuditEvent.objects.get().metadata['changed_count'], 1)
        self.assertFalse(AgentJob.objects.exists())

    def test_groups_add_remove_replace_clear(self):
        user = self.admin()
        workstations = str(AgentReleaseGroup.objects.get(slug='workstations').pk)
        pilot = str(AgentReleaseGroup.objects.get(slug='pilot').pk)
        for action, ids, expected in [('add', [workstations], [workstations]), ('add', [pilot], [pilot, workstations]),
                                     ('remove', [workstations], [pilot]), ('replace', [workstations], [workstations]), ('clear', [], [])]:
            self.apply_plan(self.request_data({'groups': {'action': action, 'ids': ids}}), user)
            self.assertEqual(sorted(str(value) for value in self.machine.rollout_groups.values_list('pk', flat=True)), sorted(expected))

    def test_empty_missing_invalid_and_confirmation(self):
        user = self.admin()
        data = self.request_data()
        for changes in [{'pinned_agent_version': 'not-version'}, {'maintenance_window_timezone': 'Invalid/Zone'}, {'source': 'bad'}, {'auto_update_enabled': 'true'}, {'groups': None}]:
            with self.assertRaises(PolicyContractError):
                bulk_policy_operation({**data, 'changes': changes}, user)
        with self.assertRaises(PolicyContractError):
            bulk_policy_operation({**data, 'endpoint_ids': []}, user)
        with self.assertRaises(PolicyContractError):
            bulk_policy_operation({**data, 'apply': True}, user)
        plan = bulk_policy_operation({**data, 'endpoint_ids': [str(uuid.uuid4())]}, user)
        self.assertEqual(plan['rejected_count'], 1)

    def test_timezone_pin_and_partial_window_validation(self):
        user = self.admin()
        plan = bulk_policy_operation(self.request_data({'maintenance_window_start': '01:00'}), user)
        self.assertEqual(plan['rejected_count'], 1)
        self.apply_plan(self.request_data({'maintenance_window_start': '23:00', 'maintenance_window_end': '02:00',
                                          'maintenance_window_timezone': 'America/Sao_Paulo', 'pinned_agent_version': '0.1.1.0-rc39'}), user)
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.maintenance_window_timezone, 'America/Sao_Paulo')
        self.assertEqual(self.machine.pinned_agent_version, '0.1.1.0-rc39')

    def test_lost_update_returns_409_without_writes(self):
        user = self.admin(); data = self.request_data()
        plan = bulk_policy_operation(data, user)
        self.machine.update_policy = 'manual'; self.machine.save()
        with self.assertRaises(PolicyContractError) as caught:
            bulk_policy_operation({**data, 'apply': True, 'confirmed_count': 1, 'expected_bulk_change_hash': plan['bulk_change_hash']}, user)
        self.assertEqual(caught.exception.status, 409)
        self.machine.refresh_from_db(); self.assertFalse(self.machine.update_paused)
        self.assertFalse(AuditEvent.objects.exists())

    def test_audit_failure_rolls_back(self):
        user = self.admin()
        with patch('agents.fleet_policy.AuditEvent.objects.create', side_effect=RuntimeError('Synthetic audit unavailable')):
            with self.assertRaises(RuntimeError):
                self.apply_plan(self.request_data(), user)
        self.machine.refresh_from_db(); self.assertFalse(self.machine.update_paused)

    def test_protected_groups_cannot_enable_or_remove_protection(self):
        user = self.admin()
        for slug in ['critical', 'servers']:
            self.machine.rollout_groups.set([AgentReleaseGroup.objects.get(slug=slug)])
            for changes in [{'auto_update_enabled': True}, {'update_policy': 'automatic'}, {'groups': {'action': 'clear', 'ids': []}}]:
                plan = bulk_policy_operation(self.request_data(changes), user)
                self.assertEqual(plan['rejected_count'], 1)
                with self.assertRaises(PolicyContractError):
                    self.apply_plan(self.request_data(changes), user)

    def test_ui_permission_visibility_and_contract(self):
        user = self.admin()
        self.assertContains(self.client.get(reverse('endpoint-list')), 'data-fleet-open')
        self.assertContains(self.client.get(reverse('agent-releases')), 'data-fleet-preview')
        user.is_superuser = False; user.save()
        self.assertNotContains(self.client.get(reverse('endpoint-list')), 'data-fleet-open')
        self.assertNotContains(self.client.get(reverse('agent-releases')), 'data-fleet-preview')
        source = (Path(__file__).resolve().parent.parent / 'static/js/fleet_policy.js').read_text()
        self.assertIn('expected_cohort_hash', source)
        self.assertIn('expected_bulk_change_hash', source)
        self.assertIn('error.status === 409', source)
        self.assertNotIn('innerHTML', source)
        self.assertNotIn('/jobs/', source)

    def test_bulk_api_payload_and_warnings(self):
        self.admin()
        response = self.client.post(reverse('api-endpoints-bulk-policy'), data=json.dumps(self.request_data()), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.machine.status = 'offline'; self.machine.agent_lifecycle_status = ''; self.machine.machine_id = 'bad'; self.machine.save()
        plan = bulk_policy_operation(self.request_data(), get_user_model().objects.get(username='fleet-admin'))
        self.assertIn('endpoint_offline', plan['targets'][0]['warnings'])
        self.assertIn('endpoint_lifecycle_unknown', plan['targets'][0]['warnings'])
        self.assertIn('machine_identity_invalid', plan['targets'][0]['warnings'])

    def test_all_or_nothing_missing_endpoint(self):
        user = self.admin()
        data = {**self.request_data(), 'endpoint_ids': [str(self.machine.pk), str(uuid.uuid4())]}
        with self.assertRaises(PolicyContractError):
            self.apply_plan(data, user)
        self.machine.refresh_from_db()
        self.assertFalse(self.machine.update_paused)
        self.assertFalse(AuditEvent.objects.exists())

    def test_audit_reason_is_sanitized(self):
        user = self.admin()
        synthetic = 'clearly-fake-sensitive-value-for-regression'
        self.apply_plan({**self.request_data(), 'reason': 'Authorization: Bearer ' + synthetic}, user)
        self.assertNotIn(synthetic, json.dumps(AuditEvent.objects.get().metadata))

    def test_backfill_repeatability_malformed_snapshots_and_m2m(self):
        migration = importlib.import_module('agents.migrations.0030_rollout_eligibility_contract')
        group = AgentReleaseGroup.objects.get(slug='workstations')
        self.machine.rollout_groups.add(group)
        self.machine.is_pilot_endpoint = True
        self.machine.updater_version = self.machine.tray_version = ''
        self.machine.save()
        for payload in [[], {'agent': []}, {'collections': {'full_inventory': {'agent': {'updater_version': '0.1.1.0-rc38', 'tray_version': '0.1.1.0-rc38'}}}}]:
            InventorySnapshot.objects.create(machine=self.machine, hostname=self.machine.hostname,
                                             collected_at=self.now, raw_payload=payload)
        for _ in range(2):
            migration.backfill_reported_versions_and_pilot(apps, SimpleNamespace(connection=connection))
            self.machine.refresh_from_db()
            self.assertEqual(self.machine.updater_version, '0.1.1.0-rc38')
            self.assertEqual(self.machine.tray_version, '0.1.1.0-rc38')
            self.assertEqual(set(self.machine.rollout_groups.values_list('slug', flat=True)), {'pilot', 'workstations'})
