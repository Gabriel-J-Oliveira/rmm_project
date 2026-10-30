import uuid
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import AgentMachine, AgentJob, AgentJobResultReceipt, AuditEvent, InventorySnapshot
from .internal_jobs import create_internal_job, validate_internal_payload


class JobControlTests(TestCase):
    def setUp(self):
        self.machine,token=AgentMachine.create_with_token(hostname='SYNTHETIC-JOBS',machine_id=str(uuid.uuid4()))
        self.agent=Client(HTTP_AUTHORIZATION='Bearer '+token)
        self.job=AgentJob.objects.create(endpoint=self.machine,job_type='ping',payload={'target':'127.0.0.1','count':2})

    def pull(self):
        return self.agent.get(reverse('agent-jobs-pull'),HTTP_X_NIGHTOWL_JOB_PROTOCOL='2')

    def test_signal_authenticated_scoped_and_does_not_claim_or_audit_idle_polls(self):
        url=reverse('agent-jobs-signal')
        self.assertIn(self.client.get(url).status_code,(401,403))
        before=AuditEvent.objects.count()
        self.assertTrue(self.agent.get(url).json()['available'])
        self.assertEqual(AuditEvent.objects.count(),before)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status,'queued')
        other,token=AgentMachine.create_with_token(hostname='SYNTHETIC-OTHER',machine_id=str(uuid.uuid4()))
        second=Client(HTTP_AUTHORIZATION='Bearer '+token)
        self.assertFalse(second.get(url).json()['available'])
        self.assertEqual(second.post(reverse('agent-jobs-started',args=[self.job.pk]),{'machine_id':other.machine_id},content_type='application/json').status_code,404)

    def test_v2_delivery_then_explicit_start_is_idempotent_and_audited(self):
        response=self.pull()
        self.assertEqual(len(response.json()['jobs']),1)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status,'sent')
        self.assertIsNone(self.job.started_at)
        self.assertFalse(AuditEvent.objects.filter(event_type='job.started').exists())
        url=reverse('agent-jobs-started',args=[self.job.pk])
        body={'machine_id':self.machine.machine_id}
        self.assertEqual(self.agent.post(url,body,content_type='application/json').status_code,200)
        self.job.refresh_from_db()
        first=self.job.started_at
        self.assertEqual(self.agent.post(url,body,content_type='application/json').status_code,200)
        self.job.refresh_from_db()
        self.assertEqual(self.job.started_at,first)
        self.assertEqual(AuditEvent.objects.filter(event_type='job.started').count(),1)
        self.assertEqual(self.pull().json()['jobs'],[])

    def test_lost_delivery_response_reappears_without_reexecuting_running_jobs(self):
        self.pull()
        self.assertEqual(self.pull().json()['jobs'],[])
        AgentJob.objects.filter(pk=self.job.pk).update(dispatched_at=timezone.now()-timedelta(seconds=31))
        self.assertEqual(self.pull().json()['jobs'][0]['id'],str(self.job.pk))

    def test_expired_cancelled_or_wrong_identity_cannot_start(self):
        self.pull()
        url=reverse('agent-jobs-started',args=[self.job.pk])
        self.assertEqual(self.agent.post(url,{'machine_id':str(uuid.uuid4())},content_type='application/json').status_code,409)
        AgentJob.objects.filter(pk=self.job.pk).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.agent.post(url,{'machine_id':self.machine.machine_id},content_type='application/json').status_code,409)
        self.job.refresh_from_db()
        self.assertIsNone(self.job.started_at)

    def test_legacy_agent_still_receives_original_contract(self):
        self.assertEqual(self.agent.get(reverse('agent-jobs-pull')).json()['jobs'][0]['payload']['count'],2)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status,'sent')

    def test_signal_allows_expiration_cleanup_without_dispatching_expired_work(self):
        AgentJob.objects.filter(pk=self.job.pk).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertTrue(self.agent.get(reverse('agent-jobs-signal')).json()['available'])
        self.assertEqual(self.pull().json()['jobs'],[])
        self.job.refresh_from_db()
        self.assertEqual(self.job.status,'expired')
        self.assertFalse(self.agent.get(reverse('agent-jobs-signal')).json()['available'])

    def test_final_ack_replay_preserves_exact_receipt_and_rejects_conflict(self):
        self.pull()
        payload={'job_id':str(self.job.pk),'machine_id':self.machine.machine_id,'status':'completed','exit_code':0,'stdout':'synthetic-only'}
        url=reverse('agent-jobs-result')
        for _ in range(2):
            self.assertEqual(self.agent.post(url,payload,content_type='application/json',HTTP_IDEMPOTENCY_KEY='synthetic-result').status_code,200)
        self.assertEqual(AgentJobResultReceipt.objects.count(),1)
        self.assertEqual(self.agent.post(url,{**payload,'stdout':'conflicting'},content_type='application/json',HTTP_IDEMPOTENCY_KEY='synthetic-result').status_code,409)

    def test_typed_contracts_reject_arbitrary_commands_and_bad_limits(self):
        self.assertEqual(validate_internal_payload('collect_services',{'names':['EventLog'],'limit':1})['limit'],1)
        for value in ({'names':['x;whoami']},{'limit':True},{'limit':101},{'command':'whoami'}):
            with self.assertRaises(ValueError): validate_internal_payload('collect_services',value)
        with override_settings(NIGHTOWL_APPROVED_SOFTWARE_IDS=['synthetic']):
            self.assertEqual(validate_internal_payload('install_software',{'package_id':'synthetic'}),{'package_id':'synthetic'})
            with self.assertRaises(ValueError): validate_internal_payload('install_software',{'package_id':'synthetic','url':'https://untrusted.invalid'})
        with self.assertRaises(ValueError): validate_internal_payload('install_software',{'package_id':'not-approved'})

    def test_internal_jobs_default_disabled_and_unauthorized_operator_denied(self):
        user=get_user_model().objects.create_user('synthetic-user',password='synthetic-only')
        with self.assertRaises(PermissionError): create_internal_job(user,self.machine.pk,'collect_services',{})
        admin=get_user_model().objects.create_superuser('synthetic-admin',password='synthetic-only')
        with self.assertRaises(ValueError): create_internal_job(admin,self.machine.pk,'install_software',{'package_id':'synthetic'})

    @override_settings(NIGHTOWL_SERVICE_COLLECTION_ENABLED=True)
    def test_internal_capability_conflict_and_completion_gates(self):
        admin=get_user_model().objects.create_superuser('synthetic-admin',password='synthetic-only')
        AgentJob.objects.filter(pk=self.job.pk).update(status='completed')
        with self.assertRaises(ValueError): create_internal_job(admin,self.machine.pk,'collect_services',{})
        InventorySnapshot.objects.create(machine=self.machine,hostname=self.machine.hostname,collected_at=timezone.now(),
            raw_payload={'snapshot_source':'heartbeat','machine_id':self.machine.machine_id,'agent_version':self.machine.agent_version,
                         'agent':{'capabilities':['collect_services']}})
        job=create_internal_job(admin,self.machine.pk,'collect_services',{'names':['EventLog'],'limit':1})
        with self.assertRaises(ValueError): create_internal_job(admin,self.machine.pk,'collect_services',{})
        self.pull()
        bad={'job_id':str(job.pk),'status':'completed','result':{'machine_id':self.machine.machine_id,'output':{'services':[]}}}
        url=reverse('agent-jobs-result')
        self.assertEqual(self.agent.post(url,bad,content_type='application/json',HTTP_IDEMPOTENCY_KEY='synthetic-internal').status_code,400)
        self.assertFalse(AgentJobResultReceipt.objects.filter(result_id='synthetic-internal').exists())
        bad['result']['output']={'schema_version':1,'read_only':True,'services':[{'name':'EventLog','status':'Running'}]}
        self.assertEqual(self.agent.post(url,bad,content_type='application/json',HTTP_IDEMPOTENCY_KEY='synthetic-internal').status_code,200)

    def test_late_final_result_receives_ack_without_overwriting_history(self):
        AgentJob.objects.filter(pk=self.job.pk).update(status='timed_out')
        payload={'job_id':str(self.job.pk),'status':'completed','stdout':'late'}
        response=self.agent.post(reverse('agent-jobs-result'),payload,content_type='application/json',HTTP_IDEMPOTENCY_KEY='synthetic-late')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['result_id'],'synthetic-late')
        self.job.refresh_from_db()
        self.assertEqual(self.job.status,'timed_out')

    def test_dashboard_uses_persisted_jobs_with_permissions_and_no_mock_rows(self):
        url=reverse('jobs-data')
        admin=get_user_model().objects.create_superuser('synthetic-admin',password='synthetic-only')
        self.client.force_login(admin)
        data=self.client.get(url).json()
        self.assertEqual(data['source'],'persisted')
        self.assertEqual([job['id'] for job in data['jobs']],[str(self.job.pk)])
        self.assertIn('history',data['jobs'][0])
        user=get_user_model().objects.create_user('synthetic-viewer',password='synthetic-only')
        self.client.force_login(user)
        denied=self.client.get(url)
        self.assertIn(denied.status_code,(302,403))
        self.assertNotIn(str(self.job.pk),denied.content.decode())

    def test_frontend_queue_is_bounded_without_partial_creation(self):
        admin=get_user_model().objects.create_superuser('synthetic-admin',password='synthetic-only')
        self.client.force_login(admin)
        AgentJob.objects.bulk_create([AgentJob(endpoint=self.machine,job_type='ping') for _ in range(49)])
        response=self.client.post(reverse('api-endpoint-job-create',args=[self.machine.pk]),{'action':'ping'})
        self.assertEqual(response.status_code,409)
        self.assertEqual(response.json()['error'],'endpoint_job_queue_full')
        self.assertEqual(AgentJob.objects.count(),50)

    @override_settings(NIGHTOWL_SOFTWARE_INSTALL_ENABLED=True,NIGHTOWL_SERVICE_COLLECTION_ENABLED=True)
    def test_internal_types_are_not_frontend_dispatch_actions(self):
        admin=get_user_model().objects.create_superuser('synthetic-admin',password='synthetic-only')
        self.client.force_login(admin)
        url=reverse('api-endpoint-job-create',args=[self.machine.pk])
        for kind in ('install_software','collect_services'):
            self.assertEqual(self.client.post(url,{'job_type':kind}).status_code,400)
        self.assertEqual(AgentJob.objects.count(),1)
