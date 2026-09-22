import threading
import unittest
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, close_old_connections, connection, connections, transaction
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from . import test_rollout_preview as fixtures
from .fleet_policy import PolicyContractError
from .models import AgentJob, AgentMachine, AgentRolloutCampaign, AgentRolloutTarget, AgentRolloutWave, AuditEvent
from .rollout_campaigns import create_agent_rollout_campaign_from_preview, transition_campaign, transition_wave
from .services import build_agent_rollout_preview


class CampaignTests(TestCase):
    def setUp(self):
        fixtures.AgentRolloutPreviewTests.setUp(self)
        self.actor = get_user_model().objects.create_user(username='campaign-admin', is_staff=True, is_superuser=True)

    def data(self, **changes):
        preview = build_agent_rollout_preview(self.release, now=self.now)
        return {'cohort_schema': preview['cohort_schema'], 'expected_cohort_hash': preview['cohort_hash'],
                'wave_plan': [{'remaining': True, 'observation_seconds': 10}],
                'reason': 'Synthetic approved campaign', **changes}

    def create(self, data=None):
        return create_agent_rollout_campaign_from_preview(self.release, data or self.data(), self.actor, now=self.now)

    def test_all_candidates_and_exclusions_are_persisted(self):
        AgentMachine.objects.create(hostname='EXCLUDED', machine_id=str(uuid.uuid4()), agent_token_hash='synthetic-excluded')
        c = self.create()
        self.assertEqual((c.total_candidates, c.eligible_count, c.excluded_count), (2, 1, 1))
        excluded = c.targets.get(state='excluded')
        self.assertIsNone(excluded.wave_id)
        self.assertTrue(excluded.reason_code)
        self.assertEqual(c.targets.count(), 2)
        self.assertFalse(AgentJob.objects.exists())

    def test_paused_rollout_zero_release_can_freeze_ready_campaign_without_jobs(self):
        self.release.status = 'paused'
        self.release.rollout_paused = True
        self.release.rollout_percentage = 0
        self.release.save(update_fields=['status', 'rollout_paused', 'rollout_percentage'])

        preview = build_agent_rollout_preview(self.release, now=self.now)
        campaign = create_agent_rollout_campaign_from_preview(
            self.release,
            {'cohort_schema': preview['cohort_schema'], 'expected_cohort_hash': preview['cohort_hash'],
             'wave_plan': [{'remaining': True}], 'reason': 'Synthetic paused selection'},
            self.actor, now=self.now)

        self.assertEqual((preview['eligible_count'], campaign.state, campaign.eligible_count), (1, 'ready', 1))
        self.assertEqual(campaign.targets.get().state, 'eligible')
        self.assertEqual(campaign.waves.count(), 1)
        self.assertFalse(AgentJob.objects.exists())

    def test_250_targets_deterministic_and_reload(self):
        AgentMachine.objects.bulk_create([AgentMachine(hostname=f'SYNTHETIC-{i}', machine_id=str(uuid.uuid4()),
            agent_token_hash=f'synthetic-{i}', agent_version=self.machine.agent_version, updater_version=self.machine.updater_version,
            agent_lifecycle_status='installed', status='online', last_seen_at=self.now, update_channel='development',
            update_policy='automatic', auto_update_enabled=True) for i in range(249)])
        c = self.create(self.data(wave_plan=[{'count': 1}, {'count': 2}, {'remaining': True}]))
        self.assertEqual(c.targets.count(), 250)
        self.assertEqual(list(c.waves.values_list('target_count', flat=True)), [1, 2, 247])
        ordered = list(c.targets.order_by('rollout_bucket', 'endpoint_id'))
        self.assertEqual([t.wave.sequence for t in ordered[:4]], [1, 2, 2, 3])
        pk, fingerprint = c.pk, c.cohort_hash
        c = AgentRolloutCampaign.objects.get(pk=pk)
        self.assertEqual(c.cohort_hash, fingerprint)
        self.assertEqual(c.eligible_count, c.targets.filter(state='eligible').count())
        self.assertEqual(c.wave_plan, [{'count': 1, 'observation_seconds': 0}, {'count': 2, 'observation_seconds': 0}, {'count': 247, 'observation_seconds': 0}])
        self.assertFalse(AgentJob.objects.exists())

    def test_endpoint_changes_do_not_rewrite_snapshot(self):
        c = self.create()
        before = c.targets.get().endpoint_hostname_snapshot
        self.machine.hostname = 'CHANGED'
        self.machine.update_policy = 'manual'
        self.machine.save()
        t = c.targets.get()
        self.assertEqual(t.endpoint_hostname_snapshot, before)
        self.assertEqual(t.update_policy_snapshot, 'automatic')

    def test_release_changes_do_not_rewrite_snapshot(self):
        c = self.create()
        self.release.revoked = True
        self.release.rollout_paused = True
        self.release.save()
        c.refresh_from_db()
        self.assertFalse(c.release_snapshot['revoked'])
        self.assertEqual(c.state, 'ready')

    def assert_changed(self, data):
        with self.assertRaises(PolicyContractError) as result:
            self.create(data)
        self.assertEqual((str(result.exception), result.exception.status), ('preview_changed', 409))
        self.assertFalse(AgentRolloutCampaign.objects.exists())
        self.assertFalse(AgentRolloutWave.objects.exists())
        self.assertFalse(AgentRolloutTarget.objects.exists())

    def test_stale_policy(self):
        data = self.data()
        self.machine.update_policy = 'manual'
        self.machine.save()
        self.assert_changed(data)

    def test_stale_group(self):
        data = self.data()
        from .models import AgentReleaseGroup
        self.machine.rollout_groups.add(AgentReleaseGroup.objects.get(slug='critical'))
        self.assert_changed(data)

    def test_stale_updater(self):
        data = self.data()
        self.machine.updater_version = ''
        self.machine.save()
        self.assert_changed(data)

    def test_stale_release(self):
        data = self.data()
        self.release.revoked = True
        self.release.save()
        self.assert_changed(data)

    def test_stale_active_job(self):
        data = self.data()
        AgentJob.objects.create(endpoint=self.machine, job_type='update_agent', status='queued')
        self.assert_changed(data)
        self.assertEqual(AgentJob.objects.count(), 1)

    def test_stale_online(self):
        data = self.data()
        self.machine.status = 'offline'
        self.machine.save()
        self.assert_changed(data)

    def test_stale_freshness(self):
        data = self.data()
        self.machine.last_seen_at = self.now - timedelta(hours=1)
        self.machine.save()
        self.assert_changed(data)

    def test_invalid_wave_plans(self):
        for plan in [[{'count': 0}], [{'count': 2}], [{'remaining': True}, {'count': 1}],
                     [{'remaining': True, 'count': 1}], [{'count': True}], [{'count': 1, 'observation_seconds': -1}], []]:
            with self.subTest(plan=plan), self.assertRaises(PolicyContractError):
                self.create(self.data(wave_plan=plan))
        self.assertFalse(AgentRolloutCampaign.objects.exists())

    def test_zero_eligible_draft_only(self):
        self.machine.status = 'offline'
        self.machine.save()
        with self.assertRaises(PolicyContractError):
            self.create(self.data(wave_plan=[]))
        c = self.create(self.data(ready=False, wave_plan=[]))
        self.assertEqual((c.state, c.targets.count(), c.waves.count()), ('draft', 1, 0))
        with self.assertRaises(PolicyContractError):
            transition_campaign(c, 'ready', self.actor, 'Synthetic approval', now=self.now)

    def test_duplicate_live_campaign_conflict(self):
        self.create()
        with self.assertRaises(PolicyContractError) as result:
            self.create()
        self.assertEqual(result.exception.status, 409)
        self.assertEqual(AgentRolloutCampaign.objects.count(), 1)

    def test_audit_failure_rolls_back_everything(self):
        with patch('agents.rollout_campaigns.audit', side_effect=RuntimeError('synthetic audit failure')):
            with self.assertRaises(RuntimeError):
                self.create()
        self.assertFalse(AgentRolloutCampaign.objects.exists())
        self.assertFalse(AgentRolloutWave.objects.exists())
        self.assertFalse(AgentRolloutTarget.objects.exists())

    def test_campaign_and_wave_states(self):
        c = self.create()
        c = transition_campaign(c, 'running', self.actor, 'Synthetic start', now=self.now)
        w = c.waves.get()
        for state in ['ready', 'running', 'observing']:
            w = transition_wave(w, state, self.actor, 'Synthetic transition', now=self.now)
        with self.assertRaises(PolicyContractError):
            transition_wave(w, 'completed', self.actor, 'Synthetic early completion', now=self.now)
        w = transition_wave(w, 'completed', self.actor, 'Synthetic domain completion', now=self.now + timedelta(seconds=10))
        c = transition_campaign(c, 'paused', self.actor, 'Synthetic pause', now=self.now)
        c = transition_campaign(c, 'running', self.actor, 'Synthetic resume', now=self.now)
        c = transition_campaign(c, 'completed', self.actor, 'Synthetic complete', now=self.now)
        self.assertIsNotNone(c.completed_at)
        with self.assertRaises(PolicyContractError):
            transition_campaign(c, 'running', self.actor, 'Synthetic reopen')
        with self.assertRaises(PolicyContractError):
            transition_wave(w, 'running', self.actor, 'Synthetic reopen')
        self.assertFalse(AgentJob.objects.exists())

    def test_abort_cancels_waves(self):
        c = self.create()
        c = transition_campaign(c, 'aborted', self.actor, 'Synthetic abort', now=self.now)
        self.assertEqual(c.waves.get().state, 'cancelled')
        with self.assertRaises(PolicyContractError):
            transition_wave(c.waves.get(), 'running', self.actor, 'Synthetic invalid')

    def test_complete_before_start_rejected(self):
        c = self.create()
        with self.assertRaises(PolicyContractError):
            transition_wave(c.waves.get(), 'completed', self.actor, 'Synthetic invalid')
        with self.assertRaises(PolicyContractError):
            transition_campaign(c, 'completed', self.actor, 'Synthetic invalid')

    def test_snapshot_guards_block_direct_updates(self):
        c = self.create()
        for queryset, changes in [(AgentRolloutCampaign.objects.filter(pk=c.pk), {'cohort_hash': 'b'*64}),
                                  (c.waves.all(), {'target_count': 2, 'eligible_target_count': 2}),
                                  (c.targets.all(), {'endpoint_hostname_snapshot': 'INVALID'})]:
            with self.subTest(changes=changes), self.assertRaises(IntegrityError), transaction.atomic():
                queryset.update(**changes)

    def test_snapshot_guards_block_save_and_bulk_update(self):
        c = self.create()
        t = c.targets.get()
        t.endpoint_hostname_snapshot = 'INVALID'
        with self.assertRaises(IntegrityError), transaction.atomic():
            t.save()
        with self.assertRaises(IntegrityError), transaction.atomic():
            AgentRolloutTarget.objects.bulk_update([t], ['endpoint_hostname_snapshot'])

    def test_ready_cannot_be_thawed_with_direct_update(self):
        c = self.create()
        with self.assertRaises(IntegrityError), transaction.atomic():
            AgentRolloutCampaign.objects.filter(pk=c.pk).update(state='draft')
        with self.assertRaises(IntegrityError), transaction.atomic():
            c.waves.update(state='completed')

    def test_ready_blocks_target_and_wave_insertion(self):
        c = self.create()
        with self.assertRaises(IntegrityError), transaction.atomic():
            AgentRolloutWave.objects.create(campaign=c, sequence=2, target_count=1, eligible_target_count=1)
        target = c.targets.get()
        target.pk = uuid.uuid4()
        with self.assertRaises(IntegrityError), transaction.atomic():
            target.save(force_insert=True)

    def test_permission_is_required_even_for_staff(self):
        actor = get_user_model().objects.create_user(username='staff-only', is_staff=True)
        with self.assertRaises(PolicyContractError) as result:
            create_agent_rollout_campaign_from_preview(self.release, self.data(), actor, now=self.now)
        self.assertEqual(result.exception.status, 403)
        self.assertFalse(AgentRolloutCampaign.objects.exists())

    def test_cross_campaign_wave_rejected(self):
        c = self.create(self.data(ready=False))
        transition_campaign(c, 'aborted', self.actor, 'Synthetic abort')
        other = self.create(self.data(ready=False))
        with self.assertRaises(IntegrityError), transaction.atomic():
            other.targets.update(wave_id=c.waves.get().pk)
        with self.assertRaises(IntegrityError), transaction.atomic():
            AgentRolloutCampaign.objects.filter(pk=other.pk).update(current_wave_id=c.waves.get().pk)

    def test_target_cannot_dispatch(self):
        c = self.create(self.data(ready=False))
        with self.assertRaises(IntegrityError), transaction.atomic():
            c.targets.update(state='queued')

    def test_no_parallel_waves_and_pause_resume(self):
        AgentMachine.objects.create(hostname='SECOND', machine_id=str(uuid.uuid4()), agent_token_hash='synthetic-second',
            agent_version=self.machine.agent_version, updater_version=self.machine.updater_version,
            agent_lifecycle_status='installed', status='online', last_seen_at=self.now,
            update_channel='development', update_policy='automatic', auto_update_enabled=True)
        c = self.create(self.data(wave_plan=[{'count': 1}, {'count': 1}]))
        c = transition_campaign(c, 'running', self.actor, 'Synthetic start', now=self.now)
        first, second = list(c.waves.all())
        first = transition_wave(first, 'ready', self.actor, 'Synthetic ready', now=self.now)
        first = transition_wave(first, 'running', self.actor, 'Synthetic start', now=self.now)
        first = transition_wave(first, 'paused', self.actor, 'Synthetic pause', now=self.now)
        second = transition_wave(second, 'ready', self.actor, 'Synthetic ready', now=self.now)
        c.refresh_from_db()
        self.assertEqual(c.current_wave_id, first.pk)
        with self.assertRaises(PolicyContractError):
            transition_wave(second, 'running', self.actor, 'Synthetic invalid', now=self.now)
        first = transition_wave(first, 'running', self.actor, 'Synthetic resume', now=self.now)
        self.assertEqual(first.started_at, self.now)
        self.assertFalse(AgentJob.objects.exists())

    def test_database_count_and_identity_constraints(self):
        c = self.create(self.data(ready=False))
        for changes in [{'concurrency_limit': 0}, {'total_candidates': 9}, {'eligible_count': -1}]:
            with self.subTest(changes=changes), self.assertRaises(IntegrityError), transaction.atomic():
                AgentRolloutCampaign.objects.filter(pk=c.pk).update(**changes)
        t = c.targets.get()
        t.pk = uuid.uuid4()
        with self.assertRaises(IntegrityError), transaction.atomic():
            t.save(force_insert=True)
        with self.assertRaises(IntegrityError), transaction.atomic():
            AgentRolloutWave.objects.create(campaign=c, sequence=1, target_count=1, eligible_target_count=1)

    def test_no_secret_in_snapshot_or_audit(self):
        c = self.create()
        serialized = str(list(c.targets.values())) + str(list(AuditEvent.objects.values())) + str(c.release_snapshot)
        self.assertNotIn('synthetic-hash', serialized)
        self.assertNotIn('agent_token', serialized)

    def test_create_csrf_is_enforced(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.actor)
        self.assertEqual(client.post(reverse('api-rollout-campaign-create', args=[self.release.pk]),
                                     data=self.data(), content_type='application/json').status_code, 403)
        self.assertFalse(AgentRolloutCampaign.objects.exists())

    def test_api_authorization_and_creation(self):
        url = reverse('api-rollout-campaign-create', args=[self.release.pk])
        self.assertEqual(self.client.post(url, data=self.data(), content_type='application/json').status_code, 403)
        self.client.force_login(self.actor)
        with patch('agents.rollout_campaigns.timezone.now', return_value=self.now):
            result = self.client.post(url, data=self.data(), content_type='application/json')
        self.assertEqual(result.status_code, 201, result.content)
        self.assertEqual(result.json()['state'], 'ready')
        self.assertFalse(AgentJob.objects.exists())


