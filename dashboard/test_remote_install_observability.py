import json
import logging
import ssl
import uuid
from unittest import mock

import requests
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from winrm.exceptions import AuthenticationError, WinRMTransportError

from agents.models import AuditEvent
from dashboard import remote_install as install
from dashboard import remote_install_batch as batch
from dashboard import remote_install_preflight as preflight
from dashboard.models import RemoteInstallBatch, RemoteInstallJob
from dashboard.remote_install_audit import InstallAttempt, record_execution_failure, sanitize_report
from dashboard.test_remote_install import COMPUTER, SENTINEL


def failed_result():
    return {'status': 'NOT_READY', 'target': {'fqdn': COMPUTER['fqdn'], 'ip': '192.168.104.15'},
            'checks': {name: ({'status': 'FAIL', 'code': 'TLS_VALIDATION_FAILED',
                'stage': 'WINRM_SESSION', 'exception_type': 'SSLError'} if name == 'REMOTE_TRANSPORT'
                else {'status': 'PASS' if name in ('TARGET', 'DNS') else 'SKIPPED'}) for name in preflight.CHECKS}}


class AttemptAuditTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user('audit-operator', is_staff=True)

    def test_admission_rejection_is_recorded_as_failed_item_without_install_job(self):
        with mock.patch.object(batch, '_ad_target', side_effect=preflight.ProbeFailure('AD_COMPUTER_DISABLED')):
            operation = batch.create_batch([COMPUTER['fqdn']], self.actor, username='admin', password=SENTINEL)
        self.assertEqual(operation.items.get().status, 'FAILED')
        self.assertFalse(RemoteInstallJob.objects.exists())
        events = list(AuditEvent.objects.order_by('created_at'))
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].metadata['correlation_id'], events[1].metadata['correlation_id'])
        self.assertEqual(events[1].metadata['code'], 'AD_COMPUTER_DISABLED')
        self.assertEqual(events[1].metadata['requested_fqdn'], COMPUTER['fqdn'])
        self.assertEqual(events[1].actor_name, self.actor.username)
        self.assertNotIn(SENTINEL, json.dumps(list(AuditEvent.objects.values()), default=str))
        report = batch.batch_payload(operation, detail=True)['items'][0]['diagnostic']
        self.assertEqual(report['code'], 'AD_COMPUTER_DISABLED')
        self.assertEqual(report['phase'], 'admission')

    def test_single_target_admission_failure_is_also_audited(self):
        with mock.patch.object(install, '_ad_target', side_effect=preflight.ProbeFailure('TARGET_MANAGED_OR_CONFLICT')):
            with self.assertRaises(preflight.ProbeFailure):
                install.create_remote_install_job(COMPUTER['fqdn'], self.actor)
        self.assertEqual(AuditEvent.objects.filter(event_type='remote_install.admission.finished').count(), 1)
        self.assertFalse(RemoteInstallJob.objects.exists())

    def test_failed_preflight_without_job_is_in_drawer_and_admin_history(self):
        with mock.patch.object(batch, '_ad_target', return_value=COMPUTER):
            operation = batch.create_batch([COMPUTER['fqdn']], self.actor, username='admin', password=SENTINEL)
            with mock.patch.object(batch.threading, 'Thread'), mock.patch.object(
                    batch, 'run_remote_install_preflight', return_value=failed_result()), mock.patch.object(
                    install, 'start_remote_install') as dispatch:
                batch.run_batch(operation.pk, 'admin', SENTINEL)
        operation.refresh_from_db()
        with self.assertNumQueries(2):
            payload = batch.batch_payload(operation, detail=True)
        item = payload['items'][0]
        self.assertIsNone(item['job_id'])
        self.assertEqual(item['error_code'], 'TLS_VALIDATION_FAILED')
        self.assertEqual(item['diagnostic']['correlation_id'], item['id'])
        self.assertEqual(item['diagnostic']['batch_id'], str(operation.pk))
        self.assertEqual(item['diagnostic']['resolved_ip'], '192.168.104.15')
        self.assertEqual(item['diagnostic']['stage'], 'WINRM_SESSION')
        self.assertEqual(item['diagnostic']['exception_type'], 'SSLError')
        self.assertIsNotNone(item['diagnostic']['finished_at'])
        dispatch.assert_not_called()
        self.assertFalse(RemoteInstallJob.objects.exists())
        self.assertEqual(AuditEvent.objects.filter(metadata__fqdn=COMPUTER['fqdn'],
            event_type='remote_install.preflight.finished').count(), 1)

    def test_preflight_audit_records_only_allowlisted_winrm_transport_metadata(self):
        with InstallAttempt(COMPUTER['fqdn'], self.actor) as attempt:
            attempt.complete({'status': 'NOT_READY', 'target': {
                'fqdn': COMPUTER['fqdn'], 'ip': '192.168.104.15',
                'winrm_transport': 'HTTP_NTLM_ENCRYPTED', 'winrm_port': 5985,
                'unexpected': 'never-persist'}, 'checks': {
                    'REMOTE_TRANSPORT': {'status': 'FAIL', 'code': 'TCP_5985_REFUSED'}}})
        event = AuditEvent.objects.get(event_type='remote_install.preflight.finished')
        self.assertEqual(event.metadata['winrm_transport'], 'HTTP_NTLM_ENCRYPTED')
        self.assertEqual(event.metadata['winrm_port'], 5985)
        self.assertNotIn('unexpected', event.metadata)

    def test_failure_in_target_revalidation_is_audited(self):
        with mock.patch.object(batch, '_ad_target', return_value=COMPUTER):
            operation = batch.create_batch([COMPUTER['fqdn']], self.actor, username='admin', password=SENTINEL)
        with mock.patch.object(batch.threading, 'Thread'), mock.patch.object(batch, '_ad_target',
                side_effect=preflight.ProbeFailure('TARGET_CHANGED')):
            batch.run_batch(operation.pk, 'admin', SENTINEL)
        event = AuditEvent.objects.get(event_type='remote_install.preflight.finished')
        self.assertEqual(event.metadata['code'], 'TARGET_CHANGED')
        self.assertEqual(event.metadata['stage'], 'TARGET')

    def test_raw_exception_never_enters_audit_logs_or_projection(self):
        with self.assertLogs('dashboard.remote_install_audit', level=logging.INFO) as logs:
            with self.assertRaises(RuntimeError):
                with InstallAttempt(COMPUTER['fqdn'], self.actor):
                    raise RuntimeError(SENTINEL + ' deploy_SYNTHETIC_TOKEN')
        persisted = json.dumps(list(AuditEvent.objects.values()), default=str)
        self.assertNotIn(SENTINEL, persisted + '\n'.join(logs.output))
        self.assertNotIn('deploy_SYNTHETIC_TOKEN', persisted + '\n'.join(logs.output))
        report = AuditEvent.objects.get(event_type='remote_install.preflight.finished').metadata
        self.assertEqual(report['exception_type'], 'RuntimeError')
        self.assertEqual(report['code'], 'PREFLIGHT_INTERNAL_ERROR')

    def test_execution_failure_is_not_hidden_by_successful_preflight(self):
        with mock.patch.object(batch, '_ad_target', return_value=COMPUTER):
            operation = batch.create_batch([COMPUTER['fqdn']], self.actor, username='admin', password=SENTINEL)
        job = RemoteInstallJob.objects.create(requested_by=self.actor, target_hostname=COMPUTER['hostname'],
            target_fqdn=COMPUTER['fqdn'], target_ad_dn=COMPUTER['distinguished_name'])
        operation.items.update(remote_install_job=job)
        with InstallAttempt(job.target_fqdn, self.actor, correlation_id=job.pk, job_id=str(job.pk)) as attempt:
            attempt.complete({'status': 'READY', 'target': {'fqdn': job.target_fqdn, 'ip': '192.168.104.15'},
                              'checks': {'DNS': {'status': 'PASS'}}})
        record_execution_failure(job, install.InstallFailure('WINRM_REQUEST_TIMEOUT', stage='REMOTE_INPUT', exception='ReadTimeout'))
        report = batch.batch_payload(operation, detail=True)['items'][0]['diagnostic']
        self.assertEqual(report['phase'], 'execution')
        self.assertEqual(report['stage'], 'REMOTE_INPUT')
        self.assertEqual(report['code'], 'WINRM_REQUEST_TIMEOUT')
        self.assertEqual(report['job_id'], str(job.pk))
        self.assertEqual(report['resolved_ip'], '192.168.104.15')
        self.assertEqual(report['checks']['DNS']['status'], 'PASS')

    def test_audit_storage_error_logs_only_safe_category(self):
        with mock.patch.object(AuditEvent.objects, 'create', side_effect=RuntimeError(SENTINEL)):
            with self.assertLogs('dashboard.remote_install_audit', level='ERROR') as logs:
                with InstallAttempt(COMPUTER['fqdn']):
                    pass
        self.assertNotIn(SENTINEL, '\n'.join(logs.output))
        self.assertIn('audit_unavailable', '\n'.join(logs.output))


