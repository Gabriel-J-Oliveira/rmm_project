import json
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import OperationalError, close_old_connections
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from dashboard import remote_install
from dashboard.models import RemoteInstallJob
from dashboard.remote_install_preflight import CHECKS, ProbeFailure
from dashboard.test_remote_install import COMPUTER, SENTINEL


def absence_proof():
    return {
        'status': 'READY', 'target': {'fqdn': COMPUTER['fqdn']},
        'checks': {key: {'status': 'PASS'} for key in CHECKS},
        'nightowl_absence': {
            'service_present': False, 'directory_present': False, 'correlation': 'UNMANAGED',
        },
    }


class HistoricalInstallReconciliationTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user('reconcile-admin', is_staff=True)
        target = mock.patch.object(remote_install, '_ad_target', return_value=COMPUTER)
        self.target = target.start()
        self.addCleanup(target.stop)
        probe = mock.patch.object(remote_install, 'run_remote_install_preflight', side_effect=lambda *args: absence_proof())
        self.probe = probe.start()
        self.addCleanup(probe.stop)

    def historical(self, status='OUTCOME_UNKNOWN', **fields):
        return RemoteInstallJob.objects.create(
            target_hostname=COMPUTER['hostname'], target_fqdn=COMPUTER['fqdn'],
            target_ad_dn=COMPUTER['distinguished_name'], requested_by=self.actor,
            status=status, stage=status, error_code='INSTALLER_FAILED', active_slot=None,
            diagnostics=fields.pop('diagnostics', {}), **fields,
        )

    def create(self):
        return remote_install.create_remote_install_job(
            COMPUTER['fqdn'], self.actor, username='synthetic-admin', password=SENTINEL)

    def test_all_historical_states_preserved_and_new_job_created(self):
        old = [self.historical(status) for status in remote_install.HISTORICAL_BLOCKERS]
        new = self.create()
        self.assertEqual(new.active_slot, 'global')
        self.probe.assert_called_once_with(COMPUTER['fqdn'], 'synthetic-admin', SENTINEL)
        for previous in old:
            original = previous.status
            previous.refresh_from_db()
            self.assertEqual(previous.status, original)
            self.assertEqual(previous.stage, original)
            self.assertEqual(previous.error_code, 'INSTALLER_FAILED')
            self.assertNotEqual(previous.pk, new.pk)
            self.assertEqual(previous.diagnostics['reconciliation']['status'], 'TARGET_ABSENCE_CONFIRMED')
            self.assertEqual(previous.diagnostics['reconciliation']['new_job_id'], str(new.pk))
            self.assertEqual(previous.diagnostics['reconciliation']['reconciled_by_user_id'], self.actor.pk)
        persisted = json.dumps(list(RemoteInstallJob.objects.values()), default=str)
        for secret in (SENTINEL, 'synthetic-admin', 'password', 'username'):
            self.assertNotIn(secret, persisted)

    def test_legacy_without_current_credentials_remains_blocked(self):
        old = self.historical()
        with self.assertRaisesMessage(remote_install.InstallFailure, 'INSTALL_RECONCILIATION_REQUIRED'):
            remote_install.create_remote_install_job(COMPUTER['fqdn'], self.actor)
        self.probe.assert_not_called()
        old.refresh_from_db()
        self.assertEqual(old.diagnostics, {})
        self.assertEqual(RemoteInstallJob.objects.count(), 1)

    def test_failed_unknown_managed_and_incomplete_proofs_do_not_reconcile(self):
        old = self.historical()
        proofs = [{'status': 'NOT_READY'}, {'status': 'READY', 'checks': {}}, None]
        for field, value in (
                ('service_present', True), ('directory_present', True),
                ('service_present', None), ('directory_present', None),
                ('correlation', 'MANAGED'), ('correlation', 'CONFLICT'), ('correlation', 'UNKNOWN')):
            proof = absence_proof()
            proof['nightowl_absence'][field] = value
            proofs.append(proof)
        for code in ('NIGHTOWL_INSTALLATION_DETECTED', 'NIGHTOWL_STATE_UNKNOWN', 'AUTHENTICATION_FAILED'):
            proof = absence_proof()
            proof['checks']['NIGHTOWL_ABSENCE'] = {'status': 'FAIL', 'code': code}
            proofs.append(proof)
        for proof in proofs:
            with self.subTest(proof=proof):
                self.probe.side_effect = None
                self.probe.return_value = proof
                with self.assertRaisesMessage(remote_install.InstallFailure, 'INSTALL_RECONCILIATION_FAILED'):
                    self.create()
                old.refresh_from_db()
                self.assertEqual(old.diagnostics, {})
                self.assertEqual(RemoteInstallJob.objects.count(), 1)

    def test_probe_errors_are_sanitized_and_do_not_write(self):
        old = self.historical()
        for error in (ProbeFailure('AUTHENTICATION_FAILED'), RuntimeError(SENTINEL)):
            self.probe.side_effect = error
            with self.assertRaisesMessage(remote_install.InstallFailure, 'INSTALL_RECONCILIATION_FAILED') as raised:
                self.create()
            self.assertNotIn(SENTINEL, str(raised.exception))
        old.refresh_from_db()
        self.assertEqual(old.diagnostics, {})

    def test_ad_identity_change_and_correlation_failure_block(self):
        old = self.historical()
        for changed in (
                {**COMPUTER, 'distinguished_name': 'CN=other,DC=control,DC=local'},
                {**COMPUTER, 'sid': 'different-identity'},
                ProbeFailure('TARGET_MANAGED_OR_CONFLICT')):
            self.target.side_effect = [COMPUTER, changed]
            with self.assertRaises((remote_install.InstallFailure, ProbeFailure)):
                self.create()
            old.refresh_from_db()
            self.assertEqual(old.diagnostics, {})

    def test_reconciled_history_requires_new_proof_for_each_attempt(self):
        old = self.historical(diagnostics={'reconciliation': {'status': 'TARGET_ABSENCE_CONFIRMED'}})
        self.probe.return_value = {'status': 'NOT_READY'}
        self.probe.side_effect = None
        with self.assertRaisesMessage(remote_install.InstallFailure, 'INSTALL_RECONCILIATION_FAILED'):
            self.create()
        old.refresh_from_db()
        self.assertEqual(old.status, 'OUTCOME_UNKNOWN')
        self.assertEqual(RemoteInstallJob.objects.count(), 1)

    def test_active_install_blocks_before_remote_probe(self):
        self.historical()
        RemoteInstallJob.objects.create(target_fqdn='other.control.local', requested_by=self.actor, active_slot='global')
        with self.assertRaisesMessage(remote_install.InstallFailure, 'INSTALL_ALREADY_RUNNING'):
            self.create()
        self.probe.assert_not_called()

    def test_active_job_without_slot_still_blocks(self):
        self.historical(status='RUNNING')
        with self.assertRaisesMessage(remote_install.InstallFailure, 'INSTALL_ALREADY_RUNNING'):
            self.create()
        self.probe.assert_not_called()

    def test_current_database_correlation_rechecked_in_transaction(self):
        old = self.historical()
        with mock.patch.object(remote_install, '_match_machine', return_value=(None, 'CONFLICT', None)):
            with self.assertRaisesMessage(remote_install.InstallFailure, 'TARGET_MANAGED_OR_CONFLICT'):
                self.create()
        old.refresh_from_db()
        self.assertEqual(old.diagnostics, {})
        self.assertEqual(RemoteInstallJob.objects.count(), 1)

    def test_install_post_uses_current_credentials_and_returns_new_uuid(self):
        from django.middleware.csrf import _get_new_csrf_string
        old = self.historical()
        self.client.force_login(self.actor)
        data = {'fqdn': COMPUTER['fqdn'], 'username': 'synthetic-admin',
                'password': SENTINEL, 'csrfmiddlewaretoken': _get_new_csrf_string()}
        with mock.patch('dashboard.ad_install_views.start_remote_install') as runner:
            response = self.client.post(reverse('agent-install-ad-install'), data, secure=True)
        self.assertEqual(response.status_code, 202)
        self.assertNotEqual(response.json()['job_id'], str(old.pk))
        self.probe.assert_called_once_with(COMPUTER['fqdn'], 'synthetic-admin', SENTINEL)
        runner.assert_called_once()
        self.assertNotIn(SENTINEL, response.content.decode())
        self.assertNotIn(SENTINEL, json.dumps(dict(self.client.session)))

    def test_failed_interrupted_retry_keeps_existing_contract(self):
        for status in ('FAILED', 'INTERRUPTED'):
            self.historical(status)
        self.create()
        self.probe.assert_not_called()

    def test_new_blocker_during_probe_requires_another_proof(self):
        old = self.historical()
        def probe(*args):
            self.historical('INSTALLED_UNVERIFIED')
            return absence_proof()
        self.probe.side_effect = probe
        with self.assertRaisesMessage(remote_install.InstallFailure, 'INSTALL_RECONCILIATION_REQUIRED'):
            self.create()
        old.refresh_from_db()
        self.assertEqual(old.diagnostics, {})
        self.assertFalse(RemoteInstallJob.objects.filter(active_slot='global').exists())

    def test_changed_historical_record_during_probe_invalidates_proof(self):
        from django.utils import timezone
        old = self.historical()
        def probe(*args):
            RemoteInstallJob.objects.filter(pk=old.pk).update(updated_at=timezone.now())
            return absence_proof()
        self.probe.side_effect = probe
        with self.assertRaisesMessage(remote_install.InstallFailure, 'INSTALL_RECONCILIATION_REQUIRED'):
            self.create()
        old.refresh_from_db()
        self.assertEqual(old.diagnostics, {})
        self.assertEqual(RemoteInstallJob.objects.count(), 1)

    def test_reservation_failure_does_not_reconcile_history(self):
        from django.db import IntegrityError
        old = self.historical()
        with mock.patch.object(RemoteInstallJob.objects, 'create', side_effect=IntegrityError):
            with self.assertRaisesMessage(remote_install.InstallFailure, 'INSTALL_ALREADY_RUNNING'):
                self.create()
        old.refresh_from_db()
        self.assertEqual(old.diagnostics, {})

    def test_reconciliation_write_failure_rolls_back_new_job(self):
        old = self.historical()
        with mock.patch.object(RemoteInstallJob, 'save', autospec=True, side_effect=RuntimeError) as save:
            original_save = save.side_effect
            # Let the new-job insert execute; fail only on the historical update.
            from django.db.models import Model
            def persist(instance, *args, **kwargs):
                if instance.pk == old.pk:
                    raise original_save()
                return Model.save(instance, *args, **kwargs)
            save.side_effect = persist
            with self.assertRaises(RuntimeError):
                self.create()
        old.refresh_from_db()
        self.assertEqual(old.diagnostics, {})
        self.assertEqual(RemoteInstallJob.objects.count(), 1)

    def test_new_runner_repeats_preflight_before_installer(self):
        self.historical()
        new = self.create()
        self.probe.side_effect = None
        self.probe.return_value = {'status': 'NOT_READY', 'checks': {
            'NIGHTOWL_ABSENCE': {'status': 'FAIL', 'code': 'NIGHTOWL_INSTALLATION_DETECTED'}}}
        from dashboard.test_remote_install import CONTRACT
        with mock.patch.object(remote_install.threading, 'Thread'), \
                mock.patch.object(remote_install, 'validate_install_release', return_value=CONTRACT), \
                mock.patch.object(remote_install, '_remote_script') as installer:
            remote_install.run_remote_install(new.pk, 'synthetic-admin', SENTINEL)
        self.assertEqual(self.probe.call_count, 2)
        installer.assert_not_called()
        new.refresh_from_db()
        self.assertEqual(new.status, 'FAILED')
        self.assertEqual(new.error_code, 'NIGHTOWL_INSTALLATION_DETECTED')

    def test_status_get_is_read_only(self):
        old = self.historical()
        before = dict(RemoteInstallJob.objects.values().get(pk=old.pk))
        self.client.force_login(self.actor)
        with mock.patch.object(remote_install, 'reconcile_stale_jobs') as reconcile:
            response = self.client.get(reverse('agent-install-job-status', args=[old.pk]))
        self.assertEqual(response.status_code, 200)
        reconcile.assert_not_called()
        self.probe.assert_not_called()
        self.assertEqual(before, dict(RemoteInstallJob.objects.values().get(pk=old.pk)))


