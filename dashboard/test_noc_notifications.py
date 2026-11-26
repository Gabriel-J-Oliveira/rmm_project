import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from agents.models import AgentMachine, AlertEvent, AlertPolicy, EndpointAlert
from dashboard.models import NocNotificationPreference, NocNotificationRead


class NocNotificationTests(TestCase):
    def setUp(self):
        self.operator = get_user_model().objects.create_superuser('notification-operator')
        self.client.force_login(self.operator)
        self.endpoint = AgentMachine.objects.create(
            hostname='NOC-NOTIFICATION-01', machine_id=str(uuid.uuid4()),
            agent_token_hash='a' * 64, status=AgentMachine.STATUS_ONLINE,
            last_seen_at=timezone.now(),
        )
        self.policy = AlertPolicy.objects.create(
            key='notification-test', name='Notification test', enabled=True,
            metric=AlertPolicy.Metric.CPU, severity=AlertPolicy.Severity.CRITICAL,
            threshold=95, duration_minutes=5, recovery_threshold=90,
            notify_visual=True, notify_sound=True,
        )
        self.policy.endpoints.add(self.endpoint)
        self.alert, self.event = self.create_event()

    def create_event(self, *, created_at=None, metadata=None, status=EndpointAlert.STATUS_OPEN):
        now = timezone.now()
        alert = EndpointAlert.objects.create(
            endpoint=self.endpoint, alert_type='cpu_pressure', severity=EndpointAlert.SEVERITY_CRITICAL,
            title='CPU sustentada', description='CPU alta por período confirmado.', status=status,
            first_seen_at=created_at or now, last_seen_at=created_at or now, source='alert_policy',
            metadata=metadata or {
                'policy': self.policy.key, 'notify_visual': True, 'notify_sound': True,
            },
        )
        event = AlertEvent.objects.create(
            alert=alert, event_type=AlertEvent.TYPE_CREATED, message='Ocorrência aberta.',
            created_at=created_at or now,
        )
        return alert, event

    def get_feed(self, **params):
        return self.client.get(reverse('noc-notifications'), params)

    def test_initial_feed_lists_ten_and_does_not_emit_historical_toasts(self):
        for index in range(11):
            self.create_event()
        response = self.get_feed()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['entries']), 10)
        self.assertEqual(response.json()['unread'], 12)
        self.assertEqual(response.json()['sound'], NocNotificationPreference.Sound.OFF)
        self.assertEqual(response.json()['notices'], [])
        self.assertTrue(all(entry['url'].startswith('/endpoints/') for entry in response.json()['entries']))

    def test_incremental_feed_respects_enabled_policy_flags_mute_and_resolution(self):
        self.alert.status = EndpointAlert.STATUS_RESOLVED
        self.alert.save(update_fields=['status'])
        self.policy.notify_visual = False
        self.policy.notify_sound = False
        self.policy.save(update_fields=['notify_visual', 'notify_sound'])
        since = timezone.now() - timedelta(seconds=5)
        fresh_alert, fresh_event = self.create_event()
        response = self.get_feed(since=since.isoformat())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['entries'], [])
        self.assertEqual(response.json()['unread'], 0)
        self.assertEqual(response.json()['notices'], [])

        self.policy.notify_visual = True
        self.policy.notify_sound = True
        self.policy.save(update_fields=['notify_visual', 'notify_sound'])
        self.client.post(reverse('noc-notifications'), {'action': 'sound', 'sound': 'all'})
        self.assertEqual(len(self.get_feed(since=since.isoformat()).json()['notices']), 1)
        fresh_alert.muted_until = timezone.now() + timedelta(minutes=5)
        fresh_alert.save(update_fields=['muted_until'])
        self.assertEqual(self.get_feed(since=since.isoformat()).json()['notices'], [])
        fresh_alert.muted_until = None
        fresh_alert.status = EndpointAlert.STATUS_RESOLVED
        fresh_alert.save(update_fields=['muted_until', 'status'])
        self.assertEqual(self.get_feed(since=since.isoformat()).json()['notices'], [])
        self.assertTrue(AlertEvent.objects.filter(pk=fresh_event.pk).exists())

    def test_sound_preference_and_individual_read_are_persisted_per_operator(self):
        response = self.client.post(reverse('noc-notifications'), {
            'action': 'sound', 'sound': NocNotificationPreference.Sound.CRITICAL,
        })
        self.assertEqual(response.status_code, 200)
        response = self.client.post(reverse('noc-notifications'), {
            'action': 'read', 'event': str(self.event.pk),
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(NocNotificationRead.objects.filter(operator=self.operator, event=self.event).exists())
        feed = self.get_feed().json()
        self.assertEqual(feed['sound'], NocNotificationPreference.Sound.CRITICAL)
        self.assertEqual(feed['unread'], 0)
        self.assertTrue(next(entry for entry in feed['entries'] if entry['id'] == str(self.event.pk))['read'])

        other = get_user_model().objects.create_user('notification-other', is_staff=True)
        other.user_permissions.add(*Permission.objects.filter(codename__in=('view_agentmachine', 'view_endpointalert')))
        self.client.force_login(other)
        other_feed = self.get_feed().json()
        self.assertEqual(other_feed['sound'], NocNotificationPreference.Sound.OFF)
        self.assertEqual(other_feed['unread'], 1)

    def test_mark_all_uses_persistent_operator_watermark(self):
        response = self.client.post(reverse('noc-notifications'), {'action': 'read_all'})
        self.assertEqual(response.status_code, 200)
        preference = NocNotificationPreference.objects.get(operator=self.operator)
        self.assertIsNotNone(preference.read_all_at)
        self.assertEqual(self.get_feed().json()['unread'], 0)

    def test_read_requires_both_fleet_permissions_and_post_requires_csrf(self):
        viewer = get_user_model().objects.create_user('notification-limited', is_staff=True)
        viewer.user_permissions.add(Permission.objects.get(codename='view_agentmachine'))
        self.client.force_login(viewer)
        self.assertEqual(self.get_feed().status_code, 403)

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.operator)
        page = csrf_client.get(reverse('noc'))
        token = page.cookies['csrftoken'].value
        response = csrf_client.post(reverse('noc-notifications'), {'action': 'read_all'}, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)
        rejected = Client(enforce_csrf_checks=True)
        rejected.force_login(self.operator)
        self.assertEqual(rejected.post(reverse('noc-notifications'), {'action': 'read_all'}).status_code, 403)

    def test_invalid_event_and_sound_actions_do_not_persist(self):
        self.assertEqual(self.client.post(reverse('noc-notifications'), {'action': 'read', 'event': str(uuid.uuid4())}).status_code, 404)
        self.assertEqual(self.client.post(reverse('noc-notifications'), {'action': 'sound', 'sound': 'always'}).status_code, 400)
        self.assertFalse(NocNotificationPreference.objects.exists())
