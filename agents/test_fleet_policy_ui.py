import json
import os
import subprocess
from pathlib import Path
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from . import test_rollout_preview


@skipUnless(os.environ.get('NIGHTOWL_UI_TESTS') == '1', 'Opt-in Playwright synthetic UI tests')
class FleetPolicyBrowserTests(TestCase):
    setUp = test_rollout_preview.AgentRolloutPreviewTests.setUp

    def test_synthetic_administrative_ui(self):
        user = get_user_model().objects.create_user(username='synthetic-ui-admin', is_staff=True, is_superuser=True)
        self.client.force_login(user)
        fixture = {'releases': self.client.get(reverse('agent-releases')).content.decode(),
                   'endpoints': self.client.get(reverse('endpoint-list')).content.decode(),
                   'endpointId': str(self.machine.pk)}
        script = Path(__file__).resolve().parent.parent / 'scripts/Test-FleetPolicyUi.cjs'
        result = subprocess.run(['node', str(script)], input=json.dumps(fixture), text=True, encoding='utf-8', capture_output=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Fleet UI PASS', result.stdout)
