from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class EndpointListPaginationTemplateTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            username='endpoint-list-pagination-test', password='synthetic-password', is_staff=True,
        )
        self.client.force_login(user)

    def test_endpoint_table_renders_pagination_without_changing_filters(self):
        response = self.client.get(reverse('endpoint-list'), {
            'mock': '1', 'quick': 'offline', 'q': 'FIN', 'status': 'offline',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['filters']['quick'], 'offline')
        self.assertEqual(response.context['filters']['q'], 'FIN')
        self.assertEqual(response.context['filters']['status'], 'offline')
        self.assertContains(response, 'data-endpoint-pagination')
        self.assertContains(response, 'data-endpoint-page-size')
        self.assertContains(response, '<option value="50">50</option>', html=True)
        self.assertContains(response, 'Selecionar todos nesta página')
        self.assertContains(response, '<option value="10" selected>10</option>', html=True)
        self.assertContains(response, '<option value="25">25</option>', html=True)
