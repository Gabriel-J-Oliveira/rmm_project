import copy
import json
import uuid
import os
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest import mock
from unittest import skipUnless
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection, close_old_connections, IntegrityError, transaction, OperationalError
from django.test import TestCase, TransactionTestCase, LiveServerTestCase, override_settings, skipUnlessDBFeature
from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from .automation import (save_definition, scheduler_round, control_definition, cancel_occurrence,
                         slot, parse_instant, materialize, known_v2)
from .models import (AgentMachine, AgentJob, AutomationDefinition, AutomationOccurrence,
                     AutomationSchedulerHealth, InventorySnapshot, AuditEvent)

UTC=dt_timezone.utc


class AutomationFixture:
    def prepare(self):
        self.now=timezone.now().replace(microsecond=0)
        self.user=get_user_model().objects.create_superuser('automation-operator',password='synthetic-only')
        self.endpoint,self.token=AgentMachine.create_with_token(hostname='SYNTHETIC-AUTOMATION',domain='isolated.invalid',
            machine_id=str(uuid.uuid4()),agent_version='synthetic-v2',last_seen_at=self.now)
        InventorySnapshot.objects.create(machine=self.endpoint,collected_at=self.now,raw_payload={'snapshot_source':'heartbeat',
            'machine_id':self.endpoint.machine_id,'agent_version':self.endpoint.agent_version,
            'agent':{'capabilities':['jobs_protocol_v2']}})

    def data(self,**changes):
        value={'name':'Synthetic automation','job_type':'collect_logs','payload':{'max_lines':2},
            'endpoint_ids':[str(self.endpoint.pk)],'schedule':{'kind':'once'},'timezone':'America/Sao_Paulo',
            'starts_at':(self.now+timedelta(minutes=5)).isoformat(),'validity_seconds':900,'offline_policy':'wait'}
        value.update(changes)
        return value

    def define(self,**changes):
        return save_definition(self.user,self.data(**changes),now=self.now)