@unittest.skipUnless(connection.vendor == 'postgresql', 'Real concurrency requires PostgreSQL')
class PostgreSQLCampaignConcurrencyTests(TransactionTestCase):
    def test_restart_with_250_targets(self):
        fixtures.AgentRolloutPreviewTests.setUp(self)
        actor = get_user_model().objects.create_user(username='restart-admin', is_staff=True, is_superuser=True)
        AgentMachine.objects.bulk_create([AgentMachine(hostname=f'RESTART-{i}', machine_id=str(uuid.uuid4()),
            agent_token_hash=f'synthetic-restart-{i}', agent_version=self.machine.agent_version,
            updater_version=self.machine.updater_version, agent_lifecycle_status='installed', status='online',
            last_seen_at=self.now, update_channel='development', update_policy='automatic', auto_update_enabled=True)
            for i in range(249)])
        preview = build_agent_rollout_preview(self.release, now=self.now)
        c = create_agent_rollout_campaign_from_preview(self.release,
            {'cohort_schema': 1, 'expected_cohort_hash': preview['cohort_hash'],
             'wave_plan': [{'count': 1}, {'count': 2}, {'remaining': True}], 'reason': 'Synthetic restart'}, actor, now=self.now)
        before = list(c.targets.order_by('id').values()), list(c.waves.values()), c.cohort_hash, c.wave_plan
        pk = c.pk
        connections.close_all()
        c = AgentRolloutCampaign.objects.get(pk=pk)
        self.assertEqual((list(c.targets.order_by('id').values()), list(c.waves.values()), c.cohort_hash, c.wave_plan), before)
        self.assertEqual(c.total_candidates, 250)
        self.assertFalse(AgentJob.objects.exists())

    def test_concurrent_creation_and_restart(self):
        fixtures.AgentRolloutPreviewTests.setUp(self)
        actor = get_user_model().objects.create_user(username='concurrent-admin', is_staff=True, is_superuser=True)
        preview = build_agent_rollout_preview(self.release, now=self.now)
        data = {'cohort_schema': 1, 'expected_cohort_hash': preview['cohort_hash'],
                'wave_plan': [{'count': 1}], 'reason': 'Synthetic concurrency'}
        barrier, outcomes, pids = threading.Barrier(2), [], []
        release, now = self.release, self.now

        def create():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid(), current_database()')
                    pid, database = cursor.fetchone()
                    self.assertEqual(database, connection.settings_dict['NAME'])
                    pids.append(pid)
                barrier.wait(timeout=10)
                create_agent_rollout_campaign_from_preview(release, data, get_user_model().objects.get(pk=actor.pk), now=now)
                outcomes.append('created')
            except PolicyContractError as error:
                outcomes.append((str(error), error.status))
            except Exception as error:
                outcomes.append(type(error).__name__)
            finally:
                connections.close_all()

        threads = [threading.Thread(target=create) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(set(pids)), 2)
        self.assertCountEqual(outcomes, ['created', ('campaign_exists', 409)])
        connections.close_all()
        c = AgentRolloutCampaign.objects.get()
        self.assertEqual((c.state, c.targets.count(), c.waves.count(), c.cohort_hash), ('ready', 1, 1, preview['cohort_hash']))
        self.assertFalse(AgentJob.objects.exists())
