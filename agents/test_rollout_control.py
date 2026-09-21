import io
import json
import copy
import threading
import unittest
import uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, connections, DatabaseError
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext

from . import test_rollout_dispatch as fixtures
from .fleet_policy import PolicyContractError
from .models import AgentJob, AgentRolloutCampaign, AuditEvent
from .rollout_campaigns import create_agent_rollout_campaign_from_preview, transition_wave
from .rollout_control import control_rollout_campaign
from .rollout_planning import build_rollout_dispatch_plan
from .rollout_runner import run_rollout_round, rollout_runner_lock
from .services import build_agent_rollout_preview


@override_settings(NIGHTOWL_ROLLOUT_ORCHESTRATOR_ENABLED=True, NIGHTOWL_AUTOMATIC_ROLLOUT_ENABLED=True)
class RolloutControlTests(TestCase):
    setUp = fixtures.RolloutDispatchTests.setUp
    add_endpoints = fixtures.RolloutDispatchTests.add_endpoints

    def create(self, **options):
        preview = build_agent_rollout_preview(self.release, now=self.now)
        return create_agent_rollout_campaign_from_preview(self.release,
            {'cohort_schema': 1, 'expected_cohort_hash': preview['cohort_hash'], 'reason': 'Synthetic control',
             'wave_plan': [{'remaining': True}], **options}, self.actor, now=self.now)

    def act(self, c, action, wave=None, **extra):
        c.refresh_from_db()
        if wave:
            wave.refresh_from_db()
        return control_rollout_campaign(c, {'action': action, 'reason': 'Synthetic administration',
            'expected_state': c.state, **({'expected_wave_state': wave.state} if wave else {}), **extra},
            self.actor, wave_id=wave.pk if wave else None, now=self.now)

    def running(self):
        c = self.create()
        self.act(c, 'start')
        wave = c.waves.first()
        self.act(c, 'prepare', wave)
        self.act(c, 'start', wave)
        return c, wave

    def test_approve_off_start_dual_flags_and_no_jobs(self):
        c = self.create(ready=False)
        with override_settings(NIGHTOWL_ROLLOUT_ORCHESTRATOR_ENABLED=False, NIGHTOWL_AUTOMATIC_ROLLOUT_ENABLED=False):
            self.act(c, 'approve')
        for a, b in [(False, False), (True, False), (False, True)]:
            with override_settings(NIGHTOWL_ROLLOUT_ORCHESTRATOR_ENABLED=a, NIGHTOWL_AUTOMATIC_ROLLOUT_ENABLED=b):
                with self.assertRaisesMessage(PolicyContractError, 'disabled'):
                    self.act(c, 'start')
                with self.assertRaisesMessage(PolicyContractError, 'disabled'):
                    run_rollout_round(now=self.now)
        self.act(c, 'start')
        self.assertEqual(c.waves.get().state, 'pending')
        self.assertFalse(AgentJob.objects.exists())

    def test_pause_abort_preserve_all_job_states_and_targets(self):
        self.add_endpoints()
        c, wave = self.running()
        from .rollout_dispatch import dispatch_rollout_campaign
        dispatch_rollout_campaign(c, now=self.now)
        job = AgentJob.objects.get()
        for state in ['queued', 'sent', 'running']:
            AgentJob.objects.filter(pk=job.pk).update(status=state)
            jobs = list(AgentJob.objects.values())
            targets = list(c.targets.values())
            with override_settings(NIGHTOWL_ROLLOUT_ORCHESTRATOR_ENABLED=False):
                self.act(c, 'pause')
            self.assertFalse(build_rollout_dispatch_plan(c, now=self.now)['dispatchable_count'])
            self.assertEqual(jobs, list(AgentJob.objects.values()))
            self.assertEqual(targets, list(c.targets.values()))
            self.act(c, 'resume')
        with override_settings(NIGHTOWL_ROLLOUT_ORCHESTRATOR_ENABLED=False):
            self.act(c, 'abort')
        self.assertEqual(jobs, list(AgentJob.objects.values()))
        self.assertEqual(targets, list(c.targets.values()))
        self.assertFalse(c.waves.exclude(state='cancelled').exists())
        audit = AuditEvent.objects.filter(event_type='rollout.administrative_action', metadata__action='abort').get()
        self.assertEqual(audit.metadata['job_count'], 1)
        self.assertNotIn('payload', audit.metadata)

    def test_stale_state_revision_reason_and_forbidden_actions(self):
        c = self.create()
        for extra in [{'expected_state': 'draft'}, {'expected_updated_at': 'stale'}, {'reason': ''}]:
            with self.assertRaises(PolicyContractError):
                self.act(c, 'start', **extra)
        for forbidden in ['complete', 'retry']:
            with self.assertRaisesMessage(PolicyContractError, 'unsupported_control_action'):
                control_rollout_campaign(c, {'action': forbidden, 'reason': 'Synthetic administration',
                    'expected_state': c.state}, self.actor, now=self.now)
        self.act(c, 'start')
        for action in ['observing', 'complete', 'advance']:
            with self.assertRaisesMessage(PolicyContractError, 'unsupported_control_action'):
                self.act(c, action, c.waves.get())
        with self.assertRaises(PolicyContractError):
            self.act(c, 'prepare_next_wave', c.waves.get())

    def test_wave_pause_resume_flags_and_predecessors(self):
        c, wave = self.running()
        with override_settings(NIGHTOWL_AUTOMATIC_ROLLOUT_ENABLED=False):
            self.act(c, 'pause', wave)
            with self.assertRaisesMessage(PolicyContractError, 'disabled'):
                self.act(c, 'resume', wave)
        self.act(c, 'resume', wave)
        self.assertFalse(AgentJob.objects.exists())

    def test_prepare_next_wave_only_after_completed_anchor(self):
        self.add_endpoints()
        c = self.create(wave_plan=[{'count': 5}, {'count': 5}, {'remaining': True}])
        self.act(c, 'start')
        first = c.waves.get(sequence=1)
        self.act(c, 'prepare', first)
        self.act(c, 'start', first)
        transition_wave(first, 'observing', self.actor, 'Synthetic reconcile boundary', now=self.now)
        transition_wave(first, 'completed', self.actor, 'Synthetic reconcile boundary', now=self.now)
        AgentRolloutCampaign.objects.filter(pk=c.pk).update(current_wave=None)
        from .rollout_governance import build_rollout_advance_preview
        preview = build_rollout_advance_preview(c, now=self.now)
        result = self.act(c, 'prepare_next_wave', first,
                          expected_advance_schema=preview['advance_schema'],
                          expected_advance_hash=preview['advance_hash'])
        self.assertEqual(result['wave_state'], 'ready')
        self.assertEqual(c.waves.get(sequence=2).state, 'ready')
        self.assertEqual(c.waves.get(sequence=3).state, 'pending')
        self.assertFalse(AgentJob.objects.exists())

    def test_authorization_csrf_and_change_without_add(self):
        c = self.create()
        url = f'/api/agent/rollout-campaigns/{c.pk}/actions/'
        data = {'action': 'start', 'reason': 'Synthetic', 'expected_state': 'ready'}
        self.assertEqual(self.client.post(url, data, content_type='application/json').status_code, 403)
        user = get_user_model().objects.create_user(username='control-only', is_staff=True)
        user.user_permissions.add(*Permission.objects.filter(codename__in=['change_agentrolloutcampaign', 'view_agentrolloutcampaign']))
        self.assertFalse(user.has_perm('agents.add_agentrolloutcampaign'))
        client = Client(enforce_csrf_checks=True)
        client.force_login(user)
        self.assertEqual(client.post(url, data, content_type='application/json').status_code, 403)
        self.assertEqual(client.get('/agent-rollouts/').status_code, 200)
        response = client.post(url, data, content_type='application/json', HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.post(url, data, content_type='application/json', HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value).status_code, 409)

    def test_audit_failure_rolls_back_admin_transition(self):
        c = self.create()
        before = AuditEvent.objects.count()
        with patch.object(AuditEvent.objects, 'create', side_effect=RuntimeError('synthetic audit failure')):
            with self.assertRaises(RuntimeError):
                self.act(c, 'start')
        c.refresh_from_db()
        self.assertEqual(c.state, 'ready')
        self.assertEqual(before, AuditEvent.objects.count())

    def test_overlapping_running_campaign_is_incompatible(self):
        c = self.create()
        self.act(c, 'start')
        second_release = copy.copy(self.release)
        second_release.pk = None
        second_release.id = None
        second_release.version = '0.1.1.0-rc40'
        second_release.save()
        self.release = second_release
        second = self.create()
        with self.assertRaisesMessage(PolicyContractError, 'campaign_incompatible'):
            self.act(second, 'start')
        self.assertFalse(AgentJob.objects.exists())

    def test_detail_250_targets_constant_queries_and_sanitized(self):
        self.add_endpoints()
        c = self.create(wave_plan=[{'count': 5}, {'count': 5}, {'remaining': True}])
        self.client.force_login(self.actor)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(f'/api/agent/rollout-campaigns/{c.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['targets']), 250)
        self.assertIsNotNone(response.json()['approved_at'])
        self.assertLess(len(queries), 25)
        self.assertNotIn('agent_token', response.content.decode())
        print(f'Control detail: 250 targets, queries={len(queries)}')

    def test_command_modes_and_readiness_are_read_only(self):
        for options in [{}, {'plan_only': True, 'execute': True}, {'execute': True, 'run_once': True}, {'plan_only': True, 'run_once': True}, {'run_once': True, 'campaign': 'synthetic'}]:
            with self.assertRaises(CommandError):
                call_command('process_agent_rollouts', **options, stdout=io.StringIO())
        before = AuditEvent.objects.count(), AgentJob.objects.count()
        output = io.StringIO()
        call_command('check_rollout_migration_readiness', stdout=output)
        self.assertIn('eligible_without_wave=0', output.getvalue())
        self.assertEqual(before, (AuditEvent.objects.count(), AgentJob.objects.count()))
        if connection.vendor != 'postgresql':
            with self.assertRaisesMessage(PolicyContractError, 'requires_postgresql'):
                run_rollout_round(now=self.now)

    def test_ui_contract_exposes_only_supported_actions(self):
        root = Path(__file__).resolve().parent.parent
        template = (root / 'templates/dashboard/rollout_operations.html').read_text(encoding='utf-8')
        script = (root / 'static/js/rollout_operations.js').read_text(encoding='utf-8')
        self.assertIn('data-can-change', template)
        self.assertIn('{% csrf_token %}', template)
        self.assertIn('expected_state', script)
        self.assertIn('expected_wave_state', script)
        self.assertIn('Execucao desabilitada pelos flags operacionais', script)
        self.assertNotIn('innerHTML', script)
        for forbidden in ['complete_campaign', 'complete_wave', 'force_success', 'force_advance', 'retry_target']:
            self.assertNotIn(forbidden, script)


@unittest.skipUnless(connection.vendor == 'postgresql', 'Requires real PostgreSQL session/row locks')
@override_settings(NIGHTOWL_ROLLOUT_ORCHESTRATOR_ENABLED=True, NIGHTOWL_AUTOMATIC_ROLLOUT_ENABLED=True)
class PostgreSQLControlTests(TransactionTestCase):
    setUp = RolloutControlTests.setUp
    create = RolloutControlTests.create
    act = RolloutControlTests.act
    running = RolloutControlTests.running
    add_endpoints = RolloutControlTests.add_endpoints

    def test_admin_concurrent_start_pause_abort_resume_abort(self):
        for state, actions in [('ready', ['start', 'start']), ('running', ['pause', 'abort']), ('paused', ['resume', 'abort'])]:
            c = self.create()
            if state in ['running', 'paused']:
                self.act(c, 'start')
            if state == 'paused':
                self.act(c, 'pause')
            barrier = threading.Barrier(2)
            def attempt(action):
                connections.close_all()
                try:
                    barrier.wait(timeout=10)
                    control_rollout_campaign(c.pk, {'action': action, 'reason': 'Synthetic concurrency', 'expected_state': state}, self.actor, now=self.now)
                    return 'changed'
                except PolicyContractError as error:
                    self.assertEqual(error.status, 409)
                    return 'conflict'
                finally:
                    connections.close_all()
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(attempt, actions))
            self.assertCountEqual(results, ['changed', 'conflict'])
            c.refresh_from_db()
            if c.state != 'aborted':
                self.act(c, 'abort')

    def test_three_waves_rounds_pause_resume_abort_and_capacity(self):
        self.add_endpoints()
        c = self.create(concurrency_limit=3, wave_plan=[{'count': 5}, {'count': 5}, {'remaining': True}])
        with override_settings(NIGHTOWL_AUTOMATIC_ROLLOUT_ENABLED=False):
            with self.assertRaises(PolicyContractError):
                self.act(c, 'start')
        self.act(c, 'start')
        wave = c.waves.first()
        self.act(c, 'prepare', wave)
        self.act(c, 'start', wave)
        output = io.StringIO()
        with CaptureQueriesContext(connection) as queries:
            with patch('agents.rollout_runner.timezone.now', return_value=self.now):
                call_command('process_agent_rollouts', run_once=True, stdout=output)
        result = json.loads(output.getvalue())
        self.assertEqual(result['campaigns'][0]['created_count'], 3)
        print(f'Periodic round: 250 targets, queries={len(queries)}')
        self.assertLess(len(queries), 45)
        self.assertEqual(run_rollout_round(now=self.now)['campaigns'][0]['created_count'], 0)
        jobs, targets = list(AgentJob.objects.values()), list(c.targets.values())
        self.act(c, 'pause')
        self.assertEqual(run_rollout_round(now=self.now)['campaigns'], [])
        self.act(c, 'resume')
        self.act(c, 'pause', wave)
        self.assertEqual(run_rollout_round(now=self.now)['campaigns'], [])
        self.act(c, 'resume', wave)
        self.assertEqual(run_rollout_round(now=self.now)['campaigns'][0]['created_count'], 0)
        connections.close_all()  # New session / process restart does not duplicate dispatch.
        self.assertEqual(run_rollout_round(now=self.now)['campaigns'][0]['created_count'], 0)
        self.act(c, 'abort')
        self.assertEqual(run_rollout_round(now=self.now)['campaigns'], [])
        self.assertEqual(jobs, list(AgentJob.objects.values()))
        self.assertEqual(targets, list(c.targets.values()))

    def test_two_runners_and_lock_release_after_errors_and_restart(self):
        c, wave = self.running()
        acquired, release = threading.Event(), threading.Event()
        def hold():
            connections.close_all()
            try:
                with rollout_runner_lock() as locked:
                    self.assertTrue(locked)
                    acquired.set()
                    release.wait(timeout=15)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(hold)
            try:
                self.assertTrue(acquired.wait(timeout=10))
                self.assertEqual(run_rollout_round(now=self.now)['status'], 'already_running')
                self.assertFalse(AgentJob.objects.exists())
            finally:
                release.set()
            future.result(timeout=20)
        for error in [DatabaseError('synthetic database failure'), RuntimeError('synthetic unexpected failure')]:
            with patch('agents.rollout_runner.dispatch_rollout_campaign', side_effect=error):
                with self.assertRaises(type(error)):
                    run_rollout_round(now=self.now)
            with rollout_runner_lock() as locked:
                self.assertTrue(locked)
        connections.close_all()
        self.assertEqual(run_rollout_round(now=self.now)['campaigns'][0]['created_count'], 1)
        self.assertEqual(run_rollout_round(now=self.now)['campaigns'][0]['created_count'], 0)

    def test_known_error_isolated_and_empty_round_no_audit(self):
        from .rollout_dispatch import dispatch_rollout_campaign
        c, wave = self.running()
        second_release = copy.copy(self.release)
        second_release.pk = None
        second_release.id = None
        second_release.version = '0.1.1.0-rc40'
        second_release.save()
        from .models import AgentMachine
        AgentMachine.objects.create(hostname='SYNTHETIC-ISOLATED', machine_id=str(uuid.uuid4()),
            agent_token_hash='synthetic-isolated-token', agent_version='0.1.1.0-rc38',
            updater_version='0.1.1.0-rc38', agent_lifecycle_status='installed', status='online',
            last_seen_at=self.now, update_channel='development', update_policy='automatic',
            auto_update_enabled=True)
        AgentMachine.objects.filter(pk=self.machine.pk).update(is_active=False)
        self.release = second_release
        second, second_wave = self.running()
        before = AuditEvent.objects.count()
        def mixed(pk, *, now):
            if pk == c.pk:
                raise PolicyContractError('synthetic_known_blocker', status=409)
            return dispatch_rollout_campaign(pk, now=now)
        with patch('agents.rollout_runner.dispatch_rollout_campaign', side_effect=mixed):
            summaries = run_rollout_round(now=self.now)['campaigns']
        self.assertEqual([item.get('status') for item in summaries], ['blocked', None])
        self.assertEqual(summaries[1]['created_count'], 1)
        self.assertEqual(before + 1, AuditEvent.objects.count())
        self.act(c, 'abort')
        self.act(second, 'abort')
        before = AuditEvent.objects.count()
        self.assertEqual(run_rollout_round(now=self.now)['campaigns'], [])
        self.assertEqual(before, AuditEvent.objects.count())
