import uuid
from datetime import datetime, timedelta, timezone as datetime_timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import AgentJob, AgentMachine, EndpointPerformanceSample, hash_agent_token
from .telemetry_analytics import build_endpoint_telemetry_summary


class EndpointTelemetrySummaryApiTests(TestCase):
    def setUp(self):
        self.endpoint = self.make_endpoint('SUMMARY-A')
        self.other = self.make_endpoint('SUMMARY-B')
        self.url = reverse('endpoint-telemetry-summary', args=[self.endpoint.pk])
        self.start = datetime(2026, 9, 1, tzinfo=datetime_timezone.utc)
        self.end = self.start + timedelta(hours=1)

    def make_endpoint(self, hostname):
        return AgentMachine.objects.create(
            hostname=hostname,
            machine_id=str(uuid.uuid4()),
            agent_token_hash=hash_agent_token(f'synthetic-{hostname}-credential'),
        )

    def login_with_permissions(self, *codenames):
        user = get_user_model().objects.create_user(
            username='summary-viewer', password='synthetic-password',
        )
        user.user_permissions.add(*Permission.objects.filter(
            content_type__app_label='agents', codename__in=codenames,
        ))
        self.client.force_login(user)

    def get_summary(self, start=None, end=None, url=None, **extra):
        params = {
            'from': (start or self.start).isoformat(),
            'to': (end or self.end).isoformat(),
            **extra,
        }
        return self.client.get(url or self.url, params)

    def sample(self, at, endpoint=None, **values):
        data = {
            'endpoint': endpoint or self.endpoint,
            'sample_id': uuid.uuid4(),
            'collected_at': at,
            'uptime_seconds': 1200,
            'collection_duration_ms': 25,
            'telemetry_errors_count': 0,
        }
        data.update(values)
        return EndpointPerformanceSample.objects.create(**data)

    def test_unauthenticated_is_denied(self):
        self.assertIn(self.get_summary().status_code, (401, 403))

    def test_permission_checks_precede_endpoint_lookup(self):
        missing_url = reverse('endpoint-telemetry-summary', args=[uuid.uuid4()])
        for permissions in ((), ('view_agentmachine',),
                            ('view_endpointperformancesample',)):
            with self.subTest(permissions=permissions):
                self.client.logout()
                get_user_model().objects.filter(username='summary-viewer').delete()
                self.login_with_permissions(*permissions)
                for url in (self.url, missing_url):
                    response = self.get_summary(url=url)
                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(response.json(), {'error': 'forbidden'})

    def test_authorized_missing_endpoint_returns_404(self):
        self.login_with_permissions('view_agentmachine', 'view_endpointperformancesample')
        url = reverse('endpoint-telemetry-summary', args=[uuid.uuid4()])
        response = self.get_summary(url=url)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {'error': 'not_found'})

    def test_period_parameters_are_required_and_timezone_aware(self):
        self.login_with_permissions('view_agentmachine', 'view_endpointperformancesample')
        valid = {'from': self.start.isoformat(), 'to': self.end.isoformat()}
        for params in ({}, {'from': valid['from']}, {'to': valid['to']}):
            with self.subTest(params=params):
                response = self.client.get(self.url, params)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json(), {'error': 'missing_period'})
        cases = (
            {'from': 'not-a-date'}, {'to': 'not-a-date'},
            {'from': self.start.replace(tzinfo=None).isoformat()},
            {'to': self.end.replace(tzinfo=None).isoformat()},
            {'to': valid['from']},
            {'from': valid['to']},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                response = self.client.get(self.url, valid | overrides)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json(), {'error': 'invalid_period'})

    def test_exactly_seven_days_allowed_but_one_second_more_rejected(self):
        self.login_with_permissions('view_agentmachine', 'view_endpointperformancesample')
        self.assertEqual(self.get_summary(end=self.start + timedelta(days=7)).status_code, 200)
        response = self.get_summary(end=self.start + timedelta(days=7, seconds=1))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'error': 'period_too_large'})

    def test_response_matches_service_and_is_read_only(self):
        self.login_with_permissions('view_agentmachine', 'view_endpointperformancesample')
        self.sample(self.start, cpu_percent=10, memory_used_percent=40,
                    memory_committed_percent=50, network_received_bytes=20,
                    network_sent_bytes=0, agent_working_set_bytes=100)
        self.sample(self.start + timedelta(minutes=5), cpu_percent=20,
                    memory_used_percent=None, network_received_bytes=None)
        before_samples = EndpointPerformanceSample.objects.count()
        before_jobs = AgentJob.objects.count()
        response = self.get_summary(limit=1)
        self.assertEqual(response.status_code, 200)
        expected = build_endpoint_telemetry_summary(self.endpoint, self.start, self.end)
        self.assertEqual(response.json(), expected)
        self.assertEqual(response.json()['endpoint_id'], str(self.endpoint.pk))
        self.assertEqual(response.json()['window']['end'], self.end.isoformat())
        self.assertEqual(response.json()['samples']['received'], 2)
        for key in ('cpu', 'memory', 'network', 'agent', 'quality'):
            self.assertIn(key, response.json())
        self.assertEqual(EndpointPerformanceSample.objects.count(), before_samples)
        self.assertEqual(AgentJob.objects.count(), before_jobs)

    def test_summary_uses_half_open_window_and_isolates_endpoint(self):
        self.login_with_permissions('view_agentmachine', 'view_endpointperformancesample')
        for at in (self.start - timedelta(seconds=1), self.start,
                   self.start + timedelta(minutes=30), self.end,
                   self.end + timedelta(seconds=1)):
            self.sample(at, cpu_percent=10)
        self.sample(self.start, endpoint=self.other, cpu_percent=90)
        response = self.get_summary()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['samples']['received'], 2)
        self.assertEqual(response.json()['cpu']['usage_percent']['avg'], 10)

    def test_valid_empty_window_returns_summary(self):
        self.login_with_permissions('view_agentmachine', 'view_endpointperformancesample')
        response = self.get_summary()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['samples']['received'], 0)
        self.assertEqual(response.json()['quality']['expected_samples'], 12)
        self.assertEqual(response.json()['quality']['coverage_percent'], 0.0)

    def test_partially_future_window_uses_effective_end(self):
        self.login_with_permissions('view_agentmachine', 'view_endpointperformancesample')
        now = timezone.now()
        with patch('agents.telemetry_analytics.timezone.now', return_value=now):
            response = self.get_summary(
                start=now - timedelta(minutes=30), end=now + timedelta(minutes=30),
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['quality']['expected_samples'], 6)

    def test_raw_endpoint_retains_limit_and_response_shape(self):
        self.login_with_permissions('view_agentmachine', 'view_endpointperformancesample')
        row = self.sample(self.start, cpu_percent=10)
        url = reverse('endpoint-telemetry', args=[self.endpoint.pk])
        response = self.client.get(url, {'limit': 1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['endpoint_id'], str(self.endpoint.pk))
        self.assertEqual(response.json()['samples'][0]['sample_id'], str(row.sample_id))
        self.assertEqual(self.client.get(url, {'limit': 501}).json(), {'error': 'invalid_limit'})
