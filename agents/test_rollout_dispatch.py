import io
import json
import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection, connections, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext

from . import test_rollout_planning as fixtures
from .fleet_policy import PolicyContractError
from .lifecycle_jobs import active_lifecycle_job, agent_job_parameters, lock_lifecycle_endpoint
from .models import AgentJob, AgentMachine, AgentRolloutTarget, AuditEvent
from .rollout_campaigns import transition_wave
from .rollout_dispatch import dispatch_rollout_campaign
from .rollout_planning import build_rollout_dispatch_plan
from .services import AgentUpdateDecision, build_update_agent_job_payload


@override_settings(NIGHTOWL_ROLLOUT_ORCHESTRATOR_ENABLED=True, NIGHTOWL_AUTOMATIC_ROLLOUT_ENABLED=True)
class RolloutDispatchTests(TestCase):
    def setUp(self):
        fixtures.DispatchPlanningTests.setUp(self)

    def add_endpoints(self):
        AgentMachine.objects.bulk_create([AgentMachine(hostname=f'SYNTHETIC-{i}', machine_id=str(uuid.uuid4()),
            agent_token_hash=f'synthetic-{i}', agent_version=self.machine.agent_version,
            updater_version=self.machine.updater_version, update_channel=self.machine.update_channel,
            update_policy=self.machine.update_policy, auto_update_enabled=True, agent_lifecycle_status='installed',
            status='online', last_seen_at=self.now) for i in range(249)])

    def create(self, **options):
        c = fixtures.DispatchPlanningTests.create(self, **options)
        transition_wave(c.waves.first(), 'running', self.actor, 'Synthetic execution approval', now=self.now)
        return c

    def dispatch(self, c):
        return dispatch_rollout_campaign(c, now=self.now)

    def test_payload_atomic_link_and_idempotence(self):
        c = self.create()
        result = self.dispatch(c)
        self.assertEqual(result['created_count'], 1)
        target = c.targets.get()
        job = target.agent_job
        self.assertEqual((target.state, target.started_at, target.completed_at), ('queued', self.now, None))
        self.assertEqual((job.endpoint_id, job.agent_release_id, job.job_type, job.status),
                         (target.endpoint_id, c.release_id, 'update_agent', 'queued'))
        self.assertEqual((job.correlation_id, job.created_by, job.timeout_seconds, job.attempt),
                         (str(target.pk), 'rollout_orchestrator', 900, 1))
        expected = build_update_agent_job_payload(target.endpoint,
            AgentUpdateDecision(True, 'eligible_for_dispatch', target.endpoint, release=self.release,
                                channel=self.release.channel), source='rollout_campaign')
        self.assertEqual(agent_job_parameters(job), expected)
        self.assertEqual(job.payload['rollout_metadata']['target_id'], str(target.pk))
        self.assertEqual(job.payload['rollout_metadata']['cohort_hash'], c.cohort_hash)
        self.assertEqual(self.dispatch(c)['created_count'], 0)
        self.assertEqual(build_rollout_dispatch_plan(c, now=self.now)['targets'][0]['reason_code'], 'target_already_dispatched')
        AgentJob.objects.filter(pk=job.pk).update(status='completed')
        self.assertEqual(self.dispatch(c)['created_count'], 0)
        target.refresh_from_db()
        self.assertEqual(target.state, 'queued')  # No reconcile in 6C.3.

    def test_four_flag_combinations(self):
        c = self.create()
        for orchestrator, automatic in [(False, False), (True, False), (False, True)]:
            with override_settings(NIGHTOWL_ROLLOUT_ORCHESTRATOR_ENABLED=orchestrator, NIGHTOWL_AUTOMATIC_ROLLOUT_ENABLED=automatic):
                with self.assertRaisesMessage(PolicyContractError, 'rollout_orchestrator_disabled'):
                    self.dispatch(c)
                self.assertFalse(AgentJob.objects.exists())
        self.assertEqual(self.dispatch(c)['created_count'], 1)

    def test_running_wave_required(self):
        c = fixtures.DispatchPlanningTests.create(self)
        with self.assertRaisesMessage(PolicyContractError, 'wave_not_running'):
            self.dispatch(c)
        self.assertFalse(AgentJob.objects.exists())

    def test_revalidates_current_state(self):
        c = self.create()
        self.assertEqual(build_rollout_dispatch_plan(c, now=self.now)['dispatchable_count'], 1)
        AgentMachine.objects.filter(pk=self.machine.pk).update(update_paused=True)
        result = self.dispatch(c)
        self.assertEqual(result['created_count'], 0)
        self.assertEqual(result['plan']['targets'][0]['reason_code'], 'endpoint_paused')

    def test_failures_roll_back_job_link_and_audit(self):
        c = self.create()
        before = AuditEvent.objects.count()
        for operation in ('jobs', 'targets', 'audit'):
            manager = {'jobs': AgentJob.objects, 'targets': AgentRolloutTarget.objects, 'audit': AuditEvent.objects}[operation]
            method = {'jobs': 'bulk_create', 'targets': 'bulk_update', 'audit': 'create'}[operation]
            with patch.object(manager, method, side_effect=RuntimeError('synthetic failure')):
                with self.assertRaisesMessage(RuntimeError, 'synthetic failure'):
                    self.dispatch(c)
            target = c.targets.get()
            self.assertEqual((target.state, target.agent_job_id, target.started_at), ('eligible', None, None))
            self.assertFalse(AgentJob.objects.exists())
            self.assertEqual(AuditEvent.objects.count(), before)

    def test_runtime_guards_and_terminal_planner(self):
        c = self.create()
        self.dispatch(c)
        target = c.targets.get()
        for changes in ({'state': 'eligible', 'agent_job': None, 'started_at': None},
                        {'endpoint_hostname_snapshot': 'changed'}, {'agent_job_id': None},
                        {'wave_id': None}, {'endpoint_id': None}):
            with self.assertRaises(IntegrityError), transaction.atomic():
                AgentRolloutTarget.objects.filter(pk=target.pk).update(**changes)
        AgentRolloutTarget.objects.filter(pk=target.pk).update(state='succeeded', completed_at=self.now)
        self.assertEqual(build_rollout_dispatch_plan(c, now=self.now)['targets'][0]['reason_code'], 'target_runtime_terminal')
        with self.assertRaises(IntegrityError), transaction.atomic():
            AgentRolloutTarget.objects.filter(pk=target.pk).update(state='running', completed_at=None)

    def test_command_requires_explicit_execute_and_campaign(self):
        c = self.create()
        with self.assertRaisesMessage(CommandError, 'EXPLICIT_CAMPAIGN_REQUIRED'):
            call_command('process_agent_rollouts', execute=True)
        with self.assertRaisesMessage(CommandError, 'ROLLOUT_MODE_CONFLICT'):
            call_command('process_agent_rollouts', execute=True, plan_only=True, campaign=str(c.pk))
        output = io.StringIO()
        with patch('agents.management.commands.process_agent_rollouts.timezone.now', return_value=self.now):
            call_command('process_agent_rollouts', execute=True, campaign=str(c.pk), stdout=output)
        self.assertEqual(json.loads(output.getvalue())['created_count'], 1)

    def test_250_targets_capacity_queries_and_next_wave(self):
        self.add_endpoints()
        c = self.create(concurrency_limit=3, wave_plan=[{'count': 5}, {'remaining': True}])
        for target in c.waves.first().targets.order_by('pk')[:2]:
            AgentJob.objects.create(endpoint_id=target.endpoint_id, job_type='repair_agent', payload={})
        with CaptureQueriesContext(connection) as queries:
            result = self.dispatch(c)
        self.assertEqual(result['created_count'], 1)
        self.assertEqual(self.dispatch(c)['created_count'], 0)
        self.assertEqual(AgentJob.objects.count(), 3)
        self.assertFalse(c.waves.last().targets.exclude(state='eligible').exists())
        self.assertLess(len(queries), 40)
        print(f'Synthetic campaign dispatch: 250 targets, queries={len(queries)}, total lifecycle jobs=3')

    def test_historical_duplicate_jobs_remain_intact(self):
        c = self.create()
        for _ in range(2):
            AgentJob.objects.create(endpoint=self.machine, job_type='update_agent', status='sent', payload={})
        before = list(AgentJob.objects.values())
        self.assertEqual(self.dispatch(c)['created_count'], 0)
        self.assertEqual(before, list(AgentJob.objects.values()))

    def test_pull_strips_backend_metadata_but_preserves_update_contract(self):
        from .models import hash_agent_token
        c = self.create()
        self.dispatch(c)
        AgentMachine.objects.filter(pk=self.machine.pk).update(agent_token_hash=hash_agent_token('synthetic-dispatch-auth'))
        with patch('agents.views.timezone.now', return_value=self.now):
            response = self.client.get('/api/agent/jobs/pull/', HTTP_AUTHORIZATION='Bearer synthetic-dispatch-auth')
        self.assertEqual(response.status_code, 200)
        item = response.json()['jobs'][0]
        job = AgentJob.objects.get(pk=item['id'])
        self.assertEqual(item['parameters'], agent_job_parameters(job))
        self.assertNotIn('rollout_metadata', item['payload'])
        self.assertEqual(job.status, 'sent')
        self.assertIn('rollout_metadata', job.payload)


