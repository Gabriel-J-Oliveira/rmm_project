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


class TicketDetailLayoutTests(TestCase):
    host = '127.0.0.1'

    def test_detail_page_renders_new_header_and_tabs(self):
        response = self.client.get(reverse('tickets:detail', args=[1048]), HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'desk-ticket-detail-header')
        self.assertContains(response, 'desk-editable-title')
        self.assertContains(response, 'Visao geral')
        self.assertContains(response, 'Atividade')
        self.assertContains(response, 'Anexos')
        self.assertContains(response, 'Dispositivo')
        self.assertContains(response, 'Relacionados')

    def test_detail_page_uses_compact_inline_actions(self):
        response = self.client.get(reverse('tickets:detail', args=[1048]), HTTP_HOST=self.host)

        self.assertContains(response, 'desk-inline-popover')
        self.assertContains(response, 'Resolver')
        self.assertNotContains(response, 'Acoes visuais')

    def test_detail_page_renders_rmm_device_tab_from_isolated_context(self):
        response = self.client.get(reverse('tickets:detail', args=[1042]), HTTP_HOST=self.host)

        self.assertContains(response, 'Bitdefender ausente em FIN-012')
        self.assertContains(response, 'FIN-012')
        self.assertContains(response, 'Acoes remotas')
        self.assertContains(response, 'Dados vindos do RMM interno')
        self.assertContains(response, 'desk-device-metrics')

    def test_detail_page_renders_resolution_and_audit_drawers(self):
        response = self.client.get(reverse('tickets:detail', args=[1048]), HTTP_HOST=self.host)

        self.assertContains(response, 'desk-resolution-drawer')
        self.assertContains(response, 'Tipo de resolucao')
        self.assertContains(response, 'desk-audit-drawer')
        self.assertContains(response, 'Auditoria completa')


class TicketDashboardLayoutTests(TestCase):
    host = '127.0.0.1'

    def test_dashboard_renders_modes_and_global_controls(self):
        response = self.client.get(reverse('tickets:dashboard'), HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Operacional')
        self.assertContains(response, 'Gerencial')
        self.assertContains(response, 'Infraestrutura')
        self.assertContains(response, 'Comparar periodo anterior')
        self.assertContains(response, 'desk-dashboard-kpi-grid')

    def test_dashboard_kpis_link_to_central_filters(self):
        response = self.client.get(reverse('tickets:dashboard'), HTTP_HOST=self.host)

        self.assertContains(response, '/tickets/central/?status=resolved')
        self.assertContains(response, '/tickets/central/?priority=critical')
        self.assertContains(response, 'desk-trend-badge')
        self.assertContains(response, 'desk-sparkline')

    def test_operational_dashboard_renders_heatmap_and_ranking(self):
        response = self.client.get(reverse('tickets:dashboard'), {'mode': 'operational'}, HTTP_HOST=self.host)

        self.assertContains(response, 'Heatmap de volume')
        self.assertContains(response, 'desk-heatmap')
        self.assertContains(response, 'Ranking de tecnicos')
        self.assertContains(response, 'desk-ranking-table')
        self.assertContains(response, 'Volume 42% acima da media')

    def test_management_dashboard_renders_fleet_health_and_annotations(self):
        response = self.client.get(reverse('tickets:dashboard'), {'mode': 'management'}, HTTP_HOST=self.host)

        self.assertContains(response, 'Saude da frota RMM')
        self.assertContains(response, 'SLA cumprido vs meta')
        self.assertContains(response, 'Anotacoes da timeline')

    def test_infrastructure_dashboard_uses_rmm_fleet_context(self):
        response = self.client.get(reverse('tickets:dashboard'), {'mode': 'infrastructure'}, HTTP_HOST=self.host)

        self.assertContains(response, 'Saude da frota RMM')
        self.assertContains(response, 'Total monitorado')
        self.assertContains(response, 'Alertas criticos')
        self.assertContains(response, 'desk-fleet-health')

    def test_dashboard_renders_wallboard_and_report_controls(self):
        response = self.client.get(reverse('tickets:dashboard'), HTTP_HOST=self.host)

        self.assertContains(response, 'Ativar wallboard')
        self.assertContains(response, 'Modo wallboard / TV')
        self.assertContains(response, 'Exportar dashboard')
        self.assertContains(response, 'Agendar relatorio')
