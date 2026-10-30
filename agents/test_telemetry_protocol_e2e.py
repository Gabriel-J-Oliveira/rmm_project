"""Actual loopback HTTP to the .NET test executable. Synthetic identities only."""
import os
import subprocess
import tempfile
import uuid
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import LiveServerTestCase
from django.urls import reverse

from .models import AgentMachine, EndpointPerformanceSample


class TelemetryProtocolE2ETests(LiveServerTestCase):
    def test_dotnet_windows_and_live_points_via_real_http(self):
        dll = Path(settings.BASE_DIR) / 'NightOwl.Agent.Windows.Tests/bin/Release/net8.0-windows/NightOwl.Agent.Windows.Tests.dll'
        if not dll.exists():
            self.fail('Build the Windows .NET test executable before running this E2E; gate is not silently skipped.')
        with tempfile.TemporaryDirectory(prefix='nightowl-e2e-cache-') as directory, self.settings(CACHES={
                'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'},
                'telemetry_live': {'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache', 'LOCATION':directory}}):
            endpoint, token = AgentMachine.create_with_token(hostname='SYNTHETIC-E2E', machine_id=str(uuid.uuid4()))
            operator = get_user_model().objects.create_superuser('synthetic-e2e', password='synthetic-only')
            self.client.force_login(operator)
            url = reverse('endpoint-telemetry-live', args=[endpoint.pk])
            started = self.client.post(url, {}, content_type='application/json')
            self.assertEqual(started.status_code, 200, started.content)
            environment = {**os.environ, 'NIGHTOWL_TEST_MACHINE':endpoint.machine_id,
                'NIGHTOWL_TEST_TOKEN':token, 'NIGHTOWL_TEST_SERVER':self.live_server_url}
            result = subprocess.run(['dotnet', str(dll), '--continuous-e2e'], env=environment, capture_output=True, text=True, timeout=30)
            # Never emit arbitrary subprocess output containing a credential on failure.
            self.assertEqual(result.returncode, 0, 'Isolated .NET protocol failed (output withheld for credential safety).')
            data = self.client.get(url).json()
            self.assertEqual(len(data['points']), 2)
            self.assertFalse(data['stale'])
            self.assertEqual(EndpointPerformanceSample.objects.filter(schema_version=2).count(), 2)
            print('PASS_REAL_LOOPBACK_DOTNET_DJANGO_SUMMARY_AND_LIVE=YES')
            if os.environ.get('NIGHTOWL_TEST_BROWSER_E2E') == '1':
                from django.middleware.csrf import get_token
                from django.test import RequestFactory
                request = RequestFactory().get('/')
                get_token(request)
                browser_environment = {**os.environ, 'NIGHTOWL_E2E_SERVER':self.live_server_url,
                    'NIGHTOWL_E2E_ENDPOINT':str(endpoint.pk), 'NIGHTOWL_E2E_SESSION':self.client.cookies['sessionid'].value,
                    'NIGHTOWL_E2E_CSRF':request.META['CSRF_COOKIE']}
                browser = subprocess.run(['node', str(Path(settings.BASE_DIR)/'tests/frontend/test_live_protocol_e2e.cjs')],
                    env=browser_environment, capture_output=True, text=True, timeout=40)
                self.assertEqual(browser.returncode, 0, 'Real loopback browser gate failed; potentially sensitive subprocess output withheld.')
                print('PASS_REAL_DOTNET_DJANGO_BROWSER_LIVE_WITH_CSRF=YES')