@override_settings(NIGHTOWL_JOB_AUTOMATIONS_ENABLED=True)
class AutomationTests(AutomationFixture,TestCase):
    def setUp(self):
        self.prepare()

    def test_immediate_has_no_scheduler_wait_and_existing_flow_stays_immediate(self):
        definition=self.define(schedule={'kind':'immediate'})
        self.assertEqual(definition.state,'finished')
        job=AgentJob.objects.get()
        self.assertEqual(job.status,'queued')
        self.assertEqual(job.payload,{'max_lines':2})
        self.assertIsNone(job.started_at)
        self.assertEqual(AuditEvent.objects.get(event_type='job.created').actor_type,AuditEvent.ACTOR_USER)
        self.assertFalse(AuditEvent.objects.filter(event_type='job.started').exists())
        self.client.force_login(self.user)
        response=self.client.post(reverse('api-endpoint-job-create',args=[self.endpoint.pk]),{'job_type':'collect_logs'})
        self.assertEqual(response.status_code,201)

    def test_future_and_restart_idempotent_occurrence(self):
        definition=self.define()
        scheduler_round(now=self.now)
        self.assertFalse(AgentJob.objects.exists())
        scheduler_round(now=definition.starts_at)
        scheduler_round(now=definition.starts_at+timedelta(seconds=1))
        self.assertEqual(AgentJob.objects.count(),1)
        self.assertEqual(AutomationOccurrence.objects.count(),1)
        definition.refresh_from_db()
        self.assertEqual(definition.state,'finished')
        self.assertIsNotNone(AutomationSchedulerHealth.objects.get().last_success_at)

    @override_settings(NIGHTOWL_PERSISTED_TELEMETRY_ANALYSIS_SCHEDULER_ENABLED=True,
                       NIGHTOWL_PERSISTED_TELEMETRY_ANALYSIS_SCHEDULER_DRY_RUN=False)
    def test_combined_round_preserves_job_limit_and_caps_analysis_work(self):
        definition=self.define()
        with mock.patch('agents.telemetry_persisted_analysis.scheduled_analysis_round',
                        return_value={'processed':3,'failed':0}) as analysis_round:
            result=scheduler_round(now=definition.starts_at,limit=20)

        self.assertEqual(AgentJob.objects.count(),1)
        analysis_round.assert_called_once_with(
            now=definition.starts_at,limit=5,dry_run=None)
        self.assertEqual(result['processed'],4)
        self.assertEqual(result['failed'],0)

    @override_settings(NIGHTOWL_PERSISTED_TELEMETRY_ANALYSIS_SCHEDULER_ENABLED=True,
                       NIGHTOWL_PERSISTED_TELEMETRY_ANALYSIS_SCHEDULER_DRY_RUN=False,
                       NIGHTOWL_PERSISTED_TELEMETRY_ANALYSIS_FIRST_CAPTURE_POLICY='immediate',
                       NIGHTOWL_TELEMETRY_RETENTION_DAYS={60:6})
    def test_systemic_telemetry_failure_does_not_block_due_job_automation(self):
        definition=self.define()
        result=scheduler_round(now=definition.starts_at,limit=20)
        self.assertEqual(AgentJob.objects.count(),1)
        self.assertEqual(result['telemetry_analysis']['mode'],'failed')
        self.assertEqual(result['telemetry_analysis']['reason'],
                         'analysis_scheduler_requires_seven_day_detail_retention')
        health=AutomationSchedulerHealth.objects.get(key='jobs')
        self.assertEqual(health.processed,1)
        telemetry_health=AutomationSchedulerHealth.objects.get(key='telemetry_analysis')
        self.assertEqual(telemetry_health.last_failure_class,'configuration')
        self.assertIsNone(telemetry_health.suspended_at)

    def test_daily_weekly_monthly_interval_and_month_missing_day(self):
        start=datetime(2026,1,31,12,tzinfo=UTC)
        definition=AutomationDefinition(starts_at=start,timezone_name='America/Sao_Paulo',schedule={'kind':'monthly','monthday':31})
        self.assertEqual(slot(definition,start),datetime(2026,3,31,12,tzinfo=UTC))
        definition.schedule={'kind':'daily'}
        self.assertEqual(slot(definition,start),start+timedelta(days=1))
        definition.schedule={'kind':'weekly','weekdays':[0,2]}
        self.assertEqual(slot(definition,start),datetime(2026,2,2,12,tzinfo=UTC))
        definition.schedule={'kind':'interval','minutes':15}
        self.assertEqual(slot(definition,start),start+timedelta(minutes=15))
        definition.ends_at=start+timedelta(minutes=10)
        self.assertIsNone(slot(definition,start))

    def test_iana_dst_gap_skipped_and_fold_not_duplicated(self):
        zone=ZoneInfo('America/New_York')
        with self.assertRaisesMessage(ValueError,'nonexistent_local_time'):
            parse_instant('2026-03-08T02:30:00',zone)
        first=parse_instant('2026-11-01T01:30:00',zone)
        self.assertEqual(first,datetime(2026,11,1,5,30,tzinfo=UTC))
        definition=AutomationDefinition(starts_at=datetime(2026,3,7,7,30,tzinfo=UTC),timezone_name=zone.key,schedule={'kind':'daily'})
        self.assertEqual(slot(definition,definition.starts_at),datetime(2026,3,9,6,30,tzinfo=UTC))
        definition.starts_at=first
        self.assertEqual(slot(definition,first),datetime(2026,11,2,6,30,tzinfo=UTC))

    def test_long_downtime_coalesces_not_backfilled(self):
        definition=self.define(schedule={'kind':'interval','minutes':15})
        later=definition.starts_at+timedelta(days=60,minutes=2)
        scheduler_round(now=later)
        self.assertEqual(AutomationOccurrence.objects.count(),1)
        occurrence=AutomationOccurrence.objects.get()
        self.assertEqual(occurrence.scheduled_for,definition.starts_at+timedelta(days=60))
        self.assertEqual(occurrence.snapshot['coalesced_from'],definition.starts_at.isoformat())

    def test_offline_wait_reconnect_before_and_after_expiry(self):
        self.endpoint.last_seen_at=self.now-timedelta(days=1)
        self.endpoint.save(update_fields=['last_seen_at'])
        definition=self.define()
        scheduler_round(now=definition.starts_at)
        job=AgentJob.objects.get()
        self.client.defaults['HTTP_AUTHORIZATION']='Bearer '+self.token
        with mock.patch('agents.views.timezone.now',return_value=definition.starts_at+timedelta(seconds=30)):
            self.assertEqual(len(self.client.get(reverse('agent-jobs-pull'),HTTP_X_NIGHTOWL_JOB_PROTOCOL='2').json()['jobs']),1)
        scheduler_round(now=job.expires_at)
        job.refresh_from_db()
        self.assertEqual(job.status,'expired')
        self.assertEqual(self.client.post(reverse('agent-jobs-started',args=[job.pk]),{'machine_id':self.endpoint.machine_id},content_type='application/json').status_code,409)

    def test_offline_skip_and_expire_record_no_jobs(self):
        self.endpoint.last_seen_at=self.now-timedelta(days=1)
        self.endpoint.save(update_fields=['last_seen_at'])
        for policy,status in [('skip','skipped'),('expire','expired')]:
            definition=self.define(offline_policy=policy)
            scheduler_round(now=definition.starts_at)
            self.assertEqual(definition.occurrences.get().status,status)
        self.assertFalse(AgentJob.objects.exists())

    def test_elapsed_window_never_delivers_late(self):
        definition=self.define()
        scheduler_round(now=definition.starts_at+timedelta(hours=2))
        self.assertEqual(definition.occurrences.get().reason,'window_elapsed')
        self.assertFalse(AgentJob.objects.exists())

    def test_cancel_next_and_queued_but_not_running(self):
        definition=self.define(schedule={'kind':'daily'})
        control_definition(self.user,definition.pk,'cancel_next',1,now=self.now)
        scheduler_round(now=definition.starts_at)
        self.assertFalse(AgentJob.objects.exists())
        definition.refresh_from_db()
        scheduler_round(now=definition.next_due_at)
        occurrence=definition.occurrences.get(status='enqueued')
        self.assertTrue(cancel_occurrence(self.user,occurrence.pk))
        self.assertEqual(AgentJob.objects.get().status,'cancelled')
        AgentJob.objects.filter(pk=occurrence.job_id).update(status='running')
        self.assertFalse(cancel_occurrence(self.user,occurrence.pk))
        self.assertEqual(AgentJob.objects.get().status,'running')

    def test_pause_resume_does_not_replay_missed_once(self):
        definition=self.define()
        control_definition(self.user,definition.pk,'pause',1,now=self.now)
        scheduler_round(now=definition.starts_at)
        self.assertFalse(AgentJob.objects.exists())
        result=control_definition(self.user,definition.pk,'resume',1,now=definition.starts_at+timedelta(hours=1))
        self.assertEqual(result.state,'finished')
        self.assertFalse(AgentJob.objects.exists())

    def test_edit_immutable_history_and_revision_conflict(self):
        definition=self.define(schedule={'kind':'daily'})
        scheduler_round(now=definition.starts_at)
        original=definition.occurrences.get().snapshot
        data=self.data(name='Changed',starts_at=(definition.starts_at+timedelta(days=1)).isoformat())
        updated=save_definition(self.user,data,definition.pk,1,now=definition.starts_at)
        self.assertEqual(updated.revision,2)
        self.assertEqual(definition.occurrences.get().snapshot,original)
        with self.assertRaisesMessage(ValueError,'revision_conflict'):
            save_definition(self.user,data,definition.pk,1,now=definition.starts_at)

    def test_overlap_and_real_result_visible(self):
        definition=self.define(schedule={'kind':'interval','minutes':15})
        scheduler_round(now=definition.starts_at)
        AgentJob.objects.update(status='running')
        scheduler_round(now=definition.starts_at+timedelta(minutes=15))
        self.assertEqual(AgentJob.objects.count(),1)
        self.assertTrue(definition.occurrences.filter(reason='overlap_blocked').exists())
        AgentJob.objects.update(status='failed',error_code='SYNTHETIC_FAILURE')
        self.client.force_login(self.user)
        data=self.client.get(reverse('job-automations')).json()
        self.assertTrue(any(row['status']=='failed' and row['reason']=='SYNTHETIC_FAILURE' for row in data['occurrences']))
        summary=next(row for row in data['definitions'] if row['id']==str(definition.pk))
        self.assertEqual(summary['latest_occurrence_status'],'skipped')
        self.assertEqual(summary['latest_occurrence_reason'],'overlap_blocked')
        self.assertEqual(summary['latest_occurrence_endpoint'],self.endpoint.hostname)
        self.assertIsNotNone(summary['latest_occurrence_at'])

    def test_definition_summary_uses_real_occurrence_and_has_no_fake_result_without_history(self):
        definition=self.define()
        self.client.force_login(self.user)
        before=self.client.get(reverse('job-automations')).json()['definitions'][0]
        self.assertIsNone(before['latest_occurrence_at'])
        self.assertIsNone(before['latest_occurrence_status'])
        self.assertIsNone(before['latest_occurrence_reason'])
        self.assertIsNone(before['latest_occurrence_endpoint'])

        scheduler_round(now=definition.starts_at)
        after=self.client.get(reverse('job-automations')).json()['definitions'][0]
        self.assertEqual(after['latest_occurrence_status'],'queued')
        self.assertEqual(after['latest_occurrence_endpoint'],self.endpoint.hostname)
        self.assertEqual(AgentJob.objects.count(),1)

    def test_multiple_targets_identity_change_isolated(self):
        other,_=AgentMachine.create_with_token(hostname='SYNTHETIC-SECOND',machine_id=str(uuid.uuid4()),agent_version='synthetic-v2')
        InventorySnapshot.objects.create(machine=other,collected_at=self.now,raw_payload={'snapshot_source':'heartbeat','machine_id':other.machine_id,
            'agent_version':other.agent_version,'agent':{'capabilities':['jobs_protocol_v2']}})
        definition=self.define(endpoint_ids=[str(self.endpoint.pk),str(other.pk)])
        other.hostname='CHANGED'
        other.save(update_fields=['hostname'])
        scheduler_round(now=definition.starts_at)
        self.assertEqual(AgentJob.objects.count(),1)
        self.assertEqual(definition.occurrences.get(endpoint=other).reason,'endpoint_identity_changed')

    def test_unique_constraint_and_manual_idempotency(self):
        definition=self.define()
        key=str(uuid.uuid4())
        control_definition(self.user,definition.pk,'run_now',1,now=self.now,request_id=key)
        control_definition(self.user,definition.pk,'run_now',1,now=self.now+timedelta(minutes=1),request_id=key)
        self.assertEqual(AgentJob.objects.count(),1)
        original=definition.occurrences.get()
        with self.assertRaises(IntegrityError),transaction.atomic():
            AutomationOccurrence.objects.create(definition=definition,revision=original.revision,endpoint=self.endpoint,
                scheduled_for=original.scheduled_for,expires_at=original.expires_at,status='pending',invocation_key=key)

    def test_no_privilege_bypass_sensitive_jobs_or_arbitrary_parameters(self):
        for kind in ['uninstall_agent','repair_agent','restart_agent','update_agent','install_software','collect_services','run_script']:
            with self.assertRaisesMessage(ValueError,'job_not_schedulable'):
                self.define(job_type=kind,payload={})
        with self.assertRaisesMessage(ValueError,'invalid_ping_count'):
            self.define(job_type='ping',payload={'target':'attacker.invalid'})
        with self.assertRaisesMessage(ValueError,'invalid_log_limit'):
            self.define(payload={'max_lines':True})
        user=get_user_model().objects.create_user('staff-only',is_staff=True)
        with self.assertRaises(PermissionError):
            save_definition(user,self.data(),now=self.now)
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('job-automations')).status_code,403)

    def test_quarantine_ambiguous_identity_and_old_capability(self):
        self.endpoint.hostname='CS-CVEL-0262'
        self.endpoint.save(update_fields=['hostname'])
        with self.assertRaisesMessage(ValueError,'endpoint_identity_unavailable'):
            self.define()
        self.endpoint.hostname='SAFE'
        self.endpoint.save(update_fields=['hostname'])
        InventorySnapshot.objects.all().delete()
        definition=self.define()
        scheduler_round(now=definition.starts_at)
        self.assertEqual(definition.occurrences.get().reason,'jobs_v2_capability_unconfirmed')
        self.assertFalse(AgentJob.objects.exists())

    def test_permission_revoked_after_materialization_blocks_pull(self):
        definition=self.define()
        scheduler_round(now=definition.starts_at)
        self.user.is_active=False
        self.user.save(update_fields=['is_active'])
        self.client.defaults['HTTP_AUTHORIZATION']='Bearer '+self.token
        with mock.patch('agents.views.timezone.now',return_value=definition.starts_at):
            self.assertEqual(self.client.get(reverse('agent-jobs-pull'),HTTP_X_NIGHTOWL_JOB_PROTOCOL='2').json()['jobs'],[])
        self.assertEqual(AgentJob.objects.get().status,'cancelled')

    def test_transient_database_failure_rolls_back_and_bounded_retry(self):
        definition=self.define()
        original=materialize
        def failure(*args,**kwargs):
            original(*args,**kwargs)
            raise OperationalError('synthetic-private-error-not-to-be-recorded')
        with mock.patch('agents.automation.materialize',side_effect=failure):
            for attempt in range(5):
                scheduler_round(now=definition.starts_at+timedelta(hours=attempt))
        definition.refresh_from_db()
        self.assertEqual(definition.state,'paused')
        self.assertEqual(definition.retry_count,5)
        self.assertEqual(definition.last_error,'database_transient_failure')
        self.assertFalse(AgentJob.objects.exists())
        self.assertFalse(AutomationOccurrence.objects.exists())

    def test_backend_validation_and_no_csrf_bypass(self):
        self.client.force_login(self.user)
        response=self.client.post(reverse('job-automations'),self.data(),content_type='application/json')
        self.assertEqual(response.status_code,201)
        self.assertEqual(self.client.get(reverse('job-automations')).json()['definitions'][0]['name'],'Synthetic automation')
        self.assertEqual(self.client.post(reverse('job-automations'),{'garbage':True},content_type='application/json').status_code,400)
        from django.test import Client
        browser=Client(enforce_csrf_checks=True)
        browser.force_login(self.user)
        self.assertEqual(browser.post(reverse('job-automations'),self.data(),content_type='application/json').status_code,403)

    def test_revoked_permission_before_true_start_and_identity_change_blocked(self):
        definition=self.define()
        scheduler_round(now=definition.starts_at)
        self.client.defaults['HTTP_AUTHORIZATION']='Bearer '+self.token
        with mock.patch('agents.views.timezone.now',return_value=definition.starts_at):
            self.client.get(reverse('agent-jobs-pull'),HTTP_X_NIGHTOWL_JOB_PROTOCOL='2')
        self.endpoint.hostname='DIFFERENT'
        self.endpoint.save(update_fields=['hostname'])
        with mock.patch('agents.job_control.timezone.now',return_value=definition.starts_at):
            response=self.client.post(reverse('agent-jobs-started',args=[AgentJob.objects.get().pk]),
                {'machine_id':self.endpoint.machine_id},content_type='application/json')
        self.assertEqual(response.status_code,409)
        self.assertIsNone(AgentJob.objects.get().started_at)

    def test_legacy_pull_does_not_receive_scheduled_work(self):
        definition=self.define()
        scheduler_round(now=definition.starts_at)
        self.client.defaults['HTTP_AUTHORIZATION']='Bearer '+self.token
        with mock.patch('agents.views.timezone.now',return_value=definition.starts_at):
            self.assertEqual(self.client.get(reverse('agent-jobs-pull')).json()['jobs'],[])
        self.assertEqual(AgentJob.objects.get().status,'queued')

    def test_cancel_definition_leaves_sent_and_running_unchanged(self):
        definition=self.define(schedule={'kind':'daily'})
        scheduler_round(now=definition.starts_at)
        job=AgentJob.objects.get()
        job.status='sent'
        job.save(update_fields=['status'])
        control_definition(self.user,definition.pk,'cancel',1)
        job.refresh_from_db()
        self.assertEqual(job.status,'sent')
        self.assertFalse(cancel_occurrence(self.user,definition.occurrences.get().pk))

    def test_invalid_limits_missing_target_and_unique_machine_id(self):
        for data in [{'validity_seconds':0},{'validity_seconds':True},{'schedule':{'kind':'interval','minutes':1}},
                     {'endpoint_ids':[]},{'endpoint_ids':[str(self.endpoint.pk)]*2},{'timezone':'Not/AZone'}]:
            with self.assertRaises(ValueError):
                self.define(**data)
        AgentMachine.create_with_token(hostname='DUPLICATE',machine_id=self.endpoint.machine_id)
        with self.assertRaisesMessage(ValueError,'endpoint_identity_unavailable'):
            self.define()

    def test_removed_target_pauses_without_partial_work(self):
        definition=self.define()
        self.endpoint.delete()
        result=scheduler_round(now=definition.starts_at)
        self.assertEqual(result['failed'],1)
        definition.refresh_from_db()
        self.assertEqual(definition.last_error,'definition_target_unavailable')
        self.assertFalse(AgentJob.objects.exists())

    def test_backoff_skips_attempts_until_due_and_does_not_starve_other_definitions(self):
        first=self.define()
        second=self.define()
        AutomationDefinition.objects.filter(pk=first.pk).update(retry_at=first.starts_at+timedelta(minutes=5))
        result=scheduler_round(now=first.starts_at,limit=1)
        self.assertEqual(result['processed'],1)
        self.assertEqual(AutomationOccurrence.objects.get().definition_id,second.pk)

    def test_audit_failure_rolls_back_definition_and_jobs(self):
        with mock.patch('agents.automation.audit',side_effect=ValueError('synthetic_audit_failure')):
            with self.assertRaises(ValueError):
                self.define(schedule={'kind':'immediate'})
        self.assertFalse(AutomationDefinition.objects.exists())
        self.assertFalse(AgentJob.objects.exists())

    def test_altered_job_payload_is_never_dispatched(self):
        definition=self.define()
        scheduler_round(now=definition.starts_at)
        AgentJob.objects.update(payload={'max_lines':500,'command':'not-authorized'})
        self.client.defaults['HTTP_AUTHORIZATION']='Bearer '+self.token
        with mock.patch('agents.views.timezone.now',return_value=definition.starts_at):
            self.assertEqual(self.client.get(reverse('agent-jobs-pull'),HTTP_X_NIGHTOWL_JOB_PROTOCOL='2').json()['jobs'],[])
        self.assertEqual(AgentJob.objects.get().status,'cancelled')

    def test_non_superuser_permissions_are_independent(self):
        user=get_user_model().objects.create_user('limited-operator',is_staff=True)
        user.user_permissions.add(Permission.objects.get(content_type__app_label='agents',codename='add_agentjob'))
        with self.assertRaises(PermissionError):
            save_definition(user,self.data(),now=self.now)
        user.user_permissions.add(Permission.objects.get(content_type__app_label='agents',codename='add_automationdefinition'))
        user=get_user_model().objects.get(pk=user.pk)
        definition=save_definition(user,self.data(),now=self.now)
        with self.assertRaises(PermissionError):
            control_definition(user,definition.pk,'pause',1)
        with self.assertRaises(PermissionError):
            control_definition(user,definition.pk,'run_now',1,request_id=str(uuid.uuid4()))
        user.user_permissions.add(Permission.objects.get(content_type__app_label='agents',codename='view_automationdefinition'))
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('job-automations')).status_code,403)

    def test_limit_of_fifty_targets_is_not_partially_admitted(self):
        with self.assertRaisesMessage(ValueError,'invalid_targets'):
            self.define(endpoint_ids=[str(uuid.uuid4()) for _ in range(51)])
        self.assertFalse(AutomationDefinition.objects.exists())

    def test_cancel_before_delivery_is_idempotent(self):
        definition=self.define()
        scheduler_round(now=definition.starts_at)
        occurrence=definition.occurrences.get()
        self.assertTrue(cancel_occurrence(self.user,occurrence.pk))
        self.assertTrue(cancel_occurrence(self.user,occurrence.pk))
        self.assertEqual(AgentJob.objects.get().status,'cancelled')

    def test_v2_canonical_and_legacy_alias_are_exact_and_compatible(self):
        snapshot=InventorySnapshot.objects.get(machine=self.endpoint)
        for capabilities in (['jobs_protocol_v2'],['job_protocol_v2'],['jobs_protocol_v2','job_protocol_v2']):
            with self.subTest(capabilities=capabilities):
                snapshot.raw_payload['agent']['capabilities']=capabilities
                snapshot.save(update_fields=['raw_payload'])
                self.assertTrue(known_v2(self.endpoint))
                definition=self.define(schedule={'kind':'immediate'})
                occurrence=definition.occurrences.get()
                self.assertEqual(occurrence.status,'enqueued')
                self.client.defaults['HTTP_AUTHORIZATION']='Bearer '+self.token
                response=self.client.get(reverse('agent-jobs-pull'),HTTP_X_NIGHTOWL_JOB_PROTOCOL='2')
                self.assertEqual([row['id'] for row in response.json()['jobs']],[str(occurrence.job_id)])
                AgentJob.objects.filter(pk=occurrence.job_id).update(status='completed')
        for capabilities in ([],['jobs_protocol_v20'],['future_admin'], 'jobs_protocol_v2', {'jobs_protocol_v2':True}):
            snapshot.raw_payload['agent']['capabilities']=capabilities
            snapshot.save(update_fields=['raw_payload'])
            self.assertFalse(known_v2(self.endpoint))
        snapshot.raw_payload['agent']['capabilities']=['jobs_protocol_v2']
        snapshot.raw_payload['agent_version']='different-version'
        snapshot.save(update_fields=['raw_payload'])
        self.assertFalse(known_v2(self.endpoint))

    def test_legacy_agent_without_v2_keeps_manual_jobs_but_not_automations(self):
        snapshot=InventorySnapshot.objects.get(machine=self.endpoint)
        snapshot.raw_payload['agent']['capabilities']=[]
        snapshot.save(update_fields=['raw_payload'])
        self.client.force_login(self.user)
        response=self.client.post(reverse('api-endpoint-job-create',args=[self.endpoint.pk]),{'job_type':'collect_logs'})
        self.assertEqual(response.status_code,201)
        self.client.logout()
        self.client.defaults['HTTP_AUTHORIZATION']='Bearer '+self.token
        self.assertEqual(len(self.client.get(reverse('agent-jobs-pull')).json()['jobs']),1)
        definition=self.define(schedule={'kind':'immediate'})
        self.assertEqual(definition.occurrences.get().reason,'jobs_v2_capability_unconfirmed')
        self.assertEqual(AgentJob.objects.count(),1)

    @override_settings(NIGHTOWL_SERVICE_COLLECTION_ENABLED=True,NIGHTOWL_SOFTWARE_INSTALL_ENABLED=True,
                       NIGHTOWL_APPROVED_SOFTWARE_IDS=['synthetic-package'])
    def test_protocol_capability_does_not_grant_internal_privileged_capabilities(self):
        from .internal_jobs import create_internal_job
        snapshot=InventorySnapshot.objects.get(machine=self.endpoint)
        snapshot.raw_payload['agent']['capabilities']=['jobs_protocol_v2','job_protocol_v2','unknown_privileged_operation']
        snapshot.save(update_fields=['raw_payload'])
        for kind,payload in [('collect_services',{}),('install_software',{'package_id':'synthetic-package'})]:
            with self.assertRaisesMessage(ValueError,'capability_unconfirmed'):
                create_internal_job(self.user,self.endpoint.pk,kind,payload)
        self.assertFalse(AgentJob.objects.exists())

    @override_settings(NIGHTOWL_JOB_AUTOMATIONS_ENABLED=False)
    def test_scheduler_is_default_disabled(self):
        self.define()
        with self.assertRaisesMessage(ValueError,'scheduler_disabled'):
            scheduler_round(now=self.now+timedelta(hours=1))
        self.assertFalse(AgentJob.objects.exists())


