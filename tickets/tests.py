from django.test import TestCase
from django.urls import reverse
from django.utils import timezone


class TicketCentralTests(TestCase):
    host = '127.0.0.1'

    def test_central_page_renders_unified_workspace(self):
        response = self.client.get(reverse('tickets:central'), HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Central de Atendimento')
        self.assertContains(response, 'desk-ticket-table')
        self.assertContains(response, 'desk-detail-panel')
        self.assertContains(response, 'desk-filter-chipbar')

    def test_ticket_index_renders_central(self):
        response = self.client.get(reverse('tickets:index'), HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Central de Atendimento')
        self.assertNotContains(response, 'Night Owl Desk / Fila')

    def test_legacy_queue_and_panel_routes_are_removed(self):
        queue_response = self.client.get('/tickets/queue/', HTTP_HOST=self.host)
        panel_response = self.client.get('/tickets/painel/', HTTP_HOST=self.host)

        self.assertEqual(queue_response.status_code, 404)
        self.assertEqual(panel_response.status_code, 404)

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


class TicketCreateLayoutTests(TestCase):
    host = '127.0.0.1'

    def test_create_page_renders_quick_and_complete_modes(self):
        response = self.client.get(reverse('tickets:create'), HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Novo chamado')
        self.assertContains(response, 'Cria&ccedil;&atilde;o completa')
        self.assertContains(response, 'Cria&ccedil;&atilde;o r&aacute;pida')
        self.assertContains(response, 'desk-create-wizard')
        self.assertContains(response, 'desk-create-quick')

    def test_create_page_renders_problem_and_requester_steps(self):
        response = self.client.get(reverse('tickets:create'), HTTP_HOST=self.host)

        self.assertContains(response, 'Passo 1 de 2')
        self.assertContains(response, 'O que aconteceu')
        self.assertContains(response, 'Passo 2 de 2')
        self.assertContains(response, 'Quem esta solicitando')
        self.assertContains(response, 'Buscar usuario existente')

    def test_create_page_explains_vip_priority_change(self):
        response = self.client.get(reverse('tickets:create'), HTTP_HOST=self.host)

        self.assertContains(response, 'Solicitante e socio/VIP')
        self.assertContains(response, 'Prioridade alterada para critica')
        self.assertContains(response, 'data-vip-explanation')

    def test_create_page_renders_rmm_endpoint_and_duplicate_notice(self):
        response = self.client.get(
            reverse('tickets:create'),
            {'category': 'Seguranca', 'endpoint': 'FIN-012'},
            HTTP_HOST=self.host,
        )

        self.assertContains(response, 'Dispositivo / endpoint relacionado')
        self.assertContains(response, 'FIN-012')
        self.assertContains(response, 'Possivel chamado duplicado')
        self.assertContains(response, '#1042')

    def test_create_page_renders_alert_prefill_from_rmm_alert(self):
        from agents.models import AgentMachine, EndpointAlert

        endpoint, _token = AgentMachine.create_with_token(
            hostname='TEST-RMM-001',
            domain='control.local',
            status='online',
            last_seen_at=timezone.now(),
        )
        alert = EndpointAlert.objects.create(
            endpoint=endpoint,
            alert_type='disk_low',
            severity='critical',
            title='Disco C: critico',
            description='Disco C: possui apenas 7% livre.',
            status='open',
        )

        response = self.client.get(reverse('tickets:create'), {'alert': str(alert.id)}, HTTP_HOST=self.host)

        self.assertContains(response, 'Criar chamado a partir deste alerta')
        self.assertContains(response, 'Disco C: critico')
        self.assertContains(response, 'Servidor')
        self.assertContains(response, 'TEST-RMM-001')


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
