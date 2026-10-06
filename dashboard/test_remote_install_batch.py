import io
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from dashboard import remote_install as install
from dashboard import remote_install_batch as batch
from dashboard.models import RemoteInstallBatch, RemoteInstallBatchItem, RemoteInstallJob
from dashboard.remote_install_preflight import ProbeFailure
from dashboard.test_remote_install import COMPUTER, SENTINEL


def target(name):
    return {**COMPUTER, 'fqdn': name, 'hostname': name.split('.')[0],
            'distinguished_name': 'CN=' + name.split('.')[0] + ',DC=control,DC=local'}


class BatchTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user('batch-admin', is_staff=True)
        for module in (batch, install):
            patch = mock.patch.object(module, '_ad_target', side_effect=target)
            patch.start()
            self.addCleanup(patch.stop)
        self.names = ['lab01.control.local', 'lab02.control.local']

    def create(self, names=None, **kwargs):
        return batch.create_batch(names or self.names, self.actor,
                                  username='synthetic-admin', password=SENTINEL, **kwargs)

    def active(self, seconds=0, diagnostics=None, stage='INSTALLING'):
        result = self.create([self.names[0]])
        result.status = 'RUNNING'
        result.current_index = 1
        result.save()
        item = result.items.get()
        job = RemoteInstallJob.objects.create(requested_by=self.actor,
            target_hostname='lab01', target_fqdn=self.names[0], target_ad_dn=item.target_ad_dn,
            status='RUNNING', stage=stage, active_slot='global',
            runner_heartbeat_at=timezone.now() - timedelta(seconds=seconds),
            diagnostics=diagnostics or {'safe_to_retry': 'NO'})
        item.remote_install_job = job
        item.status = 'INSTALLING'
        item.save()
        return result, item, job

    def test_one_many_and_fifty_targets(self):
        for n in (1, 2, 50):
            result = self.create([f'lab{i}.control.local' for i in range(n)])
            self.assertEqual(result.items.count(), n)
            self.assertEqual(result.waiting_count, n)
            self.assertEqual(list(result.items.values_list('position', flat=True)), list(range(1, n + 1)))
            result.active_slot = None
            result.save()

    def test_invalid_size_duplicate_and_browser_addresses(self):
        for names in ([], ['a.control.local'] * 2, ['a.control.local', 'A.CONTROL.LOCAL.'],
                      ['127.0.0.1'], ['https://a.control.local'], ['a'] * 51, [None]):
            with self.subTest(names=names), self.assertRaises(install.InstallFailure):
                batch.create_batch(names, self.actor, username='admin', password=SENTINEL)
        self.assertEqual(RemoteInstallBatch.objects.count(), 0)

    def test_invalid_ad_target_blocks_before_database_write(self):
        for code in ('AD_COMPUTER_DISABLED', 'TARGET_NOT_UNIQUE_OR_MISSING', 'TARGET_MANAGED_OR_CONFLICT'):
            with mock.patch.object(batch, '_ad_target', side_effect=ProbeFailure(code)), self.assertRaises(ProbeFailure):
                self.create()
        self.assertEqual(RemoteInstallBatch.objects.count(), 0)

    def test_global_admission_and_legacy_api_share_gate(self):
        result = self.create()
        with self.assertRaisesMessage(install.InstallFailure, 'INSTALL_ALREADY_RUNNING'):
            self.create()
        with self.assertRaisesMessage(install.InstallFailure, 'INSTALL_ALREADY_RUNNING'):
            install.create_remote_install_job(self.names[0], self.actor)
        self.assertEqual(RemoteInstallJob.objects.count(), 0)
        result.active_slot = None
        result.save()
        install.create_remote_install_job(self.names[0], self.actor)
        with self.assertRaisesMessage(install.InstallFailure, 'INSTALL_ALREADY_RUNNING'):
            self.create()

    def test_credentials_only_stdin(self):
        result = self.create()
        class Pipe(io.BytesIO):
            def close(self):
                self.payload = self.getvalue()
                super().close()
        pipe = Pipe()
        with mock.patch.object(batch.subprocess, 'Popen', return_value=SimpleNamespace(stdin=pipe)) as spawn:
            batch.start_batch(result, 'synthetic-admin', SENTINEL)
        self.assertEqual(json.loads(pipe.payload)['password'], SENTINEL)
        self.assertNotIn(SENTINEL, repr(spawn.call_args))
        self.assertNotIn('env', spawn.call_args.kwargs)
        for model in (RemoteInstallBatch, RemoteInstallBatchItem, RemoteInstallJob):
            persisted = json.dumps(list(model.objects.values()), default=str)
            self.assertNotIn(SENTINEL, persisted)
            self.assertNotIn('synthetic-admin', persisted)

    def test_spawn_failure_is_safe(self):
        result = self.create()
        with mock.patch.object(batch.subprocess, 'Popen', side_effect=RuntimeError(SENTINEL)), self.assertRaises(install.InstallFailure):
            batch.start_batch(result, 'admin', SENTINEL)
        result.refresh_from_db()
        self.assertEqual(result.status, 'INTERRUPTED')
        self.assertIsNone(result.active_slot)
        self.assertEqual(result.items.filter(status='FAILED').count(), 2)

    def run_simulated(self, states, *, first_preflight_fail=False):
        result = self.create()
        order = []
        def finish(job, *_args):
            self.assertEqual(RemoteInstallJob.objects.filter(active_slot='global').count(), 1)
            order.append(job.target_fqdn)
            state = states[len(order) - 1]
            RemoteInstallJob.objects.filter(pk=job.pk).update(status=state,
                diagnostics={'safe_to_retry': 'YES' if state in ('FAILED', 'INTERRUPTED') else 'NO',
                             'invocation_attempted': False},
                finished_at=timezone.now(), active_slot='global' if state == 'OUTCOME_UNKNOWN' else None)
        preflight = [{'status': 'NOT_READY'}, {'status': 'READY'}] if first_preflight_fail else None
        with mock.patch.object(batch.threading, 'Thread'), \
                mock.patch.object(batch, 'run_remote_install_preflight', side_effect=preflight,
                                  return_value={'status': 'READY'}), \
                mock.patch.object(install, 'start_remote_install', side_effect=finish):
            batch.run_batch(result.pk, 'admin', SENTINEL)
        result.refresh_from_db()
        return result, order

    def test_serial_success_then_success(self):
        result, order = self.run_simulated(['COMPLETED', 'COMPLETED'])
        self.assertEqual(order, self.names)
        self.assertEqual(result.status, 'COMPLETED')
        self.assertEqual(result.success_count, 2)

    def test_failed_preflight_continues_next_target(self):
        result, order = self.run_simulated(['COMPLETED'], first_preflight_fail=True)
        self.assertEqual(order, self.names[1:])
        self.assertEqual(result.failure_count, 1)
        self.assertEqual(result.success_count, 1)

    def test_failure_and_ambiguity_continue_without_clearing_target_blocker(self):
        for state, expected in [('FAILED', 'FAILED'), ('INTERRUPTED', 'FAILED'),
                                ('OUTCOME_UNKNOWN', 'REVIEW_REQUIRED'), ('INSTALLED_UNVERIFIED', 'REVIEW_REQUIRED')]:
            with self.subTest(state=state):
                result, order = self.run_simulated([state, 'COMPLETED'])
                self.assertEqual(order, self.names)
                self.assertEqual(result.items.first().status, expected)
                self.assertEqual(result.status, 'COMPLETED_WITH_ERRORS')
                self.assertFalse(RemoteInstallJob.objects.filter(active_slot='global').exists())
                if expected == 'REVIEW_REQUIRED':
                    with self.assertRaisesMessage(install.InstallFailure, 'INSTALL_RECONCILIATION_REQUIRED'):
                        install.create_remote_install_job(self.names[0], self.actor)
                RemoteInstallBatchItem.objects.all().delete()
                RemoteInstallJob.objects.all().delete()
                RemoteInstallBatch.objects.all().delete()

    def test_target_changed_before_preflight_never_dispatches(self):
        result = self.create([self.names[0]])
        with mock.patch.object(batch.threading, 'Thread'), \
                mock.patch.object(batch, '_ad_target', return_value={**target(self.names[0]), 'distinguished_name': 'changed'}), \
                mock.patch.object(install, 'start_remote_install') as start:
            batch.run_batch(result.pk, 'admin', SENTINEL)
        start.assert_not_called()
        self.assertEqual(result.items.get().error_code, 'TARGET_CHANGED')

    def test_manual_before_sixty_and_fresh_heartbeat_old_stage(self):
        result, item, job = self.active(seconds=59)
        RemoteInstallJob.objects.filter(pk=job.pk).update(started_at=timezone.now() - timedelta(hours=1))
        with self.assertRaisesMessage(install.InstallFailure, 'ACTION_NOT_AVAILABLE'):
            batch.resolve_stall(result.pk, item.pk, actor=self.actor)
        self.assertFalse(batch.batch_payload(result, detail=True)['manual_stall_action_available'])
        RemoteInstallJob.objects.filter(pk=job.pk).update(runner_heartbeat_at=timezone.now())
        self.assertFalse(batch._reflect_job(result.pk, item.pk))
        job.refresh_from_db()
        self.assertEqual(job.status, 'RUNNING')

    def test_manual_safe_and_ambiguous_idempotent(self):
        result, item, job = self.active(seconds=61, stage='CONNECTING', diagnostics={'safe_to_retry': 'YES'})
        batch.resolve_stall(result.pk, item.pk, actor=self.actor)
        job.refresh_from_db()
        self.assertEqual(job.status, 'INTERRUPTED')
        self.assertEqual(job.diagnostics['batch_stall_resolution']['actor_user_id'], self.actor.pk)
        snapshot = dict(RemoteInstallJob.objects.values().get(pk=job.pk))
        batch.resolve_stall(result.pk, item.pk, actor=self.actor)
        self.assertEqual(snapshot, dict(RemoteInstallJob.objects.values().get(pk=job.pk)))
        self.assertIsNone(job.active_slot)

    def test_auto_stall_120_and_unknown_proof_is_never_safe(self):
        result, item, job = self.active(seconds=121, diagnostics={'safe_to_retry': 'UNKNOWN'})
        batch._reflect_job(result.pk, item.pk)
        item.refresh_from_db()
        job.refresh_from_db()
        self.assertEqual(item.status, 'REVIEW_REQUIRED')
        self.assertEqual(job.status, 'OUTCOME_UNKNOWN')
        self.assertEqual(job.diagnostics['batch_stall_resolution']['reason'], 'AUTO_STALL_TIMEOUT')
        self.assertIsNone(job.active_slot)

    def test_auto_not_before_120(self):
        result, item, job = self.active(seconds=119)
        batch._reflect_job(result.pk, item.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, 'RUNNING')

    def test_manual_ambiguous_and_installer_stage_cannot_claim_safe(self):
        result, item, job = self.active(seconds=61, diagnostics={'safe_to_retry': 'YES'})
        batch.resolve_stall(result.pk, item.pk, actor=self.actor)
        item.refresh_from_db()
        self.assertEqual(item.status, 'REVIEW_REQUIRED')
        job.refresh_from_db()
        self.assertEqual(job.status, 'OUTCOME_UNKNOWN')
        self.assertIsNone(job.active_slot)

    def test_actual_heartbeat_boundaries(self):
        result, item, job = self.active(seconds=0)
        now = timezone.now()
        for seconds, allowed in ((59, False), (60, True), (119, True), (120, True)):
            job.runner_heartbeat_at = now - timedelta(seconds=seconds)
            self.assertEqual(batch.stale_seconds(job, now) >= batch.MANUAL_STALL_SECONDS, allowed)
        self.assertEqual(batch.AUTO_STALL_SECONDS, 120)

    def test_duplicate_runner_cannot_restart_batch(self):
        result, item, job = self.active()
        with mock.patch.object(batch, 'run_remote_install_preflight') as probe:
            batch.run_batch(result.pk, 'admin', SENTINEL)
        probe.assert_not_called()

    def test_password_sentinel_cannot_be_error_code_or_progress(self):
        self.assertEqual(batch.safe_code(SENTINEL), 'PREFLIGHT_FAILED')
        result = self.create()
        item = result.items.first()
        item.error_code = SENTINEL
        item.progress_message = SENTINEL
        item.save()
        self.assertNotIn(SENTINEL, json.dumps(batch.batch_payload(result, detail=True), default=str))

    def test_dead_parent_recovery_never_releases_healthy_child(self):
        from django.core.management import call_command
        result, item, job = self.active()
        RemoteInstallBatch.objects.filter(pk=result.pk).update(runner_heartbeat_at=timezone.now() - timedelta(seconds=121))
        call_command('reconcile_remote_install_batches', '--apply', stdout=io.StringIO())
        result.refresh_from_db()
        self.assertEqual(result.status, 'RUNNING')
        RemoteInstallJob.objects.filter(pk=job.pk).update(runner_heartbeat_at=timezone.now() - timedelta(seconds=121))
        before = dict(RemoteInstallBatch.objects.values().get())
        call_command('reconcile_remote_install_batches', stdout=io.StringIO())
        self.assertEqual(before, dict(RemoteInstallBatch.objects.values().get()))
        call_command('reconcile_remote_install_batches', '--apply', stdout=io.StringIO())
        result.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(result.status, 'INTERRUPTED')
        self.assertEqual(item.status, 'REVIEW_REQUIRED')
        self.assertIsNone(result.active_slot)

    def test_serial_auto_stall_continues_other_target(self):
        result = self.create()
        order = []
        def dispatch(job, *_):
            order.append(job.target_fqdn)
            RemoteInstallJob.objects.filter(pk=job.pk).update(status='RUNNING' if len(order) == 1 else 'COMPLETED',
                diagnostics={'safe_to_retry': 'NO'}, runner_heartbeat_at=timezone.now() - timedelta(seconds=121))
        with mock.patch.object(batch.threading, 'Thread'), \
                mock.patch.object(batch, 'run_remote_install_preflight', return_value={'status': 'READY'}), \
                mock.patch.object(install, 'start_remote_install', side_effect=dispatch), \
                mock.patch.object(batch.time, 'sleep'):
            batch.run_batch(result.pk, 'admin', SENTINEL)
        result.refresh_from_db()
        self.assertEqual(order, self.names)
        self.assertEqual(result.review_count, 1)
        self.assertEqual(result.success_count, 1)

    def test_retry_review_repeats_absence_proof(self):
        result, _ = self.run_simulated(['OUTCOME_UNKNOWN', 'COMPLETED'])
        with mock.patch.object(install, '_absence_proof') as proof:
            retry = batch.retry_batch(result, self.actor, 'new-admin', SENTINEL)
        self.assertEqual(retry.parent_batch_id, result.pk)
        self.assertEqual(proof.call_args.args[1:], ('new-admin', SENTINEL))

    def test_json_has_no_password_after_partial_spawn_error(self):
        result = self.create()
        with mock.patch.object(batch.subprocess, 'Popen', side_effect=RuntimeError(SENTINEL)):
            with self.assertRaises(install.InstallFailure) as error:
                batch.start_batch(result, 'admin', SENTINEL)
        self.assertNotIn(SENTINEL, str(error.exception))
        self.assertNotIn(SENTINEL, json.dumps(batch.batch_payload(RemoteInstallBatch.objects.get(), detail=True), default=str))

    def test_old_runner_lost_lease_cannot_send_or_rewrite(self):
        result, item, job = self.active(seconds=61)
        batch.resolve_stall(result.pk, item.pk, actor=self.actor)
        with self.assertRaises(install.InstallLeaseLost):
            install._assert_install_lease(job.pk)
        with self.assertRaises(install.InstallLeaseLost):
            install._update(job.pk, 'INSTALLER_STARTED')
        protocol = mock.Mock()
        with mock.patch.object(install, '_new_winrm_protocol', return_value=protocol), self.assertRaises(install.InstallLeaseLost):
            install._remote_script(job.target_fqdn, 'admin', SENTINEL, 'exit 0', 5,
                                   before_send=lambda: install._assert_install_lease(job.pk))
        protocol.send_command_input.assert_not_called()
        with mock.patch.object(install, '_new_winrm_protocol', return_value=protocol), self.assertRaises(install.InstallLeaseLost):
            install._remote_script(job.target_fqdn, 'admin', SENTINEL, 'exit 0', 5, lease_job_id=job.pk)
        protocol.send_command_input.assert_not_called()

    def test_real_stdin_send_holds_job_transaction_lock(self):
        from django.db import connection
        result, item, job = self.active()
        protocol = mock.Mock()
        protocol.get_command_output_raw.return_value = (b'', b'', 0, True)
        seen = []
        def send(*args, **kwargs):
            seen.append(connection.in_atomic_block)
            self.assertEqual(RemoteInstallJob.objects.get(pk=job.pk).active_slot, 'global')
        protocol.send_command_input.side_effect = send
        with mock.patch.object(install, '_new_winrm_protocol', return_value=protocol):
            install._remote_script(job.target_fqdn, 'admin', SENTINEL, 'exit 0', 5, lease_job_id=job.pk)
        self.assertEqual(seen, [True])

    def test_retry_new_parent_and_new_credentials(self):
        result, _ = self.run_simulated(['FAILED', 'COMPLETED'])
        snapshot = list(result.items.values())
        retry = batch.retry_batch(result, self.actor, 'new-user', 'new-synthetic-password')
        self.assertEqual(retry.parent_batch_id, result.pk)
        self.assertEqual(retry.items.get().target_fqdn, self.names[0])
        self.assertEqual(snapshot, list(result.items.values()))

    def test_ambiguous_retry_requires_fresh_proof_and_managed_is_rejected(self):
        result, _ = self.run_simulated(['OUTCOME_UNKNOWN', 'COMPLETED'])
        with mock.patch.object(install, '_absence_proof', side_effect=install.InstallFailure('INSTALL_RECONCILIATION_FAILED')) as proof:
            with self.assertRaises(install.InstallFailure):
                batch.retry_batch(result, self.actor, 'new-user', SENTINEL)
            proof.assert_called_once()
        with mock.patch.object(batch, '_ad_target', side_effect=ProbeFailure('TARGET_MANAGED_OR_CONFLICT')):
            with self.assertRaises(ProbeFailure):
                batch.retry_batch(result, self.actor, 'new-user', SENTINEL)
        self.assertEqual(RemoteInstallBatch.objects.count(), 1)


