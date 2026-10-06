from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from dashboard.models import RemoteInstallBatch, RemoteInstallJob


class BatchPageTests(TestCase):
    def test_jobs_and_installation_render_batch_ui_without_dispatch(self):
        user = get_user_model().objects.create_user('batch-ui', is_staff=True)
        self.client.force_login(user)
        before = RemoteInstallJob.objects.count()
        jobs = self.client.get(reverse('jobs-list'), {'tab': 'installations'})
        self.assertEqual(jobs.status_code, 200)
        for marker in ('data-task-tab="installations"', 'data-task-tab-panel="installations"',
                       'js/install_batches.js', 'js/install_batch_client.js',
                       'id="install-batch-drawer"', 'id="install-credential-host"'):
            self.assertContains(jobs, marker)
        for name in ('agenda', 'list', 'jobs', 'templates'):
            self.assertContains(jobs, f'data-task-tab="{name}"')
        installation = self.client.get(reverse('agent-install'))
        self.assertContains(installation, 'js/install_batch_client.js')
        self.assertNotContains(installation, 'data-preflight-url=')
        self.assertNotContains(installation, 'data-install-url=')
        self.assertEqual(RemoteInstallBatch.objects.count(), 0)
        self.assertEqual(RemoteInstallJob.objects.count(), before)
