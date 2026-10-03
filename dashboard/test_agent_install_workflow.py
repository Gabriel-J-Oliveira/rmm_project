from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from agents.models import AgentEnrollmentToken, AgentManualValidationToken


class AgentInstallTokenTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user('token-ui-test', password='synthetic-password', is_staff=True)
        self.client.force_login(user)
        self.url = reverse('agent-install')

    def post_enrollment(self, expires_hours=None, *, ajax=True):
        data = {'action': 'enrollment', 'name': 'Synthetic pilot', 'allowed_domain': 'example.test'}
        if expires_hours is not None:
            data['expires_hours'] = expires_hours
        headers = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'} if ajax else {}
        return self.client.post(self.url, data, **headers)

    def test_page_defaults_and_help_layout(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="expires_hours" type="number" min="1" max="72" value="2"')
        self.assertContains(response, 'id="agent-help-popover"')
        self.assertContains(response, 'id="agent-token-page-size"')
        self.assertContains(response, 'id="enrollment-token-result"')
        self.assertContains(response, 'id="manual-token-result"')
        self.assertContains(response, 'id="ad-scan-button" class="agent-secondary-button"')
        self.assertNotContains(response, 'class="panel how-to-panel"')

    def test_default_one_and_72_hours_are_accepted(self):
        for hours, expected in [(None, 2), ('1', 1), ('72', 72)]:
            before = timezone.now()
            response = self.post_enrollment(hours)
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertTrue(payload['ok'])
            self.assertEqual(payload['kind'], 'enrollment')
            self.assertTrue(payload['token'])
            self.assertEqual(response['Cache-Control'], 'no-store, no-cache, must-revalidate')
            token = AgentEnrollmentToken.objects.get(pk=payload['id'])
            self.assertGreaterEqual(token.expires_at, before + timedelta(hours=expected))
            self.assertLess(token.expires_at, timezone.now() + timedelta(hours=expected, seconds=5))
            self.assertNotEqual(token.token_hash, payload['token'])

    def test_out_of_range_and_invalid_expiry_rejected_server_side(self):
        for hours in ('0', '-1', '73', '168', 'not-a-number'):
            response = self.post_enrollment(hours)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()['error'], 'A validade deve ser entre 1 e 72 horas.')
            self.assertEqual(AgentEnrollmentToken.objects.count(), 0)

    def test_non_ajax_fallback_keeps_one_time_token(self):
        response = self.post_enrollment('2', ajax=False)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="created-enrollment-token"')
        self.assertContains(response, 'Gerar novo comando')
        self.assertContains(response, 'Revogar token')
        self.assertEqual(AgentEnrollmentToken.objects.count(), 1)
        self.assertNotIn(f'data-copy-value="{response.context["created_token"]}"', response.content.decode())
        self.assertEqual(response['Cache-Control'], 'no-store, no-cache, must-revalidate')

    def test_manual_token_ajax_uses_own_result_contract(self):
        response = self.client.post(self.url, {
            'action': 'manual_validation', 'name': 'Synthetic external', 'expires_minutes': '30',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['kind'], 'manual_validation')
        self.assertTrue(response.json()['token'])
        self.assertEqual(AgentManualValidationToken.objects.count(), 1)
        self.assertEqual(AgentEnrollmentToken.objects.count(), 0)

    def test_ajax_error_is_contextual_and_does_not_create_token(self):
        response = self.client.post(self.url, {
            'action': 'enrollment', 'name': '', 'expires_hours': '2',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error'], 'Informe um nome para o token.')
        self.assertEqual(AgentEnrollmentToken.objects.count(), 0)

    def test_ajax_creation_keeps_csrf_protection(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(get_user_model().objects.get(username='token-ui-test'))
        response = client.post(self.url, {
            'action': 'enrollment', 'name': 'Synthetic pilot', 'expires_hours': '2',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(AgentEnrollmentToken.objects.count(), 0)
