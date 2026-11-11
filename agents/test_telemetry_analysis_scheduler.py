import uuid
import json
from datetime import datetime, timedelta
from unittest.mock import patch

from django.db import OperationalError
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from django.urls import reverse

from .models import (AgentJob, AgentMachine, AutomationSchedulerHealth, EndpointPerformanceSample,
                     AuditEvent, PersistedTelemetryAnalysis, PersistedTelemetryAnalysisSchedule)
from .automation import scheduler_round
from .telemetry_continuous import persist_windows
from .telemetry_persisted_analysis import scheduled_analysis_round
from .test_telemetry_persisted_analysis import window


SCHEDULER_SETTINGS = {
    'NIGHTOWL_PERSISTED_TELEMETRY_ANALYSIS_SCHEDULER_ENABLED': True,
    'NIGHTOWL_PERSISTED_TELEMETRY_ANALYSIS_SCHEDULER_DRY_RUN': False,
    'NIGHTOWL_PERSISTED_TELEMETRY_ANALYSIS_FIRST_CAPTURE_POLICY': 'immediate',
    'NIGHTOWL_TELEMETRY_RETENTION_DAYS': {60: 7, 300: 30, 3600: 90},
}


class PersistedTelemetryAnalysisSchedulerTests(TestCase):
    def setUp(self):
        self.now = timezone.now().replace(second=0, microsecond=0)
        self.machine, _ = AgentMachine.create_with_token(
            hostname='SYNTHETIC-SCHEDULE', machine_id=str(uuid.uuid4()), agent_version='test')
        self.machine.last_seen_at = self.now
        self.machine.save(update_fields=['last_seen_at'])
        data = window(self.now - timedelta(minutes=5), quality={
            'cpu': 'available', 'memory': 'available'})
        persist_windows(self.machine, [data])

    @override_settings(**{**SCHEDULER_SETTINGS,
                          'NIGHTOWL_PERSISTED_TELEMETRY_ANALYSIS_FIRST_CAPTURE_POLICY': 'unconfigured'})
    def test_unconfigured_first_capture_policy_blocks_apply_and_dry_run_is_read_only(self):
        preview = scheduled_analysis_round(now=self.now, dry_run=True)
        self.assertEqual(preview['results'][0]['reason'], 'first_capture_policy_unconfigured')
        self.assertFalse(PersistedTelemetryAnalysisSchedule.objects.exists())
        blocked = scheduled_analysis_round(now=self.now, dry_run=False)
        self.assertEqual(blocked['reason'], 'first_capture_policy_unconfigured')
        self.assertFalse(PersistedTelemetryAnalysis.objects.exists())

    @override_settings(**{**SCHEDULER_SETTINGS, 'NIGHTOWL_JOB_AUTOMATIONS_ENABLED': True})
    def test_existing_scheduler_dry_run_does_not_run_jobs_or_persist_analysis(self):
        result = scheduler_round(now=self.now, limit=10, analysis_dry_run=True)
        self.assertIn('telemetry_analysis', result)
        self.assertEqual(AgentJob.objects.count(), 0)
        self.assertFalse(PersistedTelemetryAnalysisSchedule.objects.exists())
        self.assertFalse(PersistedTelemetryAnalysis.objects.exists())

    @override_settings(**SCHEDULER_SETTINGS)
    def test_immediate_capture_persists_once_and_sets_fifteen_day_next_run(self):
        first = scheduled_analysis_round(now=self.now, limit=10)
        self.assertEqual(first['processed'], 1)
        schedule = PersistedTelemetryAnalysisSchedule.objects.get(endpoint=self.machine)
        self.assertEqual(schedule.next_run_at, self.now + timedelta(days=15))
        self.assertEqual(schedule.last_attempt_state, 'succeeded')
        self.assertEqual(len(schedule.attempt_history), 1)
        self.assertEqual(PersistedTelemetryAnalysis.objects.get(endpoint=self.machine).analysis_count, 1)
        health = AutomationSchedulerHealth.objects.get(key='telemetry_analysis')
        self.assertEqual(health.last_attempt_status, 'completed')
        self.assertEqual(health.processed_endpoints, 1)
        self.assertEqual(health.blocked_endpoints, 0)
        self.assertIsNotNone(health.last_duration_ms)
        self.assertIsNotNone(health.last_success_at)
        replay = scheduled_analysis_round(now=self.now + timedelta(days=1), limit=10)
        self.assertEqual(replay['processed'], 0)
        self.assertEqual(PersistedTelemetryAnalysis.objects.get(endpoint=self.machine).analysis_count, 1)

    @override_settings(**{**SCHEDULER_SETTINGS, 'NIGHTOWL_JOB_AUTOMATIONS_ENABLED': False})
    def test_analysis_only_flag_runs_backend_analysis_without_job_automation(self):
        result = scheduler_round(now=self.now, limit=20)
        self.assertEqual(result['processed'], 1)
        self.assertEqual(result['telemetry_analysis']['processed'], 1)
        self.assertEqual(AgentJob.objects.count(), 0)
        self.assertEqual(PersistedTelemetryAnalysis.objects.get(endpoint=self.machine).analysis_count, 1)

    @override_settings(NIGHTOWL_JOB_AUTOMATIONS_ENABLED=True,
                       NIGHTOWL_PERSISTED_TELEMETRY_ANALYSIS_SCHEDULER_ENABLED=False,
                       NIGHTOWL_PERSISTED_TELEMETRY_ANALYSIS_SCHEDULER_DRY_RUN=False)
    def test_jobs_only_flag_does_not_invoke_telemetry_scheduler(self):
        with patch('agents.telemetry_persisted_analysis.scheduled_analysis_round') as analysis_round:
            result = scheduler_round(now=self.now, limit=20)
        self.assertNotIn('telemetry_analysis', result)
        analysis_round.assert_not_called()
        self.assertFalse(PersistedTelemetryAnalysisSchedule.objects.exists())

    @override_settings(NIGHTOWL_JOB_AUTOMATIONS_ENABLED=False,
                       NIGHTOWL_PERSISTED_TELEMETRY_ANALYSIS_SCHEDULER_ENABLED=False,
                       NIGHTOWL_PERSISTED_TELEMETRY_ANALYSIS_SCHEDULER_DRY_RUN=False)
    def test_both_scheduler_flags_off_rejects_normal_round(self):
        with self.assertRaisesMessage(ValueError, 'scheduler_disabled'):
            scheduler_round(now=self.now, limit=20)

    @override_settings(NIGHTOWL_JOB_AUTOMATIONS_ENABLED=False,
                       NIGHTOWL_PERSISTED_TELEMETRY_ANALYSIS_SCHEDULER_ENABLED=False,
                       NIGHTOWL_PERSISTED_TELEMETRY_ANALYSIS_SCHEDULER_DRY_RUN=False,
                       NIGHTOWL_PERSISTED_TELEMETRY_ANALYSIS_FIRST_CAPTURE_POLICY='immediate')
    def test_explicit_analysis_dry_run_is_read_only_even_when_both_flags_are_off(self):
        result = scheduler_round(now=self.now, limit=20, analysis_dry_run=True)
        self.assertEqual(result['telemetry_analysis']['mode'], 'dry_run')
        self.assertEqual(result['processed'], 0)
        self.assertEqual(result['failed'], 0)
        self.assertFalse(AgentJob.objects.exists())
        self.assertFalse(PersistedTelemetryAnalysisSchedule.objects.exists())
        self.assertFalse(PersistedTelemetryAnalysis.objects.exists())
        self.assertFalse(AutomationSchedulerHealth.objects.exists())

    @override_settings(**SCHEDULER_SETTINGS)
    def test_round_limit_bounds_initialization_plus_due_analysis(self):
        for index in range(4):
            machine, _ = AgentMachine.create_with_token(
                hostname=f'SYNTHETIC-BOUNDED-{index}', machine_id=str(uuid.uuid4()), agent_version='test')
            data = window(self.now - timedelta(minutes=5))
            persist_windows(machine, [data])
        result = scheduled_analysis_round(now=self.now, limit=2)
        self.assertLessEqual(result['initialized'] + result['due'], 2)
        self.assertEqual(PersistedTelemetryAnalysisSchedule.objects.count(), 1)

    @override_settings(**SCHEDULER_SETTINGS)
    def test_seven_day_source_retention_expires_cycle_without_rebuilding_rollups(self):
        scheduled_analysis_round(now=self.now, limit=10, dry_run=True)
        PersistedTelemetryAnalysisSchedule.objects.create(
            endpoint=self.machine, machine_id_snapshot=self.machine.machine_id,
            first_scheduled_at=self.now, scheduled_for=self.now, next_run_at=self.now)
        later = self.now + timedelta(days=8)
        result = scheduled_analysis_round(now=later, limit=10)
        schedule = PersistedTelemetryAnalysisSchedule.objects.get(endpoint=self.machine)
        self.assertEqual(result['failed'], 1)
        self.assertEqual(schedule.last_attempt_reason, 'seven_day_source_retention_elapsed')
        self.assertIsNone(schedule.scheduled_for)
        self.assertEqual(schedule.next_run_at, self.now + timedelta(days=15))
        self.assertFalse(PersistedTelemetryAnalysis.objects.exists())

    @override_settings(**{**SCHEDULER_SETTINGS,
                         'NIGHTOWL_PERSISTED_TELEMETRY_ANALYSIS_FIRST_CAPTURE_POLICY': 'staggered_24h'})
    def test_staggering_is_deterministic_and_does_not_exceed_one_day(self):
        schedule_result = scheduled_analysis_round(now=self.now, limit=10, dry_run=True)
        next_run = schedule_result['results'][0]['next_run_at']
        self.assertIsNotNone(next_run)
        self.assertLessEqual(datetime.fromisoformat(next_run) - self.now, timedelta(days=1))
        self.assertFalse(PersistedTelemetryAnalysisSchedule.objects.exists())

    @override_settings(**{**SCHEDULER_SETTINGS,
                         'NIGHTOWL_PERSISTED_TELEMETRY_ANALYSIS_FIRST_CAPTURE_POLICY': 'staggered_24h'})
    def test_fleet_phase_spreads_deterministically_across_the_first_day(self):
        for index in range(1, 20):
            machine, _ = AgentMachine.create_with_token(
                hostname=f'SYNTHETIC-SCHEDULE-{index}', machine_id=str(uuid.uuid4()), agent_version='test')
            data = window(self.now - timedelta(minutes=5))
            persist_windows(machine, [data])
        first = scheduled_analysis_round(now=self.now, limit=100, dry_run=True)
        second = scheduled_analysis_round(now=self.now, limit=100, dry_run=True)
        phases = [item['next_run_at'] for item in first['results'] if item['next_run_at']]
        self.assertGreater(len(set(phases)), 10)
        self.assertEqual(phases, [item['next_run_at'] for item in second['results'] if item['next_run_at']])

    @override_settings(**SCHEDULER_SETTINGS)
    def test_offline_endpoint_retries_then_recovers_without_advancing_the_cycle(self):
        self.machine.last_seen_at = self.now - timedelta(minutes=16)
        self.machine.save(update_fields=['last_seen_at'])
        scheduled_analysis_round(now=self.now, limit=10)
        schedule = PersistedTelemetryAnalysisSchedule.objects.get(endpoint=self.machine)
        self.assertEqual(schedule.last_attempt_state, 'endpoint_offline')
        self.assertEqual(schedule.retry_at, self.now + timedelta(minutes=15))
        original_due = schedule.scheduled_for

        self.machine.last_seen_at = self.now + timedelta(minutes=15)
        self.machine.save(update_fields=['last_seen_at'])
        recovered_at = self.now + timedelta(minutes=15)
        result = scheduled_analysis_round(now=recovered_at, limit=10)
        self.assertEqual(result['processed'], 1)
        schedule.refresh_from_db()
        self.assertEqual(schedule.next_run_at, original_due + timedelta(days=15))
        self.assertEqual([entry['state'] for entry in schedule.attempt_history],
                         ['endpoint_offline', 'succeeded'])

    @override_settings(**SCHEDULER_SETTINGS)
    def test_offline_retry_backoff_is_bounded_and_eventually_advances_period(self):
        self.machine.last_seen_at = self.now - timedelta(days=1)
        self.machine.save(update_fields=['last_seen_at'])
        scheduled_analysis_round(now=self.now, limit=10)
        schedule = PersistedTelemetryAnalysisSchedule.objects.get(endpoint=self.machine)
        for _ in range(5):
            scheduled_analysis_round(now=schedule.retry_at, limit=10)
            schedule.refresh_from_db()
        self.assertEqual(schedule.last_attempt_state, 'retry_exhausted')
        self.assertEqual(schedule.retry_count, 0)
        self.assertIsNone(schedule.retry_at)
        self.assertGreater(schedule.next_run_at, self.now + timedelta(days=1))
        self.assertEqual(len(schedule.attempt_history), 6)

    @override_settings(**SCHEDULER_SETTINGS)
    def test_machine_identity_change_moves_schedule_to_review(self):
        scheduled_analysis_round(now=self.now, limit=10, dry_run=True)
        schedule = PersistedTelemetryAnalysisSchedule.objects.create(
            endpoint=self.machine, machine_id_snapshot=self.machine.machine_id,
            first_scheduled_at=self.now, scheduled_for=self.now, next_run_at=self.now)
        self.machine.machine_id = str(uuid.uuid4())
        self.machine.save(update_fields=['machine_id'])
        scheduled_analysis_round(now=self.now, limit=10)
        schedule.refresh_from_db()
        self.assertTrue(schedule.review_required)
        self.assertEqual(schedule.review_reason, 'machine_identity_changed')
        self.assertFalse(PersistedTelemetryAnalysis.objects.exists())

    @override_settings(**SCHEDULER_SETTINGS)
    def test_terminal_endpoint_is_not_retried_or_analyzed(self):
        PersistedTelemetryAnalysisSchedule.objects.create(
            endpoint=self.machine, machine_id_snapshot=self.machine.machine_id,
            first_scheduled_at=self.now, scheduled_for=self.now, next_run_at=self.now)
        self.machine.agent_lifecycle_status = AgentMachine.STATUS_UNINSTALLED
        self.machine.save(update_fields=['agent_lifecycle_status'])
        result = scheduled_analysis_round(now=self.now, limit=10)
        schedule = PersistedTelemetryAnalysisSchedule.objects.get(endpoint=self.machine)
        self.assertEqual(result['review_required'], 1)
        self.assertTrue(schedule.review_required)
        self.assertEqual(schedule.review_reason, 'endpoint_inactive')
        self.assertFalse(PersistedTelemetryAnalysis.objects.exists())

    @override_settings(**SCHEDULER_SETTINGS)
    def test_ambiguous_hostname_is_persistently_marked_for_review(self):
        AgentMachine.create_with_token(hostname=self.machine.hostname,
                                       machine_id=str(uuid.uuid4()), agent_version='other')
        scheduled_analysis_round(now=self.now, limit=10)
        schedule = PersistedTelemetryAnalysisSchedule.objects.get(endpoint=self.machine)
        self.assertTrue(schedule.review_required)
        self.assertEqual(schedule.review_reason, 'endpoint_name_ambiguous')
        self.assertIsNone(schedule.next_run_at)
        self.assertFalse(PersistedTelemetryAnalysis.objects.exists())

    @override_settings(**SCHEDULER_SETTINGS)
    def test_transaction_failure_records_bounded_retry_without_analysis(self):
        with patch('agents.telemetry_persisted_analysis.analyze_endpoint',
                   side_effect=OperationalError('synthetic')):
            result = scheduler_round(now=self.now, limit=10)
        self.assertEqual(result['failed'], 1)
        self.assertEqual(result['telemetry_analysis']['failed'], 1)
        schedule = PersistedTelemetryAnalysisSchedule.objects.get(endpoint=self.machine)
        self.assertEqual(schedule.last_attempt_reason, 'database_transient_failure')
        self.assertEqual(schedule.retry_at, self.now + timedelta(minutes=15))
        self.assertFalse(PersistedTelemetryAnalysis.objects.exists())
        self.assertEqual(AutomationSchedulerHealth.objects.get(key='telemetry_analysis').failed, 1)

    @override_settings(**SCHEDULER_SETTINGS)
    def test_insufficient_window_retries_and_does_not_write_latest_analysis(self):
        EndpointPerformanceSample.objects.filter(endpoint=self.machine).delete()
        weak = window(self.now - timedelta(minutes=5), received=4)
        persist_windows(self.machine, [weak])
        result = scheduled_analysis_round(now=self.now, limit=10)
        self.assertEqual(result['failed'], 1)
        schedule = PersistedTelemetryAnalysisSchedule.objects.get(endpoint=self.machine)
        self.assertEqual(schedule.last_attempt_reason, 'coverage_below_policy')
        analysis = PersistedTelemetryAnalysis.objects.get(endpoint=self.machine)
        self.assertIsNone(analysis.latest_analysis_id)
        self.assertEqual(analysis.analysis_count, 0)

    @override_settings(**SCHEDULER_SETTINGS)
    def test_no_primary_metrics_remains_unknown_and_does_not_create_analysis(self):
        EndpointPerformanceSample.objects.filter(endpoint=self.machine).delete()
        absent = window(self.now - timedelta(minutes=5), cpu=None, memory=None)
        persist_windows(self.machine, [absent])
        result = scheduled_analysis_round(now=self.now, limit=10)
        self.assertEqual(result['failed'], 1)
        schedule = PersistedTelemetryAnalysisSchedule.objects.get(endpoint=self.machine)
        self.assertEqual(schedule.last_attempt_reason, 'primary_metrics_unavailable')
        analysis = PersistedTelemetryAnalysis.objects.get(endpoint=self.machine)
        self.assertIsNone(analysis.latest_analysis_id)
        self.assertEqual(analysis.analysis_count, 0)

    @override_settings(**SCHEDULER_SETTINGS)
    def test_detailed_source_absence_is_preserved_as_quality_not_zero_metrics(self):
        EndpointPerformanceSample.objects.filter(endpoint=self.machine).delete()
        unavailable = window(self.now - timedelta(minutes=5), quality={
            'cpu': 'available', 'memory': 'available', 'disk': 'unavailable',
            'network': 'error', 'group_status_counts': {
                'disk': {'unavailable': 6}, 'network': {'error': 6}}})
        persist_windows(self.machine, [unavailable])
        scheduled_analysis_round(now=self.now, limit=10)
        detail = PersistedTelemetryAnalysis.objects.get(endpoint=self.machine).latest_analysis
        self.assertEqual(detail['disk']['instances'], {})
        self.assertEqual(detail['network']['interfaces'], {})
        self.assertNotIn('disk', detail['quality']['reported_metric_keys'])
        self.assertEqual(detail['quality']['metric_status']['disk']['status'], 'unavailable')
        self.assertEqual(detail['quality']['metric_status']['network']['status'], 'error')
        self.assertEqual(detail['quality']['source_quality']['group_status_counts']['disk'],
                         {'unavailable': 6})

    @override_settings(**{**SCHEDULER_SETTINGS, 'NIGHTOWL_TELEMETRY_RETENTION_DAYS': {60: 6}})
    def test_three_consecutive_systemic_configuration_failures_suspend_fail_closed(self):
        first = scheduled_analysis_round(now=self.now, limit=10)
        second = scheduled_analysis_round(now=self.now + timedelta(minutes=1), limit=10)
        third = scheduled_analysis_round(now=self.now + timedelta(minutes=2), limit=10)
        self.assertEqual([first['mode'], second['mode'], third['mode']], ['failed'] * 3)
        self.assertFalse(first['suspended'])
        self.assertFalse(second['suspended'])
        self.assertTrue(third['suspended'])
        health = AutomationSchedulerHealth.objects.get(key='telemetry_analysis')
        self.assertEqual(health.consecutive_systemic_failures, 3)
        self.assertEqual(health.last_failure_class, 'configuration')
        self.assertEqual(health.suspension_reason, 'analysis_scheduler_requires_seven_day_detail_retention')
        self.assertEqual(AuditEvent.objects.filter(
            event_type='telemetry_analysis_scheduler.suspended').count(), 1)

    @override_settings(**SCHEDULER_SETTINGS)
    def test_suspension_does_not_clear_without_audited_authorized_recovery(self):
        health = AutomationSchedulerHealth.objects.create(
            key='telemetry_analysis', suspended_at=self.now,
            suspension_reason='database_operation_failed', consecutive_systemic_failures=3)
        suspended = scheduled_analysis_round(now=self.now + timedelta(minutes=1), limit=10)
        self.assertEqual(suspended['mode'], 'suspended')
        health.refresh_from_db()
        self.assertEqual(health.suspended_at, self.now)

        from django.contrib.auth import get_user_model
        from .telemetry_persisted_analysis import recover_telemetry_analysis_scheduler
        operator = get_user_model().objects.create_superuser('scheduler-recovery', password='synthetic-only')
        resumed = recover_telemetry_analysis_scheduler(operator, 'Banco recuperado e validado em teste.')
        self.assertIsNone(resumed.suspended_at)
        self.assertEqual(resumed.consecutive_systemic_failures, 0)
        self.assertEqual(resumed.recovered_by, 'scheduler-recovery')
        self.assertEqual(AuditEvent.objects.get(
            event_type='telemetry_analysis_scheduler.recovered').actor_name, 'scheduler-recovery')

    def test_health_api_requires_technical_permission_and_audits_recovery(self):
        AutomationSchedulerHealth.objects.create(
            key='telemetry_analysis', suspended_at=self.now,
            suspension_reason='database_operation_failed', consecutive_systemic_failures=3)
        client = Client()
        operator = get_user_model().objects.create_superuser('health-api-operator', password='synthetic-only')
        self.assertTrue(client.login(username='health-api-operator', password='synthetic-only'))
        health_url = reverse('telemetry-analysis-scheduler-health')
        self.assertEqual(client.get(health_url).status_code, 200)
        response = client.post(health_url, data=json.dumps({
            'action': 'resume', 'reason': 'Database recovery verified in isolated test.'}),
            content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['health']['suspended'])
        self.assertTrue(AuditEvent.objects.filter(
            event_type='telemetry_analysis_scheduler.recovered', actor_name=operator.username).exists())

        client.logout()
        unprivileged = get_user_model().objects.create_user('unprivileged-health', password='synthetic-only')
        self.assertTrue(client.login(username=unprivileged.username, password='synthetic-only'))
        denied = client.get(health_url)
        self.assertEqual(denied.status_code, 302, denied.get('Location'))
        with self.assertRaises(PermissionError):
            from .telemetry_persisted_analysis import recover_telemetry_analysis_scheduler
            recover_telemetry_analysis_scheduler(unprivileged, 'Attempted unauthorized recovery.')

    @override_settings(**{**SCHEDULER_SETTINGS,
                          'NIGHTOWL_TELEMETRY_RETENTION_DAYS': {60: 6},
                          'NIGHTOWL_JOB_AUTOMATIONS_ENABLED': True})
    def test_telemetry_systemic_failure_does_not_suspend_independent_jobs_scheduler(self):
        result = scheduler_round(now=self.now, limit=10)
        self.assertEqual(result['telemetry_analysis']['mode'], 'failed')
        self.assertEqual(result['telemetry_analysis']['health']['last_failure_class'], 'configuration')
        jobs_health = AutomationSchedulerHealth.objects.get(key='jobs')
        self.assertEqual(jobs_health.failed, 0)
        telemetry_health = AutomationSchedulerHealth.objects.get(key='telemetry_analysis')
        self.assertIsNone(telemetry_health.suspended_at)

    @override_settings(**SCHEDULER_SETTINGS)
    def test_quality_blocks_are_observable_but_do_not_count_as_systemic_failures(self):
        EndpointPerformanceSample.objects.filter(endpoint=self.machine).delete()
        weak = window(self.now - timedelta(minutes=5), received=4)
        persist_windows(self.machine, [weak])
        result = scheduled_analysis_round(now=self.now, limit=10)
        self.assertEqual(result['blocked'], 1)
        health = AutomationSchedulerHealth.objects.get(key='telemetry_analysis')
        self.assertEqual(health.last_failure_class, 'endpoint_quality')
        self.assertEqual(health.consecutive_systemic_failures, 0)
        self.assertEqual(health.blocked_endpoints, 1)
        self.assertIsNone(health.suspended_at)

    @override_settings(**SCHEDULER_SETTINGS)
    def test_repeated_interrupted_rounds_suspend_instead_of_resuming_silently(self):
        AutomationSchedulerHealth.objects.create(
            key='telemetry_analysis', last_started_at=self.now - timedelta(minutes=5),
            last_attempt_status='running', consecutive_systemic_failures=2)
        result = scheduled_analysis_round(now=self.now, limit=10)
        self.assertEqual(result['mode'], 'suspended')
        self.assertEqual(result['reason'], 'previous_round_interrupted')
        self.assertFalse(PersistedTelemetryAnalysisSchedule.objects.exists())
        self.assertFalse(PersistedTelemetryAnalysis.objects.exists())
        health = AutomationSchedulerHealth.objects.get(key='telemetry_analysis')
        self.assertIsNotNone(health.suspended_at)
        self.assertEqual(health.consecutive_systemic_failures, 3)
        self.assertEqual(health.last_failure_class, 'scheduler')
