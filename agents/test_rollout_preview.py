import importlib
import uuid
from datetime import datetime, time, timedelta, timezone as utc
from types import SimpleNamespace

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .models import AgentJob, AgentMachine, AgentRelease, AgentReleaseGroup, AgentReleaseSigningKey, AuditEvent, InventorySnapshot
from .services import build_agent_rollout_preview, evaluate_agent_update_eligibility, record_collection, record_heartbeat


class AgentRolloutPreviewTests(TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 17, 15, tzinfo=utc.utc)
        self.machine = AgentMachine.objects.create(
            hostname='PREVIEW-LAB', machine_id=str(uuid.uuid4()), agent_token_hash='synthetic-hash',
            agent_version='0.1.1.0-rc38', updater_version='0.1.1.0-rc38',
            agent_lifecycle_status='installed', status='online', last_seen_at=self.now,
            update_channel='development', update_policy='automatic', auto_update_enabled=True,
        )
        self.key = AgentReleaseSigningKey.objects.create(
            key_id='synthetic-preview-key', public_key_xml='<RSAKeyValue/>', status='active')
        self.release = AgentRelease.objects.create(
            version='0.1.1.0-rc39', channel='development', status='published',
            package_url='https://nightowl.controlsul.com.br/downloads/nightowl-agent/test.zip',
            sha256='a'*64, size=100, manifest_url='https://nightowl.controlsul.com.br/manifest.json',
            manifest_sha256='b'*64, signature_url='https://nightowl.controlsul.com.br/manifest.sig',
            signature_sha256='c'*64, signature_key_id=self.key.key_id, signature_valid=True,
            legacy_unsigned=False, minimum_updater_version='0.1.1.0-rc6', rollout_percentage=100,
        )

    def decision(self, **kwargs):
        return evaluate_agent_update_eligibility(self.machine, explicit_release=self.release,
                                                 now=self.now, automatic_rollout=True, **kwargs)

    def preview(self, **kwargs):
        return build_agent_rollout_preview(self.release, now=self.now, **kwargs)

    def test_evaluation_and_preview_have_no_writes(self):
        before = list(AgentMachine.objects.values())
        releases = list(AgentRelease.objects.values())
        audits = AuditEvent.objects.count()
        queries = []
        def capture(execute, sql, params, many, context):
            queries.append(sql.strip().split()[0].upper())
            return execute(sql, params, many, context)
        with connection.execute_wrapper(capture):
            self.assertTrue(self.decision().eligible)
            result = self.preview()
        self.assertTrue(all(command == 'SELECT' for command in queries))
        self.assertEqual(before, list(AgentMachine.objects.values()))
        self.assertEqual(releases, list(AgentRelease.objects.values()))
        self.assertEqual(audits, AuditEvent.objects.count())
        self.assertFalse(AgentJob.objects.exists())
        self.assertEqual(result['eligible_count'], 1)
        self.assertNotIn('agent_token_hash', str(result))

    def test_artifact_source_channel_is_part_of_cohort_fingerprint(self):
        original_hash = self.preview()['cohort_hash']
        AgentRelease.objects.filter(pk=self.release.pk).update(source_channel='pilot')
        self.release.refresh_from_db()
        self.assertNotEqual(self.preview()['cohort_hash'], original_hash)

    def test_hash_is_repeatable_independent_of_queryset_order_and_visuals(self):
        AgentMachine.objects.create(hostname='SECOND', machine_id=str(uuid.uuid4()), agent_token_hash='second')
        first = self.preview(endpoints=AgentMachine.objects.order_by('id'))
        second = self.preview(endpoints=AgentMachine.objects.order_by('-id'))
        self.assertEqual(first, second)
        self.machine.hostname = 'RENAMED'
        self.machine.save(update_fields=['hostname'])
        third = build_agent_rollout_preview(self.release, now=self.now + timedelta(seconds=1))
        self.assertEqual(first['cohort_hash'], third['cohort_hash'])
        self.assertNotEqual(first['generated_at'], third['generated_at'])

    def test_relevant_policy_and_release_changes_change_hash(self):
        before = self.preview()['cohort_hash']
        self.machine.update_paused = True
        self.machine.save(update_fields=['update_paused'])
        after = self.preview()['cohort_hash']
        self.assertNotEqual(before, after)
        self.release.rollout_percentage = 50
        self.assertNotEqual(after, self.preview()['cohort_hash'])

    def test_operational_exclusions(self):
        cases = [
            ('status', 'offline', 'endpoint_offline'),
            ('last_seen_at', self.now-timedelta(minutes=16), 'endpoint_stale'),
            ('last_seen_at', None, 'endpoint_stale'),
            ('agent_lifecycle_status', '', 'endpoint_lifecycle_unknown'),
            ('agent_lifecycle_status', 'uninstalled', 'endpoint_lifecycle_terminal'),
            ('agent_lifecycle_status', 'purged', 'endpoint_lifecycle_terminal'),
            ('is_active', False, 'endpoint_inactive'),
            ('machine_id', 'LEGACY-HOST', 'machine_identity_invalid'),
            ('machine_id', '', 'machine_identity_invalid'),
            ('update_paused', True, 'endpoint_paused'),
            ('updater_version', '', 'updater_version_unknown'),
            ('updater_version', '0.1.0.7', 'updater_bootstrap_required'),
            ('updater_version', '0.1.1.0-rc6', 'minimum_updater_incompatible'),
        ]
        self.release.minimum_updater_version = '0.1.1.0-rc20'
        for field, value, reason in cases:
            with self.subTest(field=field, value=value):
                old = getattr(self.machine, field)
                setattr(self.machine, field, value)
                self.assertEqual(self.decision().reason_code, reason)
                setattr(self.machine, field, old)

    def test_updater_uses_reported_version_not_agent(self):
        self.machine.agent_version = '0.1.0.7'
        self.assertTrue(self.decision().eligible)
        self.machine.updater_version = ''
        self.assertEqual(self.decision().reason_code, 'updater_version_unknown')

    def test_release_and_key_security_gates(self):
        for field, value, reason in [('rollout_paused', True, 'release_paused'),
                                      ('revoked', True, 'release_revoked'),
                                      ('signature_valid', False, 'signature_invalid'),
                                      ('signature_key_id', 'missing', 'key_unknown')]:
            with self.subTest(field=field):
                old = getattr(self.release, field)
                setattr(self.release, field, value)
                self.assertEqual(self.decision().reason_code, reason)
                setattr(self.release, field, old)
        self.key.revoked_at = self.now-timedelta(days=1)
        self.key.status = 'revoked'
        self.key.revocation_reason = 'Synthetic revocation test'
        self.key.save()
        self.assertEqual(self.decision().reason_code, 'key_revoked')

    def test_campaign_selection_accepts_paused_rollout_zero_but_delivery_does_not(self):
        self.release.status = AgentRelease.STATUS_PAUSED
        self.release.rollout_paused = True
        self.release.rollout_percentage = 0
        self.release.save(update_fields=['status', 'rollout_paused', 'rollout_percentage'])

        preview = self.preview()
        delivery = evaluate_agent_update_eligibility(
            self.machine, now=self.now, explicit_release=self.release, automatic_rollout=True)

        self.assertEqual((preview['eligible_count'], preview['targets'][0]['reason_code']), (1, 'eligible'))
        self.assertFalse(preview['release_execution_ready'])
        self.assertEqual(preview['release_execution_blocker'], 'release_paused')
        self.assertEqual(delivery.reason_code, 'release_paused')
        self.machine.set_agent_token('synthetic-paused-policy-token')
        self.machine.save(update_fields=['agent_token_hash'])
        response = Client(HTTP_AUTHORIZATION='Bearer synthetic-paused-policy-token').get('/api/agent/update-policy/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['update_available'])
        self.assertFalse(AgentJob.objects.exists())

    def test_campaign_selection_accepts_published_rollout_zero_but_delivery_does_not(self):
        self.release.rollout_percentage = 0
        self.release.save(update_fields=['rollout_percentage'])

        preview = self.preview()
        delivery = evaluate_agent_update_eligibility(
            self.machine, now=self.now, explicit_release=self.release, automatic_rollout=True)

        self.assertEqual((preview['eligible_count'], preview['targets'][0]['reason_code']), (1, 'eligible'))
        self.assertTrue(preview['release_execution_ready'])
        self.assertEqual(preview['release_execution_blocker'], '')
        self.assertEqual(delivery.reason_code, 'rollout_not_selected')
        self.machine.set_agent_token('synthetic-zero-policy-token')
        self.machine.save(update_fields=['agent_token_hash'])
        response = Client(HTTP_AUTHORIZATION='Bearer synthetic-zero-policy-token').get('/api/agent/update-policy/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['update_available'])
        self.assertEqual(response.json()['reason_code'], 'rollout_not_selected')
        self.assertFalse(AgentJob.objects.exists())

    def test_campaign_selection_is_explicit_and_rejects_non_operational_release_states(self):
        with self.assertRaisesMessage(ValueError, 'Campaign selection requires'):
            evaluate_agent_update_eligibility(
                self.machine, now=self.now, explicit_release=self.release, campaign_selection=True)
        for status in [AgentRelease.STATUS_REVOKED, AgentRelease.STATUS_SUPERSEDED, 'active', 'draft', 'unknown']:
            with self.subTest(status=status):
                self.release.status = status
                self.release.revoked = status == AgentRelease.STATUS_REVOKED
                decision = evaluate_agent_update_eligibility(
                    self.machine, now=self.now, explicit_release=self.release,
                    automatic_rollout=True, campaign_selection=True)
                self.assertFalse(decision.eligible)
                self.assertIn(decision.reason_code, {'release_revoked', 'release_not_available'})
        self.release.revoked = False

    def test_pilot_group_is_only_authority(self):
        self.machine.update_channel = self.release.channel = 'pilot'
        self.machine.is_pilot_endpoint = True
        self.assertEqual(self.decision().reason_code, 'group_not_allowed')
        self.machine.rollout_groups.add(AgentReleaseGroup.objects.get(slug='pilot'))
        self.machine.is_pilot_endpoint = False
        self.assertTrue(self.decision().eligible)

    def test_active_and_stale_jobs_exclude_without_reconciling(self):
        j = AgentJob.objects.create(endpoint=self.machine, job_type='update_agent', status='sent',
                                    expires_at=self.now+timedelta(minutes=5))
        # Explicit timestamps avoid relying on the test runner's wall clock.
        AgentJob.objects.filter(pk=j.pk).update(created_at=self.now)
        self.assertEqual(self.decision().reason_code, 'update_job_active')
        j.expires_at = self.now-timedelta(days=1)
        j.save(update_fields=['expires_at'])
        self.assertEqual(self.decision().reason_code, 'update_job_stale')
        j.refresh_from_db()
        self.assertEqual(j.status, 'sent')

    def test_mandatory_does_not_bypass_rollout_or_auto_consent(self):
        self.release.mandatory = True
        self.release.rollout_percentage = 0
        self.assertEqual(self.decision().reason_code, 'rollout_not_selected')
        legacy = evaluate_agent_update_eligibility(self.machine, now=self.now, explicit_release=self.release)
        self.assertTrue(legacy.eligible)
        self.release.rollout_percentage = 100
        self.machine.auto_update_enabled = False
        self.assertEqual(self.decision().reason_code, 'manual_policy')

    def test_mandatory_does_not_bypass_security(self):
        self.release.mandatory = True
        self.test_operational_exclusions()
        self.release.allowed_groups.add(AgentReleaseGroup.objects.get(slug='workstations'))
        self.assertEqual(self.decision().reason_code, 'group_not_allowed')
        self.release.allowed_groups.clear()
        self.test_release_and_key_security_gates()

    def test_maintenance_timezone_and_midnight(self):
        self.machine.update_policy = 'maintenance_window'
        self.machine.maintenance_window_timezone = 'Asia/Tokyo'
        self.machine.maintenance_window_start = time(23)
        self.machine.maintenance_window_end = time(2)
        # 15 UTC is midnight in Tokyo.
        self.assertTrue(self.decision().eligible)
        self.machine.maintenance_window_timezone = 'UTC'
        self.assertEqual(self.decision().reason_code, 'outside_maintenance_window')
        self.machine.maintenance_window_end = time(23)
        self.assertEqual(self.decision().reason_code, 'maintenance_window_invalid')
        self.machine.maintenance_window_end = time(2)
        self.machine.maintenance_window_timezone = 'Invalid/Zone'
        self.assertEqual(self.decision().reason_code, 'maintenance_timezone_invalid')

    def test_downgrade_protected_and_group_filter_no_replacements(self):
        self.machine.agent_version = '0.1.1.0-rc40'
        self.assertEqual(self.decision().reason_code, 'downgrade_requires_force')
        group = AgentReleaseGroup.objects.get(slug='workstations')
        result = self.preview(target_group_ids=[group.pk])
        self.assertEqual(result['total_candidates'], 0)
        self.machine.rollout_groups.add(group)
        self.machine.save(update_fields=['agent_version'])
        result = self.preview(target_group_ids=[group.pk])
        self.assertEqual(result['total_candidates'], 1)
        self.assertEqual(result['eligible_count'], 0)

    def test_protected_groups_pin_and_ambiguous_identity(self):
        self.machine.rollout_groups.add(AgentReleaseGroup.objects.get(slug='critical'))
        self.assertEqual(self.decision().reason_code, 'protected_endpoint_group')
        self.machine.rollout_groups.clear()
        self.machine.pinned_agent_version = '0.1.1.0-rc38'
        self.assertEqual(self.decision().reason_code, 'pinned_release_mismatch')
        self.machine.pinned_agent_version = ''
        AgentMachine.objects.create(hostname='DUPLICATE', machine_id=self.machine.machine_id, agent_token_hash='duplicate')
        self.assertEqual(self.decision().reason_code, 'machine_identity_ambiguous')

    def test_release_group_restrictions_and_paused_status(self):
        self.release.allowed_groups.add(AgentReleaseGroup.objects.get(slug='workstations'))
        self.assertEqual(self.decision().reason_code, 'group_not_allowed')
        self.release.allowed_groups.clear()
        self.release.status = 'paused'
        self.assertEqual(self.decision().reason_code, 'release_paused')

    def test_signature_expiration_uses_explicit_preview_time(self):
        self.key.valid_until = self.now+timedelta(seconds=1)
        self.key.save()
        self.assertTrue(self.decision().eligible)
        later = build_agent_rollout_preview(self.release, now=self.now+timedelta(seconds=2))
        self.assertEqual(later['targets'][0]['reason_code'], 'signature_invalid')

    def test_policy_window_api_validates_timezone_and_equal_bounds(self):
        user = get_user_model().objects.create_user(username='timezone-admin', is_superuser=True, is_staff=True)
        self.client.force_login(user)
        url = reverse('api-endpoint-update-policy', kwargs={'pk': str(self.machine.pk)})
        self.assertEqual(self.client.post(url, {'maintenance_window_start': '23:00',
                                              'maintenance_window_end': '02:00',
                                              'maintenance_window_timezone': 'Asia/Tokyo'}).status_code, 200)
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.maintenance_window_timezone, 'Asia/Tokyo')
        self.assertEqual(self.client.post(url, {'maintenance_window_start': '23:00',
                                              'maintenance_window_end': '23:00'}).status_code, 400)
        self.assertEqual(self.client.post(url, {'maintenance_window_timezone': 'Invalid/Zone'}).status_code, 400)

    def test_group_filter_duplicates_do_not_change_hash(self):
        group = AgentReleaseGroup.objects.get(slug='workstations')
        self.machine.rollout_groups.add(group)
        self.assertEqual(self.preview(target_group_ids=[group.pk])['cohort_hash'],
                         self.preview(target_group_ids=[group.pk, group.pk])['cohort_hash'])

    @override_settings(TIME_ZONE='Asia/Tokyo')
    def test_existing_window_uses_settings_timezone_fallback(self):
        self.machine.update_policy = 'maintenance_window'
        self.machine.maintenance_window_start = time(23)
        self.machine.maintenance_window_end = time(2)
        self.assertTrue(self.decision().eligible)

    def test_reported_versions_are_persisted_and_partial_payload_preserves(self):
        record_heartbeat(self.machine, {'hostname': self.machine.hostname, 'agent': {
            'version': '0.1.1.0-rc38', 'updater_version': '0.1.1.0-rc37+synthetic', 'tray_version': '0.1.1.0-rc36'}}, {})
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.updater_version, '0.1.1.0-rc37')
        record_collection(self.machine, 'full_inventory', {'agent_version': '0.1.1.0-rc38', 'updater_version': '0.1.1.0-rc38'})
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.updater_version, '0.1.1.0-rc38')
        self.assertEqual(self.machine.tray_version, '0.1.1.0-rc36')

    def test_policy_groups_omitted_preserved_explicit_empty_cleared(self):
        user = get_user_model().objects.create_user(username='preview-admin', is_staff=True, is_superuser=True)
        self.client.force_login(user)
        group = AgentReleaseGroup.objects.get(slug='pilot')
        self.machine.rollout_groups.add(group)
        url = reverse('api-endpoint-update-policy', kwargs={'pk': str(self.machine.pk)})
        self.assertEqual(self.client.post(url, {'update_policy': 'manual'}).status_code, 200)
        self.assertTrue(self.machine.rollout_groups.exists())
        self.assertEqual(self.client.post(url, {'rollout_groups': ['']}).status_code, 200)
        self.assertFalse(self.machine.rollout_groups.exists())
        self.assertFalse(AgentJob.objects.exists())

    def test_backfill_preserves_legacy_pilot_and_reported_versions_only(self):
        migration = importlib.import_module('agents.migrations.0030_rollout_eligibility_contract')
        self.machine.is_pilot_endpoint = True
        self.machine.updater_version = self.machine.tray_version = ''
        self.machine.save()
        InventorySnapshot.objects.create(machine=self.machine, hostname=self.machine.hostname,
                                         collected_at=self.now, raw_payload={'updater_version': '0.1.1.0-rc37'})
        migration.backfill_reported_versions_and_pilot(apps, SimpleNamespace(connection=connection))
        self.machine.refresh_from_db()
        self.assertTrue(self.machine.rollout_groups.filter(slug='pilot').exists())
        self.assertEqual(self.machine.updater_version, '0.1.1.0-rc37')
        self.assertEqual(self.machine.tray_version, '')