@override_settings(NIGHTOWL_JOB_AUTOMATIONS_ENABLED=False)
class EndpointAssignedPolicyApiTests(AutomationFixture, TestCase):
    def setUp(self):
        self.prepare()
        self.other, _ = AgentMachine.create_with_token(hostname='SYNTHETIC-OTHER', domain='isolated.invalid',
            machine_id=str(uuid.uuid4()), agent_version='synthetic-v2', last_seen_at=self.now)
        InventorySnapshot.objects.create(machine=self.other, collected_at=self.now, raw_payload={
            'snapshot_source': 'heartbeat', 'machine_id': self.other.machine_id,
            'agent_version': self.other.agent_version, 'agent': {'capabilities': ['jobs_protocol_v2']}})
        self.client.force_login(self.user)

    def run_url(self, definition, endpoint=None):
        return reverse('api-endpoint-automation-policy-run', kwargs={
            'endpoint_id': (endpoint or self.endpoint).pk, 'definition_id': definition.pk})

    def list_url(self, endpoint=None):
        return reverse('api-endpoint-automation-policies', kwargs={'endpoint_id': (endpoint or self.endpoint).pk})

    def post_run(self, definition, request_id=None, endpoint=None):
        return self.client.post(self.run_url(definition, endpoint), data=json.dumps({
            'revision': definition.revision, 'request_id': request_id or str(uuid.uuid4())}),
            content_type='application/json')

    def test_list_matches_assignments_and_history_by_endpoint_uuid_only(self):
        shared = self.define(endpoint_ids=[str(self.endpoint.pk), str(self.other.pk)])
        only_self = self.define()
        only_other = self.define(endpoint_ids=[str(self.other.pk)])
        targeted = self.post_run(shared, str(uuid.uuid4()))
        self.assertEqual(targeted.status_code, 202, targeted.content)
        result = self.client.get(self.list_url()).json()
        self.assertEqual({row['id'] for row in result['policies']}, {str(shared.pk), str(only_self.pk)})
        self.assertEqual(result['endpoint']['id'], str(self.endpoint.pk))
        self.assertFalse(result['scheduler_enabled'])
        self_row = next(row for row in result['policies'] if row['id'] == str(shared.pk))
        self.assertEqual(self_row['history'][0]['status'], 'queued')
        self.assertEqual(self_row['history'][0]['job_id'], str(AgentJob.objects.get().pk))
        other_result = self.client.get(self.list_url(self.other)).json()
        self.assertEqual({row['id'] for row in other_result['policies']}, {str(shared.pk), str(only_other.pk)})
        self.assertEqual(other_result['policies'][0]['history'], [])

    def test_manual_execution_targets_only_this_endpoint_and_replay_is_idempotent(self):
        definition = self.define(endpoint_ids=[str(self.endpoint.pk), str(self.other.pk)])
        request_id = str(uuid.uuid4())
        first = self.post_run(definition, request_id)
        replay = self.post_run(definition, request_id)
        self.assertEqual(first.status_code, 202, first.content)
        self.assertEqual(replay.status_code, 202, replay.content)
        self.assertEqual(first.json()['occurrence']['id'], replay.json()['occurrence']['id'])
        self.assertEqual(AgentJob.objects.count(), 1)
        self.assertEqual(AgentJob.objects.get().endpoint_id, self.endpoint.pk)
        self.assertEqual(AutomationOccurrence.objects.count(), 1)
        self.assertEqual(AutomationOccurrence.objects.get().endpoint_id, self.endpoint.pk)
        events = list(AuditEvent.objects.filter(event_type='automation.run_now_endpoint').order_by('pk'))
        self.assertEqual(len(events), 2, 'Both original request and idempotent replay remain auditable')
        for event in events:
            self.assertEqual(event.metadata['endpoint_id'], str(self.endpoint.pk))
            self.assertEqual(event.metadata['scope'], 'single_endpoint')

    def test_scheduler_flag_is_separate_from_authorized_manual_execution(self):
        definition = self.define()
        result = self.client.get(self.list_url()).json()
        row = result['policies'][0]
        self.assertFalse(result['scheduler_enabled'])
        self.assertTrue(row['can_run_now'])
        self.assertEqual(row['history'], [])
        response = self.post_run(definition)
        self.assertEqual(response.status_code, 202, response.content)
        self.assertEqual(AgentJob.objects.count(), 1)

    def test_user_without_execute_permission_can_view_but_cannot_run(self):
        definition = self.define()
        limited = get_user_model().objects.create_user('endpoint-policy-view', is_staff=True)
        for code in ('view_automationdefinition', 'view_agentmachine', 'view_agentjob'):
            limited.user_permissions.add(Permission.objects.get(content_type__app_label='agents', codename=code))
        self.client.force_login(limited)
        response = self.client.get(self.list_url())
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['policies'][0]['can_run_now'])
        self.assertEqual(response.json()['policies'][0]['run_block_reason'], 'permission_required')
        self.assertEqual(self.post_run(definition).status_code, 403)

    def test_paused_and_unassigned_policies_cannot_enqueue(self):
        for state in ('paused', 'cancelled'):
            with self.subTest(state=state):
                inactive = self.define()
                inactive.state = state
                inactive.save(update_fields=['state'])
                row = next(row for row in self.client.get(self.list_url()).json()['policies'] if row['id'] == str(inactive.pk))
                self.assertFalse(row['can_run_now'])
                self.assertEqual(self.post_run(inactive).status_code, 409)
        assigned_elsewhere = self.define(endpoint_ids=[str(self.other.pk)])
        self.assertEqual(self.post_run(assigned_elsewhere).status_code, 409)
        self.assertFalse(AgentJob.objects.exists())

    def test_ambiguous_identity_is_blocked_and_no_job_is_created(self):
        definition = self.define()
        duplicate, _ = AgentMachine.create_with_token(hostname=self.endpoint.hostname,
            domain=self.endpoint.domain, machine_id=str(uuid.uuid4()), agent_version='synthetic-v2')
        snapshot = InventorySnapshot.objects.get(machine=self.endpoint)
        snapshot.raw_payload['machine_id'] = duplicate.machine_id
        snapshot.save(update_fields=['raw_payload'])
        self.assertFalse(self.client.get(self.list_url()).json()['policies'][0]['can_run_now'])
        response = self.post_run(definition)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['occurrence']['status'], 'blocked')
        self.assertEqual(response.json()['occurrence']['reason'], 'endpoint_identity_unavailable')
        self.assertFalse(AgentJob.objects.exists())

    def test_offline_skip_policy_records_skip_without_remote_job(self):
        self.endpoint.last_seen_at = self.now - timedelta(days=1)
        self.endpoint.save(update_fields=['last_seen_at'])
        definition = self.define(offline_policy='skip')
        self.assertFalse(self.client.get(self.list_url()).json()['policies'][0]['can_run_now'])
        response = self.post_run(definition)
        self.assertEqual(response.json()['occurrence']['status'], 'skipped')
        self.assertEqual(response.json()['occurrence']['reason'], 'endpoint_offline')
        self.assertFalse(AgentJob.objects.exists())

    def test_targeted_run_requires_csrf_and_exact_request_contract(self):
        definition = self.define()
        from django.test import Client
        browser = Client(enforce_csrf_checks=True)
        browser.force_login(self.user)
        payload = json.dumps({'revision': definition.revision, 'request_id': str(uuid.uuid4())})
        self.assertEqual(browser.post(self.run_url(definition), payload, content_type='application/json').status_code, 403)
        self.assertEqual(self.client.post(self.run_url(definition), data='{}', content_type='application/json').status_code, 400)


