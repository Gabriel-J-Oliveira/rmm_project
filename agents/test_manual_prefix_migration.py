from datetime import timedelta

from django.apps import apps as current_apps
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone

from agents.models import hash_manual_validation_token


class ManualPrefixMigrationTests(TransactionTestCase):
    def test_0046_redacts_legacy_prefixes_without_changing_credentials_or_history(self):
        old_target = [('agents', '0045_alter_terminalsession_mode')]
        new_target = [('agents', '0046_deployment_batch')]
        executor = MigrationExecutor(connection)
        try:
            executor.migrate(old_target)
            old = executor.loader.project_state(old_target).apps
            Enrollment = old.get_model('agents', 'AgentEnrollmentToken')
            Manual = old.get_model('agents', 'AgentManualValidationToken')
            Log = old.get_model('agents', 'AgentEnrollmentLog')
            Audit = old.get_model('agents', 'AuditEvent')
            raw = 'manual_1234567890'
            enrollment = Enrollment.objects.create(name='legacy', token_hash='synthetic-enrollment-hash')
            manual = Manual.objects.create(name='legacy', token_hash=hash_manual_validation_token(raw),
                prefix=raw, expires_at=timezone.now()+timedelta(minutes=30), enrollment_token_id=enrollment.pk)
            log = Log.objects.create(hostname='PC', status='success', enrollment_token_id=enrollment.pk,
                message='preserve', metadata={'manual_validation_token_prefix': raw, 'other': 'preserve'})
            audit = Audit.objects.create(event_type='synthetic', title='preserve',
                metadata={'prefix': raw, 'other': 'preserve'})
            executor = MigrationExecutor(connection)
            executor.migrate(new_target)
            manual = current_apps.get_model('agents', 'AgentManualValidationToken').objects.get(pk=manual.pk)
            log = current_apps.get_model('agents', 'AgentEnrollmentLog').objects.get(pk=log.pk)
            audit = current_apps.get_model('agents', 'AuditEvent').objects.get(pk=audit.pk)
            self.assertEqual(manual.token_hash, hash_manual_validation_token(raw))
            self.assertEqual(manual.enrollment_token_id, enrollment.pk)
            self.assertTrue(manual.can_be_used())
            self.assertEqual(manual.prefix, raw[:10])
            self.assertEqual(log.metadata, {'manual_validation_token_prefix': raw[:10], 'other': 'preserve'})
            self.assertEqual((log.message, log.enrollment_token_id), ('preserve', enrollment.pk))
            self.assertEqual(audit.metadata, {'prefix': raw[:10], 'other': 'preserve'})
            self.assertEqual(audit.title, 'preserve')
        finally:
            MigrationExecutor(connection).migrate(new_target)