class BatchRouteTests(BatchTests):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.actor)
        self.url = reverse('agent-install-batches')

    def test_json_create_and_read_are_safe_and_readonly(self):
        with mock.patch.object(batch, 'start_batch'), mock.patch('dashboard.remote_install_batch_views.start_batch'):
            response = self.client.post(self.url, json.dumps({'targets': self.names,
                'username': 'admin', 'password': SENTINEL}), content_type='application/json', secure=True)
        self.assertEqual(response.status_code, 202)
        result = RemoteInstallBatch.objects.get()
        result.diagnostics = {'password': SENTINEL, 'stdout': '<script>alert(1)</script>'}
        result.save()
        snapshot = dict(RemoteInstallBatch.objects.values().get())
        for url in (self.url, response.json()['status_url']):
            read = self.client.get(url)
            self.assertEqual(read.status_code, 200)
            self.assertNotIn(SENTINEL, read.content.decode())
            self.assertNotIn('stdout', read.content.decode())
            self.assertEqual(read['Cache-Control'], 'no-store')
        self.assertEqual(snapshot, dict(RemoteInstallBatch.objects.values().get()))
        self.assertNotIn(SENTINEL, json.dumps(dict(self.client.session)))

    def test_auth_csrf_https_and_arbitrary_fields_are_blocked(self):
        data = json.dumps({'targets': self.names, 'username': 'admin', 'password': SENTINEL})
        self.assertEqual(self.client.post(self.url, data, content_type='application/json').status_code, 403)
        guarded = Client(enforce_csrf_checks=True)
        guarded.force_login(self.actor)
        self.assertEqual(guarded.post(self.url, data, content_type='application/json', secure=True).status_code, 403)
        self.assertEqual(self.client.post(self.url, data[:-1] + ',"ip":"127.0.0.1"}',
                                         content_type='application/json', secure=True).status_code, 409)
        self.actor.is_staff = False
        self.actor.save()
        self.assertIn(self.client.get(self.url).status_code, (302, 403))
        self.assertEqual(RemoteInstallBatch.objects.count(), 0)


