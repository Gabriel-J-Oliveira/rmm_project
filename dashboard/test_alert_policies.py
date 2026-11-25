from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse

from agents.models import AgentMachine, AlertPolicy, AuditEvent, EndpointAlert


class AlertPolicyTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('noc-operator', is_staff=True)
        self.client.force_login(self.user)
        self.policies = reverse('alert-policies')
        self.edit = reverse('alert-policy-edit', args=['ram-warning'])
        self.values = {
            'name': 'RAM de desktops', 'enabled': 'on', 'metric': 'ram', 'severity': 'warning',
            'threshold': '90', 'duration_minutes': '15', 'recovery_threshold': '80',
            'max_age_minutes': '5', 'min_coverage_percent': '80', 'scope': 'desktops',
            'notify_visual': 'on', 'revision': '0',
        }

    def grant(self, permission):
        self.user.user_permissions.add(Permission.objects.get(
            content_type__app_label='agents', codename=permission,
        ))

    def test_defaults_and_read_only_permissions(self):
        self.assertEqual(AlertPolicy.objects.count(), 14)
        presets = {
            'cpu': [(85, 15), (95, 30)], 'ram': [(85, 15), (95, 30)], 'disk': [(85, 0), (95, 0)],
            'offline': [(24, 0), (72, 0)], 'uptime': [(15, 0), (30, 0)],
        }
        for metric, limits in presets.items():
            self.assertEqual(list(AlertPolicy.objects.filter(metric=metric).order_by('threshold')
                                  .values_list('threshold', 'duration_minutes')), limits)
        self.assertFalse(AlertPolicy.objects.filter(enabled=True).exists())
        self.assertEqual(self.client.get(self.policies).status_code, 403)
        self.grant('view_alertpolicy')
        self.assertContains(self.client.get(self.policies), 'Políticas de Alertas')
        self.assertNotContains(self.client.get(self.policies), self.edit)
        self.assertEqual(self.client.post(self.edit, self.values).status_code, 403)

    def test_edit_with_endpoint_targets_is_audited_and_rejects_stale_revision(self):
        self.grant('change_alertpolicy')
        self.assertContains(self.client.get(self.edit), 'data-policy-summary')
        endpoint = AgentMachine.objects.create(hostname='pilot', agent_token_hash='a' * 64)
        data = {**self.values, 'scope': 'endpoints', 'endpoints': [str(endpoint.pk)], 'notify_sound': 'on'}
        self.assertRedirects(self.client.post(self.edit, data), self.policies)
        policy = AlertPolicy.objects.get(pk='ram-warning')
        self.assertEqual(policy.threshold, 90)
        self.assertEqual(policy.revision, 1)
        self.assertTrue(policy.enabled)
        self.assertTrue(policy.notify_sound)
        self.assertEqual(list(policy.endpoints.all()), [endpoint])
        audit = AuditEvent.objects.get(event_type='alert_policy.updated')
        self.assertEqual(audit.actor_name, self.user.username)
        self.assertEqual(audit.metadata['before']['threshold'], 85)
        self.assertEqual(audit.metadata['after']['endpoints'], [str(endpoint.pk)])
        self.assertContains(self.client.post(self.edit, {**data, 'threshold': 94}),
                            'Reabra a edição', status_code=422)
        policy.refresh_from_db()
        self.assertEqual(policy.threshold, 90)
        self.assertEqual(AuditEvent.objects.filter(event_type='alert_policy.updated').count(), 1)
        self.assertFalse(EndpointAlert.objects.exists())

    def test_invalid_inputs_do_not_change_policy(self):
        self.grant('change_alertpolicy')
        for overrides in (
            {'threshold': 101}, {'recovery_threshold': 90}, {'scope': 'endpoints'},
            {'scope': 'endpoints', 'endpoints': ['invalid']}, {'min_coverage_percent': 0},
            {'metric': 'antivirus'}, {'metric': 'job_failures'}, {'revision': ''},
        ):
            with self.subTest(overrides=overrides):
                response = self.client.post(self.edit, {**self.values, **overrides})
                self.assertEqual(response.status_code, 422)
        self.assertEqual(AlertPolicy.objects.get(pk='ram-warning').revision, 0)
        self.assertFalse(AuditEvent.objects.filter(event_type='alert_policy.updated').exists())

    def test_metric_change_clears_irrelevant_parameters(self):
        self.grant('change_alertpolicy')
        data = {**self.values, 'metric': 'antivirus', 'antivirus_condition': 'either'}
        self.assertRedirects(self.client.post(self.edit, data), self.policies)
        policy = AlertPolicy.objects.get(pk='ram-warning')
        self.assertEqual(policy.antivirus_condition, 'either')
        self.assertIsNone(policy.threshold)
        self.assertIsNone(policy.recovery_threshold)
        self.assertIsNone(policy.min_coverage_percent)

    def test_mutations_require_csrf(self):
        self.grant('change_alertpolicy')
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(self.edit, self.values).status_code, 403)