@override_settings(NIGHTOWL_JOB_AUTOMATIONS_ENABLED=True)
class AutomationPostgresTests(AutomationFixture,TransactionTestCase):
    def setUp(self):
        self.prepare()

    @skipUnlessDBFeature('has_select_for_update_skip_locked')
    def test_two_scheduler_instances_one_occurrence_per_endpoint(self):
        definition=self.define()
        def run():
            close_old_connections()
            try:
                return scheduler_round(now=definition.starts_at)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _:run(),range(2)))
        self.assertEqual(AutomationOccurrence.objects.count(),1)
        self.assertEqual(AgentJob.objects.count(),1)

    @skipUnlessDBFeature('has_select_for_update')
    def test_concurrent_targeted_replay_creates_one_job_and_never_other_target(self):
        other, _ = AgentMachine.create_with_token(hostname='SYNTHETIC-SECOND', domain='isolated.invalid',
            machine_id=str(uuid.uuid4()), agent_version='synthetic-v2', last_seen_at=self.now)
        InventorySnapshot.objects.create(machine=other, collected_at=self.now, raw_payload={
            'snapshot_source': 'heartbeat', 'machine_id': other.machine_id,
            'agent_version': other.agent_version, 'agent': {'capabilities': ['jobs_protocol_v2']}})
        definition = self.define(endpoint_ids=[str(self.endpoint.pk), str(other.pk)])
        request_id = str(uuid.uuid4())

        def run_targeted():
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.user.pk)
                control_definition(actor, definition.pk, 'run_now_endpoint', definition.revision,
                    request_id=request_id, endpoint_id=self.endpoint.pk)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: run_targeted(), range(2)))
        self.assertEqual(AutomationOccurrence.objects.count(), 1)
        self.assertEqual(AgentJob.objects.count(), 1)
        self.assertEqual(AgentJob.objects.get().endpoint_id, self.endpoint.pk)