class DiagnosticClassificationTests(SimpleTestCase):
    def test_sanitizer_has_no_arbitrary_json_or_error_fields(self):
        report = sanitize_report({'fqdn': COMPUTER['fqdn'], 'requested_fqdn': COMPUTER['fqdn'],
            'password': SENTINEL, 'exception_type': SENTINEL, 'stage': [], 'code': SENTINEL,
            'resolved_ip': SENTINEL, 'correlation_id': str(uuid.uuid4()),
            'winrm_transport': 'untrusted-value', 'winrm_port': 5985,
            'started_at': timezone.now().isoformat(),
            'checks': {'DNS': {'status': 'FAIL', 'code': SENTINEL, 'raw': SENTINEL}}})
        self.assertNotIn(SENTINEL, json.dumps(report))
        self.assertEqual(report['code'], 'PREFLIGHT_FAILED')
        self.assertEqual(report['stage'], 'PREFLIGHT')
        self.assertEqual(report['winrm_transport'], '')
        self.assertEqual(report['winrm_port'], 5985)

    def test_sanitizer_keeps_bounded_endpoint_evidence_without_secrets(self):
        report = sanitize_report({
            'fqdn': COMPUTER['fqdn'], 'resolved_ip': '192.168.100.3',
            'discovered_ipv4_addresses': ['192.168.100.3', '192.168.100.221', '127.0.0.1'],
            'endpoint_attempts': [
                {'ip': '192.168.100.3', 'tcp_status': 'OPEN', 'status': 'IDENTITY_MISMATCH',
                 'code': 'REMOTE_IDENTITY_MISMATCH'},
                {'ip': '192.168.100.221', 'tcp_status': 'OPEN', 'status': 'SELECTED'},
                {'ip': '127.0.0.1', 'status': 'SELECTED', 'password': SENTINEL},
            ],
        })
        self.assertEqual(report['selected_ip'], '192.168.100.3')
        self.assertEqual(report['discovered_ipv4_addresses'], ['192.168.100.3', '192.168.100.221'])
        self.assertEqual([item['status'] for item in report['endpoint_attempts']], ['IDENTITY_MISMATCH', 'SELECTED'])
        self.assertNotIn(SENTINEL, json.dumps(report))
        self.assertIsNone(sanitize_report({'fqdn': COMPUTER['fqdn'], 'resolved_ip': '127.0.0.1'})['resolved_ip'])

    def test_tcp_failure_categories(self):
        for error, code in ((TimeoutError(SENTINEL), 'TCP_5986_TIMEOUT'),
                            (ConnectionRefusedError(SENTINEL), 'TCP_5986_REFUSED'),
                            (OSError(SENTINEL), 'TCP_5986_UNAVAILABLE')):
            with self.subTest(code=code), mock.patch.object(preflight.socket, 'create_connection', side_effect=error):
                with self.assertRaises(preflight.ProbeFailure) as raised:
                    preflight._tcp_available('192.168.104.15')
                self.assertEqual(raised.exception.code, code)
                self.assertEqual(raised.exception.stage, 'TCP_5986')

    def test_http_tcp_failure_categories_use_only_authorized_port_number(self):
        for error, code in ((TimeoutError(SENTINEL), 'TCP_5985_TIMEOUT'),
                            (ConnectionRefusedError(SENTINEL), 'TCP_5985_REFUSED'),
                            (OSError(SENTINEL), 'TCP_5985_UNAVAILABLE')):
            with self.subTest(code=code), mock.patch.object(preflight.socket, 'create_connection', side_effect=error) as connect:
                with self.assertRaises(preflight.ProbeFailure) as raised:
                    preflight._tcp_available('192.168.104.15', port=5985)
                self.assertEqual(raised.exception.code, code)
                self.assertEqual(raised.exception.stage, 'TCP_5985')
                self.assertEqual(connect.call_args.args[0], ('192.168.104.15', 5985))
                self.assertNotIn(SENTINEL, str(raised.exception))

    def test_winrm_categories_and_actual_failure_stage(self):
        errors = [(ssl.SSLCertVerificationError(SENTINEL), 'TLS_VALIDATION_FAILED'),
            (requests.exceptions.SSLError('handshake failure ' + SENTINEL), 'TLS_HANDSHAKE_FAILED'),
            (requests.exceptions.ReadTimeout(SENTINEL), 'WINRM_REQUEST_TIMEOUT'),
            (requests.exceptions.ConnectionError(SENTINEL), 'WINRM_CONNECTION_FAILED'),
            (AuthenticationError(SENTINEL), 'AUTHENTICATION_FAILED'),
            (WinRMTransportError('http', 403, SENTINEL), 'WINRM_AUTHORIZATION_DENIED'),
            (WinRMTransportError('http', 500, SENTINEL), 'WINRM_HTTP_ERROR'),
            (RuntimeError(SENTINEL), 'WINRM_PROTOCOL_ERROR')]
        for method, stage in (('open_shell', 'WINRM_SESSION'), ('run_command', 'REMOTE_EXECUTION'),
                              ('get_command_output_raw', 'REMOTE_OUTPUT')):
            for error, code in errors:
                with self.subTest(method=method, code=code):
                    protocol = mock.Mock()
                    getattr(protocol, method).side_effect = error
                    with mock.patch.object(preflight, '_new_winrm_protocol', return_value=protocol):
                        with self.assertRaises(preflight.ProbeFailure) as raised:
                            preflight._winrm_probe(COMPUTER['fqdn'], 'admin', SENTINEL)
                    self.assertEqual(raised.exception.code, code)
                    self.assertEqual(raised.exception.stage, stage)
                    self.assertNotIn(SENTINEL, str(raised.exception))

    def test_runner_classification_preserves_dispatch_uncertainty(self):
        for method, stage, attempted in (('open_shell', 'WINRM_SESSION', False),
                ('run_command', 'REMOTE_EXECUTION', False), ('send_command_input', 'REMOTE_INPUT', True),
                ('get_command_output_raw', 'REMOTE_OUTPUT', True)):
            with self.subTest(method=method):
                protocol = mock.Mock()
                getattr(protocol, method).side_effect = requests.exceptions.ReadTimeout(SENTINEL)
                with mock.patch.object(install, '_new_winrm_protocol', return_value=protocol), \
                        mock.patch.object(install, '_verify_remote_identity_session'):
                    with self.assertRaises(install.InstallFailure) as raised:
                        install._remote_script(COMPUTER['fqdn'], 'admin', SENTINEL, 'static-probe', 3,
                            endpoint_ip='192.168.104.20', expected_computer=COMPUTER)
                self.assertEqual(raised.exception.code, 'WINRM_REQUEST_TIMEOUT')
                self.assertEqual(raised.exception.stage, stage)
                self.assertEqual(raised.exception.command_attempted, attempted)
                self.assertNotIn(SENTINEL, str(raised.exception))