class ConcurrentInstallReconciliationTests(TransactionTestCase):
    def test_concurrent_proofs_admit_only_one_job(self):
        actor = get_user_model().objects.create_user('concurrent-reconcile-admin', is_staff=True)
        old = RemoteInstallJob.objects.create(
            target_hostname=COMPUTER['hostname'], target_fqdn=COMPUTER['fqdn'],
            target_ad_dn=COMPUTER['distinguished_name'], requested_by=actor,
            status='OUTCOME_UNKNOWN', stage='OUTCOME_UNKNOWN', active_slot=None, diagnostics={})
        barrier = threading.Barrier(2)
        def probe(*args):
            barrier.wait(timeout=5)
            return absence_proof()
        def request():
            close_old_connections()
            try:
                current_actor = get_user_model().objects.get(pk=actor.pk)
                return remote_install.create_remote_install_job(COMPUTER['fqdn'], current_actor,
                                                                username='synthetic', password=SENTINEL).pk
            except remote_install.InstallFailure as exc:
                return exc.code
            except OperationalError:
                # SQLite may reject the competing transaction with database locked;
                # PostgreSQL serializes via row locks and the unique global slot.
                return 'DATABASE_CONTENTION'
            finally:
                close_old_connections()
        with mock.patch.object(remote_install, '_ad_target', return_value=COMPUTER), \
                mock.patch.object(remote_install, 'reconcile_stale_jobs'), \
                mock.patch.object(remote_install, 'run_remote_install_preflight', side_effect=probe), \
                ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(request) for _ in range(2)]
            results = [future.result(timeout=10) for future in futures]
        admitted = RemoteInstallJob.objects.filter(active_slot='global')
        self.assertLessEqual(admitted.count(), 1)
        # A contention failure must not leave historical reconciliation alone.
        old.refresh_from_db()
        if admitted.exists():
            self.assertEqual(old.diagnostics['reconciliation']['new_job_id'], str(admitted.get().pk))
            self.assertIn(admitted.get().pk, results)
        else:
            self.assertEqual(old.diagnostics, {})
            self.assertEqual(results, ['DATABASE_CONTENTION', 'DATABASE_CONTENTION'])
        self.assertEqual(old.status, 'OUTCOME_UNKNOWN')
