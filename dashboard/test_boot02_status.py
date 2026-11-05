from types import SimpleNamespace
from django.test import SimpleTestCase
from dashboard.remote_install_batch import installation_display_status, installation_error_message, ERRORS


class InstallationDisplayStatusTests(SimpleTestCase):
    def test_states_preserve_operational_evidence_and_ambiguity(self):
        cases = [
            ('INSTALLING', '', 'RUNNING', 'INSTALLING', 'installing'),
            ('INSTALLING', '', 'RUNNING', 'WAITING_HEARTBEAT', 'pending'),
            ('REVIEW_REQUIRED', 'HEARTBEAT_TIMEOUT', 'INSTALLED_UNVERIFIED', 'WAITING_HEARTBEAT', 'pending'),
            ('COMPLETED', '', 'COMPLETED', 'COMPLETED', 'operational'),
            ('REVIEW_REQUIRED', '', 'OUTCOME_UNKNOWN', 'INSTALLING', 'unknown'),
            ('FAILED', 'REPAIR_REQUIRED', 'FAILED', 'INSTALLING', 'repair_required'),
            ('FAILED', '', 'FAILED', 'INSTALLING', 'failed'),
        ]
        for status, code, job_status, stage, expected in cases:
            with self.subTest(status=status, job_status=job_status, stage=stage):
                item = SimpleNamespace(status=status, error_code=code)
                job = SimpleNamespace(status=job_status, stage=stage, diagnostics={'service_validation_status': 'PASS'})
                self.assertEqual(installation_display_status(item, job), expected)

    def test_unverified_without_service_proof_is_not_called_installed(self):
        item = SimpleNamespace(status='REVIEW_REQUIRED', error_code='INSTALL_CONFIRMATION_PENDING')
        job = SimpleNamespace(status='INSTALLED_UNVERIFIED', stage='INSTALLER_FINISHED', diagnostics={})
        self.assertEqual(installation_display_status(item, job), 'pending_unverified')

    def test_unavailable_winrm_offers_manual_fallback(self):
        self.assertIn('manual', ERRORS['WINRM_UNAVAILABLE'].lower())
        self.assertIn('manual', installation_error_message('TCP_5986_TIMEOUT').lower())
