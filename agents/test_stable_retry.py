from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from django.test import SimpleTestCase, override_settings

from .stable_retry import classify_retry_evidence, retry_delay


@override_settings(NIGHTOWL_STABLE_ROLLOUT_RETRY_DELAYS_SECONDS=[900, 3600, 14400])
class StableRetryPolicyTests(SimpleTestCase):
    def test_retry_delays_are_configured_per_additional_attempt(self):
        self.assertEqual(retry_delay(1), None)
        self.assertEqual(retry_delay(2), timedelta(minutes=15))
        self.assertEqual(retry_delay(3), timedelta(hours=1))
        self.assertEqual(retry_delay(4), timedelta(hours=4))
        self.assertEqual(retry_delay(5), None)

    def test_only_proven_pre_execution_network_failures_are_retryable(self):
        job = SimpleNamespace(status='failed', error_code='UPDATE_DOWNLOAD_FAILED', result_received_at=datetime.now(timezone.utc))
        self.assertEqual(classify_retry_evidence(job), ('retryable', 'update_download_failed'))

    def test_timeout_or_missing_receipt_is_inconclusive(self):
        job = SimpleNamespace(status='timed_out', error_code='JOB_TIMEOUT', result_received_at=None)
        self.assertEqual(classify_retry_evidence(job), ('inconclusive', 'execution_state_unknown'))

    def test_space_waits_and_rollback_requires_operator(self):
        space = SimpleNamespace(status='failed', error_code='INSUFFICIENT_DISK_SPACE', result_received_at=datetime.now(timezone.utc))
        rollback = SimpleNamespace(status='rolled_back', error_code='ROLLBACK', result_received_at=datetime.now(timezone.utc))
        self.assertEqual(classify_retry_evidence(space), ('awaiting_condition', 'space_insufficient'))
        self.assertEqual(classify_retry_evidence(rollback), ('rolled_back', 'operator_intervention_required'))

    def test_unknown_failure_is_not_retried(self):
        job = SimpleNamespace(status='failed', error_code='INSTALL_FAILED', result_received_at=datetime.now(timezone.utc))
        self.assertEqual(classify_retry_evidence(job), ('permanent_failure', 'install_failed'))
