from django.test import TestCase
from django.urls import reverse


class TicketCentralTests(TestCase):
    host = '127.0.0.1'

    def test_central_page_renders_unified_workspace(self):
        response = self.client.get(reverse('tickets:central'), HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Central de Atendimento')
        self.assertContains(response, 'desk-ticket-table')
        self.assertContains(response, 'desk-detail-panel')
        self.assertContains(response, 'desk-filter-chipbar')

    def test_legacy_queue_route_renders_central(self):
        response = self.client.get(reverse('tickets:list'), HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Central de Atendimento')
        self.assertNotContains(response, 'Night Owl Desk / Fila')

    def test_service_panel_route_renders_central_kanban_mode(self):
        response = self.client.get(reverse('tickets:service-panel'), HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Central de Atendimento')
        self.assertContains(response, 'Kanban por status')

    def test_selected_ticket_opens_side_panel_without_detail_navigation(self):
        response = self.client.get(reverse('tickets:central'), {'ticket': 1048}, HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '#1048')
        self.assertContains(response, 'Socio sem acesso ao e-mail')
        self.assertContains(response, 'desk-comment-dock')

    def test_desk_nav_uses_central_as_main_entry(self):
        response = self.client.get(reverse('tickets:central'), HTTP_HOST=self.host)

        self.assertContains(response, 'Central de Atendimento')
        self.assertNotContains(response, '>Fila</a>')
        self.assertNotContains(response, '>Painel de Chamados</a>')
