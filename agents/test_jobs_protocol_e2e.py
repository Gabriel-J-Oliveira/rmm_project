"""Real local HTTP, .NET dispatcher/executor and PostgreSQL-compatible API contract.

The database here is isolated SQLite; it does not prove PostgreSQL concurrency.
"""
import json
import math
import statistics
import os
import subprocess
import uuid
from pathlib import Path
from django.conf import settings
from django.test import LiveServerTestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from .models import AgentMachine,AgentJob,AgentJobResultReceipt


class JobsProtocolE2ETests(LiveServerTestCase):
    def test_real_dotnet_dispatch_start_result_and_dashboard(self):
        dll=Path(settings.BASE_DIR)/'NightOwl.Agent.Windows.Tests/bin/Release/net8.0-windows/NightOwl.Agent.Windows.Tests.dll'
        self.assertTrue(dll.exists(),'Build Windows tests in Release before this E2E.')
        endpoint,token=AgentMachine.create_with_token(hostname='SYNTHETIC-JOBS-E2E',machine_id=str(uuid.uuid4()))
        jobs=[AgentJob.objects.create(endpoint=endpoint,job_type='collect_logs',payload={'max_lines':2},created_by='synthetic-operator') for _ in range(10)]
        environment={**os.environ,'NIGHTOWL_TEST_SERVER':self.live_server_url,'NIGHTOWL_TEST_TOKEN':token,
                     'NIGHTOWL_TEST_MACHINE':endpoint.machine_id,'NIGHTOWL_TEST_JOB_COUNT':str(len(jobs))}
        result=subprocess.run(['dotnet',str(dll),'--jobs-e2e'],env=environment,capture_output=True,text=True,timeout=100)
        if result.returncode:
            for diagnostic in result.stdout.splitlines():
                if diagnostic.startswith('JOB_PROTOCOL_FAILURE='):
                    # This test-defined contract contains only categories/states, never process output.
                    print(diagnostic)
        self.assertEqual(result.returncode,0,'Real .NET job E2E failed; process output withheld for credential safety.')
        line=next(line for line in result.stdout.splitlines() if line.startswith('JOB_PROTOCOL_TIMINGS='))
        evidence=json.loads(line.split('=',1)[1])
        timings={stage:[] for stage in ('signal','received','execution_started','result_ack')}
        for job in jobs:
            job.refresh_from_db()
            self.assertEqual(job.status,'completed')
            self.assertIsNotNone(job.started_at)
            self.assertGreaterEqual(job.started_at,job.dispatched_at)
            for stage in timings:
                timings[stage].append((parse_datetime(evidence['jobs'][str(job.pk)][stage])-job.created_at).total_seconds()*1000)
        self.assertEqual(AgentJobResultReceipt.objects.count(),10)
        operator=get_user_model().objects.create_superuser('synthetic-job-operator',password='synthetic-only')
        self.client.force_login(operator)
        data=self.client.get(reverse('jobs-data')).json()
        self.assertEqual({item['id'] for item in data['jobs']},{str(job.pk) for job in jobs})
        self.assertTrue(all(item['rawStatus']=='completed' and item['history'] for item in data['jobs']))
        measured={stage:{'p50_ms':round(statistics.median(values),2),'p95_ms':round(sorted(values)[math.ceil(len(values)*.95)-1],2)} for stage,values in timings.items()}
        print('MEASURED_LOCAL_HTTP_10_PREQUEUED_JOBS='+json.dumps({'latencies':measured,'signal_requests':evidence['signals'],
              'working_set_bytes':evidence['working_set_bytes'],'harness_cpu_ms':evidence['cpu_ms'],'kind':'isolated_loopback_not_fleet'}))
        if os.environ.get('NIGHTOWL_TEST_BROWSER_E2E')=='1':
            browser=subprocess.run(['node',str(Path(settings.BASE_DIR)/'tests/frontend/test_jobs_protocol_e2e.cjs')],
                env={**os.environ,'NIGHTOWL_E2E_SERVER':self.live_server_url,'NIGHTOWL_E2E_SESSION':self.client.cookies['sessionid'].value},
                capture_output=True,text=True,timeout=40)
            if browser.returncode:
                for diagnostic in browser.stdout.splitlines():
                    if diagnostic.startswith('JOB_BROWSER_FAILURE='): print(diagnostic)
            self.assertEqual(browser.returncode,0,'Jobs browser E2E failed; output withheld for credential safety.')
            print('PASS_REAL_DOTNET_DJANGO_JOBS_BROWSER=YES')