class BatchRaceTests(TransactionTestCase):
    def test_concurrent_batches_have_one_global_admission(self):
        actor = get_user_model().objects.create_user('race-admin', is_staff=True)
        gate = threading.Barrier(2)
        def lookup(name):
            gate.wait(timeout=5)
            return target(name)
        def request():
            close_old_connections()
            try:
                return batch.create_batch(['race.control.local'], actor, username='admin', password=SENTINEL).pk
            except install.InstallFailure as exc:
                return exc.code
            finally:
                close_old_connections()
        with mock.patch.object(batch, '_ad_target', side_effect=lookup), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: request(), range(2)))
        self.assertEqual(RemoteInstallBatch.objects.count(), 1)
        self.assertEqual(RemoteInstallBatch.objects.filter(active_slot='global').count(), 1)
        self.assertIn('INSTALL_ALREADY_RUNNING', results)

    def test_batch_and_legacy_race_share_global_lock(self):
        actor = get_user_model().objects.create_user('mixed-race-admin', is_staff=True)
        gate = threading.Barrier(2)
        def lookup(name):
            gate.wait(timeout=5)
            return target(name)
        def request(kind):
            close_old_connections()
            try:
                if kind == 'batch':
                    return batch.create_batch(['race.control.local'], actor, username='admin', password=SENTINEL).pk
                return install.create_remote_install_job('legacy.control.local', actor).pk
            except install.InstallFailure as exc:
                return exc.code
            finally:
                close_old_connections()
        with mock.patch.object(batch, '_ad_target', side_effect=lookup), \
                mock.patch.object(install, '_ad_target', side_effect=lookup), \
                mock.patch.object(install, 'reconcile_stale_jobs'), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(request, ('batch', 'legacy')))
        self.assertEqual(RemoteInstallBatch.objects.count() + RemoteInstallJob.objects.count(), 1)
        self.assertIn('INSTALL_ALREADY_RUNNING', results)
