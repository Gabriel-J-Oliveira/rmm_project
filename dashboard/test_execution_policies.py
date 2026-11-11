from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class ExecutionPoliciesNavigationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('execution-policies-ui', is_staff=True)
        self.client.force_login(self.user)

    def test_execution_policies_have_distinct_navigation_without_duplicating_software_policies(self):
        response = self.client.get(reverse('jobs-list'), {'tab': 'automations'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['active_nav'], 'execution_policies')
        self.assertContains(response, 'Políticas de Execução')
        self.assertContains(response, 'href="/jobs/?tab=automations"')
        self.assertContains(response, 'data-task-tab-panel="automations"')
        self.assertContains(response, 'data-task-tab="jobs"')
        self.assertContains(response, 'href="/software-policies/"')
        self.assertNotContains(response, 'Políticas de Software')

    def test_jobs_default_navigation_remains_separate(self):
        response = self.client.get(reverse('jobs-list'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['active_nav'], 'jobs')
        self.assertContains(response, '<strong>Tarefas</strong>', html=True)
