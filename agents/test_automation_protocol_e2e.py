"""One real local Windows read-only job, released only after its calendar slot."""
import os
import json
import subprocess
import time
from pathlib import Path
from datetime import timedelta

from django.conf import settings
from django.test import LiveServerTestCase, override_settings, Client
from django.utils import timezone

from .test_automation import AutomationFixture
from .automation import save_definition, scheduler_round, known_v2
from .models import AgentJob, AgentJobResultReceipt, AuditEvent, InventorySnapshot
from django.urls import reverse


@override_settings(NIGHTOWL_JOB_AUTOMATIONS_ENABLED=True)
class AutomationProtocolE2ETests(AutomationFixture,LiveServerTestCase):
    def ingest_actual_worker_heartbeat(self):
        dll=Path(settings.BASE_DIR)/'NightOwl.Agent.Windows.Tests/bin/Release/net8.0-windows/NightOwl.Agent.Windows.Tests.dll'
        result=subprocess.run(['dotnet',str(dll),'--heartbeat-contract'],env={**os.environ,
            'NIGHTOWL_TEST_MACHINE':self.endpoint.machine_id},capture_output=True,text=True,timeout=35)
        self.assertEqual(result.returncode,0,'Actual Worker heartbeat contract failed; process output withheld.')
        body=json.loads(next(line.split('=',1)[1] for line in result.stdout.splitlines() if line.startswith('WORKER_HEARTBEAT_CONTRACT=')))
        # Match the local test process's real native identity only in this isolated DB.
        self.endpoint.hostname=body['hostname']
        self.endpoint.domain=body.get('domain') or ''
        self.endpoint.save(update_fields=['hostname','domain'])
        InventorySnapshot.objects.filter(machine=self.endpoint).delete()
        agent=Client(HTTP_AUTHORIZATION='Bearer '+self.token)
        response=agent.post(reverse('agent-heartbeat'),body,content_type='application/json')
        self.assertEqual(response.status_code,200,'Worker heartbeat rejected by actual Django ingestion.')
        self.endpoint.refresh_from_db()
        snapshot=InventorySnapshot.objects.get(machine=self.endpoint,raw_payload__snapshot_source='heartbeat')
        capabilities=snapshot.raw_payload['agent']['capabilities']
        self.assertIn('jobs_protocol_v2',capabilities)
        self.assertIn('job_protocol_v2',capabilities)
        self.assertNotIn('unknown_privileged_operation',capabilities)
        self.assertNotIn('install_software',capabilities)
        self.assertTrue(known_v2(self.endpoint))
        return dll

    def execute_actual_job(self,dll,job):
        result=subprocess.run(['dotnet',str(dll),'--jobs-e2e'],env={**os.environ,
            'NIGHTOWL_TEST_SERVER':self.live_server_url,'NIGHTOWL_TEST_TOKEN':self.token,
            'NIGHTOWL_TEST_MACHINE':self.endpoint.machine_id,'NIGHTOWL_TEST_JOB_COUNT':'1'},
            capture_output=True,text=True,timeout=90)
        self.assertEqual(result.returncode,0,'Scheduled .NET execution failed; private process output withheld.')
        job.refresh_from_db()
        self.assertEqual(job.status,'completed')
        self.assertTrue(AgentJobResultReceipt.objects.filter(job=job).exists())

    def test_calendar_occurrence_uses_existing_dotnet_execution_and_exact_receipt(self):
        self.prepare()
        dll=self.ingest_actual_worker_heartbeat()
        due=timezone.now()+timedelta(seconds=2)
        definition=save_definition(self.user,self.data(starts_at=due.isoformat()),now=timezone.now())
        scheduler_round()
        self.assertFalse(AgentJob.objects.exists())
        time.sleep(max(0,(due-timezone.now()).total_seconds())+.05)
        scheduler_round()
        job=AgentJob.objects.get()
        self.assertGreaterEqual(job.created_at,due)
        self.execute_actual_job(dll,job)
        self.assertGreaterEqual(job.started_at,due)
        self.assertTrue(AgentJobResultReceipt.objects.filter(job=job).exists())
        self.assertTrue(AuditEvent.objects.filter(event_type='job.started',metadata__job_id=str(job.pk)).exists())
        scheduler_round()
        self.assertEqual(AgentJob.objects.count(),1)
        self.assertEqual(definition.occurrences.get().job_id,job.pk)
        print('PASS_REAL_CALENDAR_DJANGO_DOTNET_EXACT_ACK=YES (isolated loopback, local read-only collect_logs)')

    def test_actual_worker_capability_allows_immediate_and_recurring_first_occurrence(self):
        self.prepare()
        dll=self.ingest_actual_worker_heartbeat()
        immediate=save_definition(self.user,self.data(schedule={'kind':'immediate'}),now=timezone.now())
        self.execute_actual_job(dll,immediate.occurrences.get().job)
        due=timezone.now()+timedelta(seconds=1)
        recurring=save_definition(self.user,self.data(schedule={'kind':'interval','minutes':15},starts_at=due.isoformat()),now=timezone.now())
        time.sleep(max(0,(due-timezone.now()).total_seconds())+.05)
        scheduler_round()
        job=recurring.occurrences.get().job
        self.execute_actual_job(dll,job)
        recurring.refresh_from_db()
        self.assertEqual(recurring.next_due_at,due+timedelta(minutes=15))
        # Calendar advancement is simulated, not a claimed real 15-minute soak.
        scheduler_round(now=recurring.next_due_at)
        scheduler_round(now=recurring.next_due_at)
        self.assertEqual(recurring.occurrences.count(),2)
        self.assertEqual(AgentJob.objects.count(),3)
        print('PASS_ACTUAL_WORKER_HEARTBEAT_BACKEND_IMMEDIATE_AND_RECURRENCE=YES (second recurring slot uses simulated clock)')
