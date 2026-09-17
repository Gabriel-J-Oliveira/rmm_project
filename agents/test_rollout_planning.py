import io
import json
import os
import subprocess
import sys
import uuid
import unittest
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext

from . import test_rollout_preview as fixtures
from .models import AgentJob, AgentMachine, AgentRelease, AgentReleaseGroup, AgentRolloutCampaign, AgentRolloutTarget, AgentRolloutWave, AuditEvent
from .rollout_campaigns import create_agent_rollout_campaign_from_preview, transition_campaign, transition_wave
from .rollout_planning import build_rollout_dispatch_plan, build_rollout_planning_context, evaluate_rollout_target_dispatch_safety, rollout_orchestration_enabled
from .services import build_agent_rollout_preview


class DispatchPlanningTests(TestCase):
    def setUp(self):
        fixtures.AgentRolloutPreviewTests.setUp(self)
        AgentRelease.objects.filter(pk=self.release.pk).update(checksum_url='https://nightowl.controlsul.com.br/checksums.json')
        self.release.refresh_from_db()
        self.actor = get_user_model().objects.create_user(username='planner-admin', is_staff=True, is_superuser=True)

    def create(self, **options):
        preview = build_agent_rollout_preview(self.release, now=self.now)
        c = create_agent_rollout_campaign_from_preview(self.release,
            {'cohort_schema': 1, 'expected_cohort_hash': preview['cohort_hash'], 'wave_plan': [{'remaining': True}],
             'reason': 'Synthetic planning approval', **options}, self.actor, now=self.now)
        c = transition_campaign(c, 'running', self.actor, 'Synthetic domain start', now=self.now)
        transition_wave(c.waves.first(), 'ready', self.actor, 'Synthetic ready', now=self.now)
        return c

    def reason(self, c):
        return build_rollout_dispatch_plan(c, now=self.now)['targets'][0]['reason_code']

    def test_healthy_ready_and_running(self):
        c = self.create()
        self.assertEqual(self.reason(c), 'eligible_for_dispatch')
        transition_wave(c.waves.get(), 'running', self.actor, 'Synthetic running', now=self.now)
        self.assertEqual(self.reason(c), 'eligible_for_dispatch')
        self.assertFalse(AgentJob.objects.exists())

    def test_endpoint_drift_matrix(self):
        c = self.create()
        cases = [('update_policy', 'manual', 'endpoint_policy_changed'),
                 ('update_channel', 'stable', 'endpoint_channel_changed'),
                 ('pinned_agent_version', '0.1.1.0-rc38', 'pinned_release_mismatch'),
                 ('pinned_agent_version', '0.1.1.0-rc39', 'endpoint_policy_changed'),
                 ('auto_update_enabled', False, 'endpoint_policy_changed'),
                 ('update_paused', True, 'endpoint_paused'),
                 ('is_active', False, 'endpoint_inactive'),
                 ('agent_lifecycle_status', 'purged', 'endpoint_lifecycle_terminal'),
                 ('agent_lifecycle_status', '', 'endpoint_lifecycle_unknown'),
                 ('status', 'offline', 'endpoint_offline'),
                 ('last_seen_at', None, 'endpoint_stale'),
                 ('last_seen_at', self.now-timedelta(seconds=901), 'endpoint_stale'),
                 ('last_seen_at', self.now+timedelta(seconds=1), 'endpoint_stale'),
                 ('updater_version', '', 'updater_version_unknown'),
                 ('updater_version', 'invalid', 'updater_version_unknown'),
                 ('updater_version', '0.1.0.7', 'updater_bootstrap_required'),
                 ('machine_id', str(uuid.uuid4()), 'endpoint_identity_changed'),
                 ('agent_version', '', 'agent_version_unknown'),
                 ('agent_version', '0.1.1.0-rc39', 'target_already_current'),
                 ('agent_version', '0.1.1.0-rc40', 'downgrade_requires_force')]
        for field, value, reason in cases:
            old = getattr(self.machine, field)
            with self.subTest(field=field, value=str(value)):
                AgentMachine.objects.filter(pk=self.machine.pk).update(**{field: value})
                self.assertEqual(self.reason(c), reason)
                AgentMachine.objects.filter(pk=self.machine.pk).update(**{field: old})

    def test_group_drift_and_current_protections(self):
        c = self.create()
        for slug, reason in [('remote', 'endpoint_groups_changed'), ('critical', 'protected_endpoint_group'), ('servers', 'protected_endpoint_group')]:
            with self.subTest(slug=slug):
                self.machine.rollout_groups.set([AgentReleaseGroup.objects.get(slug=slug)])
                self.assertEqual(self.reason(c), reason)
        self.machine.rollout_groups.clear()

    def test_duplicate_identity_blocks(self):
        c = self.create()
        AgentMachine.objects.create(hostname='DUPLICATE', machine_id=self.machine.machine_id, agent_token_hash='synthetic-duplicate')
        self.assertEqual(self.reason(c), 'machine_identity_ambiguous')

    def test_release_material_drift_matrix(self):
        c = self.create()
        cases = [('version', '0.1.1.0-rc40'), ('channel', 'pilot'), ('sha256', 'd'*64), ('size', 101),
                 ('manifest_sha256', 'd'*64), ('signature_sha256', 'd'*64),
                 ('minimum_updater_version', '0.1.1.0-rc39'), ('mandatory', True),
                 ('package_url', 'https://nightowl.controlsul.com.br/different.zip')]
        for field, value in cases:
            with self.subTest(field=field):
                old = getattr(self.release, field)
                AgentRelease.objects.filter(pk=self.release.pk).update(**{field: value})
                self.assertEqual(self.reason(c), 'release_contract_changed')
                AgentRelease.objects.filter(pk=self.release.pk).update(**{field: old})
        self.release.allowed_groups.add(AgentReleaseGroup.objects.get(slug='remote'))
        self.assertEqual(self.reason(c), 'release_contract_changed')

    def test_release_security_matrix(self):
        c = self.create()
        for field, value, reason in [('revoked', True, 'release_revoked'), ('status', 'revoked', 'release_revoked'),
                                    ('status', 'superseded', 'release_not_available'), ('rollout_paused', True, 'release_paused'),
                                    ('status', 'paused', 'release_paused'), ('signature_valid', False, 'signature_invalid'),
                                    ('legacy_unsigned', True, 'signature_invalid'), ('signature_key_id', 'unknown-key', 'key_unknown'),
                                    ('package_url', 'http://invalid.example/test.zip', 'release_domain_invalid'),
                                    ('manifest_sha256', '', 'release_metadata_incomplete')]:
            with self.subTest(field=field):
                old = getattr(self.release, field)
                AgentRelease.objects.filter(pk=self.release.pk).update(**{field: value})
                self.assertEqual(self.reason(c), reason)
                AgentRelease.objects.filter(pk=self.release.pk).update(**{field: old})

    def test_key_temporal_security_uses_now(self):
        c = self.create()
        for field, value, reason in [('status', 'revoked', 'key_revoked'),
                                    ('valid_from', self.now+timedelta(seconds=1), 'key_not_yet_valid'),
                                    ('valid_until', self.now-timedelta(seconds=1), 'key_expired')]:
            with self.subTest(field=field):
                old = getattr(self.key, field)
                type(self.key).objects.filter(pk=self.key.pk).update(**{field: value})
                self.assertEqual(self.reason(c), reason)
                type(self.key).objects.filter(pk=self.key.pk).update(**{field: old})

    def test_rollout_percentage_and_hostname_do_not_reselect(self):
        c = self.create()
        before = list(c.targets.values())
        AgentRelease.objects.filter(pk=self.release.pk).update(rollout_percentage=0)
        AgentMachine.objects.filter(pk=self.machine.pk).update(hostname='RENAMED')
        self.assertEqual(self.reason(c), 'eligible_for_dispatch')
        self.assertEqual(before, list(c.targets.values()))
        self.assertEqual(build_rollout_dispatch_plan(c, now=self.now)['targets'][0]['hostname'], 'PREVIEW-LAB')

    def test_lifecycle_active_and_stale_jobs(self):
        c = self.create()
        for kind in ['update_agent', 'repair_agent', 'uninstall_agent']:
            for status in ['queued', 'sent', 'running']:
                with self.subTest(kind=kind, status=status):
                    j = AgentJob.objects.create(endpoint=self.machine, job_type=kind, status=status)
                    AgentJob.objects.filter(pk=j.pk).update(created_at=self.now)
                    plan = build_rollout_dispatch_plan(c, now=self.now)
                    self.assertEqual(plan['targets'][0]['reason_code'], 'update_job_active')
                    self.assertEqual(plan['available_capacity'], 0)
                    AgentJob.objects.filter(pk=j.pk).update(created_at=self.now-timedelta(seconds=901))
                    self.assertEqual(self.reason(c), 'update_job_stale')
                    j.delete()

    def test_non_running_campaign_and_non_dispatchable_wave(self):
        c = self.create()
        transition_campaign(c, 'paused', self.actor, 'Synthetic pause', now=self.now)
        self.assertEqual(self.reason(c), 'campaign_not_running')
        transition_campaign(c, 'running', self.actor, 'Synthetic resume', now=self.now)
        w = transition_wave(c.waves.get(), 'running', self.actor, 'Synthetic start', now=self.now)
        transition_wave(w, 'observing', self.actor, 'Synthetic observe', now=self.now)
        self.assertEqual(self.reason(c), 'wave_not_dispatchable')

    def test_250_targets_query_count_purity_determinism_and_capacity(self):
        small = self.create()
        with CaptureQueriesContext(connection) as small_queries:
            build_rollout_dispatch_plan(small, now=self.now)
        transition_campaign(small, 'aborted', self.actor, 'Synthetic small baseline finished', now=self.now)
        AgentMachine.objects.bulk_create([AgentMachine(hostname=f'PLAN-{i}', machine_id=str(uuid.uuid4()),
            agent_token_hash=f'synthetic-plan-{i}', agent_version=self.machine.agent_version,
            updater_version=self.machine.updater_version, agent_lifecycle_status='installed', status='online',
            last_seen_at=self.now, update_channel='development', update_policy='automatic', auto_update_enabled=True)
            for i in range(249)])
        c = self.create(concurrency_limit=3)
        models = [AgentRolloutCampaign, AgentRolloutWave, AgentRolloutTarget, AgentMachine, AgentRelease, AgentJob, AuditEvent]
        before = [list(m.objects.order_by('pk').values()) for m in models]
        with CaptureQueriesContext(connection) as queries:
            first = build_rollout_dispatch_plan(c, now=self.now)
        self.assertTrue(all(q['sql'].lstrip().upper().startswith('SELECT') for q in queries))
        self.assertLessEqual(len(queries), 10)
        self.assertEqual(len(queries), len(small_queries))
        print(f'Synthetic dispatch planner: 1 and 250 targets; SELECTs={len(queries)}')
        self.assertEqual(first, build_rollout_dispatch_plan(c.pk, now=self.now))
        context = build_rollout_planning_context(c)
        context['targets'].reverse()
        with patch('agents.rollout_planning.build_rollout_planning_context', return_value=context):
            self.assertEqual(first, build_rollout_dispatch_plan(c.pk, now=self.now))
        self.assertEqual((first['dispatchable_count'], first['blocked_count'], len(first['selected_for_dispatch'])), (250, 0, 3))
        self.assertEqual(first['targets'], sorted(first['targets'], key=lambda t: (t['bucket'], t['endpoint_id'])))
        self.assertEqual(before, [list(m.objects.order_by('pk').values()) for m in models])
        self.assertFalse(AgentJob.objects.exists())

    def test_no_replacement_between_waves(self):
        AgentMachine.objects.create(hostname='SECOND', machine_id=str(uuid.uuid4()), agent_token_hash='synthetic-wave2',
            agent_version=self.machine.agent_version, updater_version=self.machine.updater_version,
            agent_lifecycle_status='installed', status='online', last_seen_at=self.now,
            update_channel='development', update_policy='automatic', auto_update_enabled=True)
        c = self.create(wave_plan=[{'count': 1}, {'remaining': True}])
        first, second = list(c.waves.all())
        AgentMachine.objects.filter(pk=first.targets.get().endpoint_id).update(status='offline')
        plan = build_rollout_dispatch_plan(c, now=self.now)
        self.assertEqual((plan['total_wave_targets'], plan['blocked_count'], plan['selected_for_dispatch']), (1, 1, []))
        self.assertEqual(build_rollout_dispatch_plan(c, now=self.now, wave_id=second.pk)['targets'][0]['reason_code'], 'wave_not_dispatchable')

    def test_plan_only_command_and_switches(self):
        c = self.create()
        for orchestrator, automatic in [(False, False), (False, True), (True, False), (True, True)]:
            with override_settings(NIGHTOWL_ROLLOUT_ORCHESTRATOR_ENABLED=orchestrator, NIGHTOWL_AUTOMATIC_ROLLOUT_ENABLED=automatic):
                self.assertEqual(rollout_orchestration_enabled(), orchestrator and automatic)
                with self.assertRaisesMessage(CommandError, 'ROLLOUT_DISPATCH_NOT_IMPLEMENTED'):
                    call_command('process_agent_rollouts', stdout=io.StringIO())
                output = io.StringIO()
                with patch('agents.management.commands.process_agent_rollouts.timezone.now', return_value=self.now):
                    call_command('process_agent_rollouts', plan_only=True, campaign=str(c.pk), stdout=output)
                self.assertEqual(json.loads(output.getvalue())['dispatchable_count'], 1)
        self.assertFalse(AgentJob.objects.exists())

    def test_runtime_flags_default_off_without_dotenv(self):
        env = os.environ.copy()
        for key in ['NIGHTOWL_ROLLOUT_ORCHESTRATOR_ENABLED', 'NIGHTOWL_AUTOMATIC_ROLLOUT_ENABLED']:
            env.pop(key, None)
        code = 'import environ; from unittest.mock import patch;\nwith patch.object(environ.Env,"read_env",return_value=None):\n import config.settings as s\n print(s.NIGHTOWL_ROLLOUT_ORCHESTRATOR_ENABLED,s.NIGHTOWL_AUTOMATIC_ROLLOUT_ENABLED)'
        result = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), 'False False')

    def test_individual_evaluation_reloads_current_endpoint(self):
        c = self.create()
        t = c.targets.select_related('endpoint').get()
        AgentMachine.objects.filter(pk=self.machine.pk).update(status='offline')
        self.assertEqual(evaluate_rollout_target_dispatch_safety(c, t, now=self.now)['reason_code'], 'endpoint_offline')

    def test_minimum_updater_is_revalidated(self):
        AgentRelease.objects.filter(pk=self.release.pk).update(minimum_updater_version='0.1.1.0-rc38')
        self.release.refresh_from_db()
        c = self.create()
        AgentMachine.objects.filter(pk=self.machine.pk).update(updater_version='0.1.1.0-rc37')
        self.assertEqual(self.reason(c), 'minimum_updater_incompatible')

    def test_legacy_policy_snapshot_is_fail_closed(self):
        preview = build_agent_rollout_preview(self.release, now=self.now)
        c = create_agent_rollout_campaign_from_preview(self.release,
            {'cohort_schema': 1, 'expected_cohort_hash': preview['cohort_hash'], 'wave_plan': [{'count': 1}],
             'reason': 'Synthetic legacy snapshot', 'ready': False}, self.actor, now=self.now)
        c.targets.update(exclusion_metadata={})
        c = transition_campaign(c, 'ready', self.actor, 'Synthetic approval', now=self.now)
        c = transition_campaign(c, 'running', self.actor, 'Synthetic start', now=self.now)
        transition_wave(c.waves.get(), 'ready', self.actor, 'Synthetic ready', now=self.now)
        self.assertEqual(self.reason(c), 'target_policy_snapshot_incomplete')

    def test_sensitive_url_query_is_never_returned(self):
        c = self.create()
        value = 'synthetic-query-value-not-a-real-secret'
        AgentRelease.objects.filter(pk=self.release.pk).update(package_url=self.release.package_url+'?token='+value)
        plan = build_rollout_dispatch_plan(c, now=self.now)
        self.assertEqual(plan['targets'][0]['reason_code'], 'release_contract_changed')
        self.assertNotIn(value, json.dumps(plan))
        self.assertNotIn('https://', json.dumps(plan))

    def test_maintenance_window_is_current_safety(self):
        from datetime import time
        AgentMachine.objects.filter(pk=self.machine.pk).update(update_policy='maintenance_window',
            maintenance_window_start=time(14), maintenance_window_end=time(16), maintenance_window_timezone='UTC')
        self.machine.refresh_from_db()
        c = self.create()
        self.assertEqual(self.reason(c), 'eligible_for_dispatch')
        AgentMachine.objects.filter(pk=self.machine.pk).update(last_seen_at=self.now+timedelta(hours=2))
        self.assertEqual(build_rollout_dispatch_plan(c, now=self.now+timedelta(hours=2))['targets'][0]['reason_code'], 'outside_maintenance_window')
        AgentMachine.objects.filter(pk=self.machine.pk).update(last_seen_at=self.now)
        AgentMachine.objects.filter(pk=self.machine.pk).update(maintenance_window_start=time(16), maintenance_window_end=time(18))
        self.assertEqual(self.reason(c), 'endpoint_policy_changed')


@unittest.skipUnless(connection.vendor == 'postgresql', 'Connection restart requires PostgreSQL')
class PostgreSQLPlanningRestartTests(TransactionTestCase):
    def test_plan_survives_connection_restart(self):
        DispatchPlanningTests.setUp(self)
        c = DispatchPlanningTests.create(self)
        before = build_rollout_dispatch_plan(c, now=self.now)
        self.assertEqual(before['dispatchable_count'], 1)
        pk = c.pk
        connections.close_all()
        self.assertEqual(build_rollout_dispatch_plan(pk, now=self.now), before)
        self.assertFalse(AgentJob.objects.exists())