@unittest.skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL row locks')
@override_settings(NIGHTOWL_ROLLOUT_ORCHESTRATOR_ENABLED=True, NIGHTOWL_AUTOMATIC_ROLLOUT_ENABLED=True)
class PostgreSQLDispatchTests(TransactionTestCase):
    setUp = RolloutDispatchTests.setUp
    create = RolloutDispatchTests.create
    dispatch = RolloutDispatchTests.dispatch
    add_endpoints = RolloutDispatchTests.add_endpoints

    def test_concurrent_dispatch_repeated_and_restart(self):
        self.add_endpoints()
        c = self.create(concurrency_limit=3)
        for _ in range(3):
            barrier = threading.Barrier(2)
            def run():
                connections.close_all()
                try:
                    barrier.wait(timeout=10)
                    return dispatch_rollout_campaign(c.pk, now=self.now)['created_count']
                finally:
                    connections.close_all()
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: run(), range(2)))
            self.assertLessEqual(sum(results), 3)
            self.assertEqual(AgentJob.objects.count(), 3)
        connections.close_all()
        self.assertEqual(self.dispatch(c)['created_count'], 0)
        self.assertEqual(c.targets.filter(state='queued', agent_job__isnull=False).count(), 3)

    def test_manual_writer_and_campaign_both_lock_orders(self):
        from dashboard.views import endpoint_job_create
        c = self.create()
        for manual_first in (True, False):
            acquired, release = threading.Event(), threading.Event()
            def manual():
                connections.close_all()
                try:
                    with transaction.atomic():
                        endpoint = lock_lifecycle_endpoint(self.machine.pk)
                        if manual_first:
                            acquired.set()
                            release.wait(timeout=10)
                        request = RequestFactory().post('/synthetic-job/', {'job_type': 'update_agent', 'release_id': str(self.release.pk)})
                        request.user = self.actor
                        response = endpoint_job_create(request, endpoint.pk)
                        return response.status_code
                finally:
                    connections.close_all()
            if manual_first:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    first = pool.submit(manual)
                    self.assertTrue(acquired.wait(timeout=10))
                    second = pool.submit(dispatch_rollout_campaign, c.pk, now=self.now)
                    time.sleep(0.1)
                    self.assertFalse(second.done())
                    release.set()
                    self.assertEqual(first.result(timeout=20), 201)
                    self.assertEqual(second.result(timeout=20)['created_count'], 0)
                AgentJob.objects.all().delete()
            else:
                original = AuditEvent.objects.create
                def hold_audit(**kwargs):
                    if kwargs.get('event_type') == 'rollout.dispatch_created':
                        acquired.set()
                        release.wait(timeout=10)
                    return original(**kwargs)
                with patch.object(AuditEvent.objects, 'create', side_effect=hold_audit), ThreadPoolExecutor(max_workers=2) as pool:
                    first = pool.submit(dispatch_rollout_campaign, c.pk, now=self.now)
                    self.assertTrue(acquired.wait(timeout=10))
                    second = pool.submit(manual)
                    time.sleep(0.1)
                    self.assertFalse(second.done())
                    release.set()
                    self.assertEqual(first.result(timeout=20)['created_count'], 1)
                    self.assertEqual(second.result(timeout=20), 409)
            self.assertEqual(AgentJob.objects.count(), 0 if manual_first else 1)

    def test_migration_reverse_before_and_after_dispatch(self):
        c = self.create()
        before = list(AgentMachine.objects.values())
        MigrationExecutor(connection).migrate([('agents', '0031_agentrolloutcampaign_agentrolloutwave_and_more')])
        MigrationExecutor(connection).migrate([('agents', '0032_rollout_target_execution')])
        MigrationExecutor(connection).migrate([('agents', '0033_agentrolloutcampaign_auto_pause_policy_and_more')])
        self.assertEqual(before, list(AgentMachine.objects.values()))
        self.dispatch(c)
        with self.assertRaisesMessage(RuntimeError, 'ROLLBACK_UNSAFE'):
            MigrationExecutor(connection).migrate([('agents', '0031_agentrolloutcampaign_agentrolloutwave_and_more')])
        MigrationExecutor(connection).migrate([('agents', '0033_agentrolloutcampaign_auto_pause_policy_and_more')])
        self.assertEqual(c.targets.get().state, 'queued')