@override_settings(NIGHTOWL_JOB_AUTOMATIONS_ENABLED=True)
class AutomationBrowserTests(AutomationFixture,LiveServerTestCase):
    @skipUnless(os.environ.get('NIGHTOWL_TEST_BROWSER_E2E')=='1','Explicit isolated browser gate required')
    def test_real_ui_and_persisted_automations(self):
        self.prepare()
        other,_=AgentMachine.create_with_token(hostname='SYNTHETIC-SECOND',machine_id=str(uuid.uuid4()),
            agent_version='synthetic-v2',last_seen_at=self.now)
        InventorySnapshot.objects.create(machine=other,collected_at=self.now,raw_payload={'snapshot_source':'heartbeat',
            'machine_id':other.machine_id,'agent_version':other.agent_version,'agent':{'capabilities':['jobs_protocol_v2']}})
        scheduler_round(now=self.now)
        self.client.force_login(self.user)
        result=subprocess.run(['node',str(Path(settings.BASE_DIR)/'tests/frontend/test_job_automations_e2e.cjs')],
            env={**os.environ,'NIGHTOWL_E2E_SERVER':self.live_server_url,'NIGHTOWL_E2E_SESSION':self.client.cookies['sessionid'].value},
            capture_output=True,text=True,timeout=110)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(AgentJob.objects.count(),2)
        self.assertEqual(AutomationDefinition.objects.count(),2)
        self.assertEqual(AutomationOccurrence.objects.filter(status='cancelled').count(),2)
        print(result.stdout.strip())

    @skipUnless(os.environ.get('NIGHTOWL_TEST_BROWSER_E2E')=='1','Explicit isolated browser gate required')
    def test_endpoint_policy_run_is_scoped_and_confirmed_in_real_ui(self):
        self.prepare()
        other, _ = AgentMachine.create_with_token(hostname='SYNTHETIC-NOT-SELECTED', domain='isolated.invalid',
            machine_id=str(uuid.uuid4()), agent_version='synthetic-v2', last_seen_at=self.now)
        InventorySnapshot.objects.create(machine=other, collected_at=self.now, raw_payload={
            'snapshot_source': 'heartbeat', 'machine_id': other.machine_id,
            'agent_version': other.agent_version, 'agent': {'capabilities': ['jobs_protocol_v2']}})
        name = '<img src=x onerror=alert(1)> Endpoint-only policy'
        definition = self.define(name=name, endpoint_ids=[str(self.endpoint.pk), str(other.pk)])
        self.client.force_login(self.user)
        result = subprocess.run(['node', str(Path(settings.BASE_DIR) / 'tests/frontend/test_endpoint_policy_e2e.cjs')],
            env={**os.environ, 'NIGHTOWL_E2E_SERVER': self.live_server_url,
                 'NIGHTOWL_E2E_SESSION': self.client.cookies['sessionid'].value,
                 'NIGHTOWL_E2E_ENDPOINT': str(self.endpoint.pk),
                 'NIGHTOWL_E2E_ENDPOINT_NAME': self.endpoint.hostname,
                 'NIGHTOWL_E2E_POLICY': str(definition.pk),
                 'NIGHTOWL_E2E_POLICY_NAME': name}, capture_output=True, text=True, timeout=110)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(AgentJob.objects.count(), 1)
        self.assertEqual(AgentJob.objects.get().endpoint_id, self.endpoint.pk)
        self.assertEqual(AutomationOccurrence.objects.count(), 1)
        self.assertEqual(AutomationOccurrence.objects.get().endpoint_id, self.endpoint.pk)
        self.assertFalse(AutomationOccurrence.objects.filter(endpoint=other).exists())
        print(result.stdout.strip())
