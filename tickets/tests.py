from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone


class DeskTechnicalTestCase(TestCase):
    host = '127.0.0.1'

    def setUp(self):
        super().setUp()
        self.tech = get_user_model().objects.create_user('gabriel', password='x', is_staff=True)
        self.client.force_login(self.tech)


class TicketCentralTests(DeskTechnicalTestCase):

    def test_central_page_renders_unified_workspace(self):
        response = self.client.get(reverse('tickets:central'), HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Central de Atendimento')
        self.assertContains(response, 'desk-ticket-table')
        self.assertContains(response, 'desk-detail-panel')
        self.assertContains(response, 'nw-filterbar')
        self.assertContains(response, 'desk-command-palette')
        self.assertContains(response, 'Atribuídos a mim')
        self.assertContains(response, 'Responsavel')
        self.assertContains(response, 'RMM Alertas')
        self.assertContains(response, 'desk-ticket-grid-header')
        self.assertContains(response, 'Selecione um chamado para ver mais detalhes')

    def test_ticket_index_renders_central(self):
        response = self.client.get(reverse('tickets:index'), HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Central de Atendimento')

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

    def test_central_bulk_actions_are_hidden_until_selection(self):
        response = self.client.get(reverse('tickets:central'), HTTP_HOST=self.host)

        self.assertContains(response, 'desk-selection-bar')
        self.assertContains(response, 'hidden')
        self.assertContains(response, 'data-selected-label')

    def test_central_rows_are_prepared_for_client_side_preview(self):
        from tickets.models import Ticket, TicketCategory

        category, _ = TicketCategory.objects.get_or_create(
            name='Acesso',
            defaults={'description': 'Acesso'},
        )

        Ticket.objects.create(
            title='Chamado preparado para preview',
            description='Validar atributos da linha da central.',
            requester_name='Mariana Souza',
            requester_department='Financeiro',
            category=category,
            queue='N1 - Atendimento',
        )

        response = self.client.get(reverse('tickets:central'), HTTP_HOST=self.host)

        self.assertContains(response, 'central-ticket-data')
        self.assertContains(response, 'data-preview-panel')
        self.assertContains(response, 'data-preview-trigger')
        self.assertContains(response, 'data-detail-url=')

    def test_central_renders_rmm_context_and_shortcuts(self):
        response = self.client.get(reverse('tickets:central'), {'origin': 'rmm'}, HTTP_HOST=self.host)

        self.assertContains(response, 'RMM Alert')
        self.assertContains(response, 'desk-rmm-mini-card')
        self.assertContains(response, 'Acesso remoto')
        self.assertContains(response, 'Atalhos da Central')

    def test_desk_nav_uses_central_as_main_entry(self):
        response = self.client.get(reverse('tickets:central'), HTTP_HOST=self.host)

        self.assertContains(response, 'Central de Atendimento')
        self.assertNotContains(response, '>Fila</a>')


class TicketUserDirectoryAccessTests(TestCase):
    host = '127.0.0.1'

    def test_technical_user_can_access_user_directory(self):
        user = get_user_model().objects.create_user('tech', password='x', is_staff=True)
        self.client.force_login(user)

        response = self.client.get(reverse('tickets:users'), HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)

    def test_authenticated_non_technical_user_is_blocked_from_user_directory(self):
        user = get_user_model().objects.create_user('requester', password='x')
        self.client.force_login(user)

        response = self.client.get(reverse('tickets:users'), HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 302)
        self.assertIn('/meus-chamados/', response['Location'])

    def test_anonymous_user_is_redirected_to_login_for_user_directory(self):
        response = self.client.get(reverse('tickets:users'), HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])


class TicketUserDirectoryTests(DeskTechnicalTestCase):

    def setUp(self):
        super().setUp()
        from access_inventory.models import ADOrganizationalUnit, ADUser
        from tickets.models import TicketCategory

        self.ou, _ = ADOrganizationalUnit.objects.get_or_create(
            distinguished_name='OU=Financeiro,OU=USUARIOS,DC=nalen,DC=local',
            defaults={'name': 'Financeiro'},
        )
        self.ad_user = ADUser.objects.create(
            sid='S-1-5-21-1000',
            sam_account_name='mariana.souza',
            display_name='Mariana Souza',
            user_principal_name='mariana.souza@nalen.local',
            email='mariana.souza@nalen.local',
            distinguished_name='CN=Mariana Souza,OU=Financeiro,OU=USUARIOS,DC=nalen,DC=local',
            ou=self.ou,
            enabled=True,
        )
        self.category, _ = TicketCategory.objects.get_or_create(name='Acesso', defaults={'description': 'Acesso'})

    def test_identity_matching_ignores_display_name_and_uses_username_or_email(self):
        from tickets.models import Ticket
        from tickets.services.user_directory import find_ad_user_for_ticket

        by_name = Ticket.objects.create(number=9001, title='Nome igual', description='x', requester_name='Mariana Souza')
        by_username = Ticket.objects.create(
            number=9002,
            title='Usuario de rede',
            description='x',
            requester_name='Solicitante',
            requester_username='NALEN\\Mariana.Souza',
        )
        by_email = Ticket.objects.create(
            number=9003,
            title='E-mail',
            description='x',
            requester_name='Solicitante',
            requester_email='MARIANA.SOUZA@nalen.local',
        )

        self.assertEqual(find_ad_user_for_ticket(by_name).status, 'no_identity')
        self.assertEqual(find_ad_user_for_ticket(by_username).user, self.ad_user)
        self.assertEqual(find_ad_user_for_ticket(by_email).user, self.ad_user)

    def test_identity_matching_supports_fk_sam_upn_email_case_and_spaces(self):
        from tickets.models import Ticket
        from tickets.services.user_directory import find_ad_user_for_ticket

        linked = Ticket.objects.create(number=9004, title='FK', description='x', requester_ad_user=self.ad_user)
        by_sam = Ticket.objects.create(number=9005, title='SAM', description='x', requester_username='  mariana.souza  ')
        by_upn = Ticket.objects.create(number=9006, title='UPN', description='x', requester_username='MARIANA.SOUZA@NALEN.LOCAL')
        by_email = Ticket.objects.create(number=9007, title='Mail', description='x', requester_email=' mariana.souza@nalen.local ')

        self.assertEqual(find_ad_user_for_ticket(linked).status, 'linked')
        self.assertEqual(find_ad_user_for_ticket(by_sam).user, self.ad_user)
        self.assertEqual(find_ad_user_for_ticket(by_upn).user, self.ad_user)
        self.assertEqual(find_ad_user_for_ticket(by_email).user, self.ad_user)

    def test_ambiguous_matches_do_not_associate_automatically(self):
        from access_inventory.models import ADUser
        from tickets.models import Ticket
        from tickets.services.user_directory import find_ad_user_for_ticket

        ADUser.objects.create(
            sid='S-1-5-21-1001',
            sam_account_name='mariana.souza',
            display_name='Mariana Souza Duplicada',
            user_principal_name='mariana.duplicada@nalen.local',
            email='mariana.duplicada@nalen.local',
            distinguished_name='CN=Mariana Duplicada,OU=Financeiro,OU=USUARIOS,DC=nalen,DC=local',
            ou=self.ou,
        )
        ticket = Ticket.objects.create(number=9008, title='Ambiguo', description='x', requester_username='mariana.souza')

        match = find_ad_user_for_ticket(ticket)

        self.assertEqual(match.status, 'ambiguous')
        self.assertIsNone(match.user)
        self.assertEqual(len(match.candidates), 2)

    def test_users_list_and_profile_render_real_ad_data(self):
        from tickets.models import Ticket

        Ticket.objects.create(
            number=9010,
            title='Acesso ao ERP',
            description='Validar acesso',
            requester_ad_user=self.ad_user,
            requester_name='Mariana Souza',
            requester_email='mariana.souza@nalen.local',
            requester_username='mariana.souza',
            category=self.category,
        )

        list_response = self.client.get(reverse('tickets:users'), HTTP_HOST=self.host)
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, 'Mariana Souza')
        self.assertContains(list_response, 'mariana.souza')

        detail_response = self.client.get(reverse('tickets:user-detail', args=[self.ad_user.pk]), HTTP_HOST=self.host)
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, 'Abrir chamado')
        self.assertContains(detail_response, 'requester_ad_user')
        self.assertContains(detail_response, '#9010')

    def test_users_list_filters_open_tickets_endpoint_and_ou(self):
        from agents.models import AgentMachine
        from tickets.models import Ticket

        AgentMachine.create_with_token(
            hostname='FIN-012',
            domain='nalen.local',
            status=AgentMachine.STATUS_ONLINE,
            last_seen_at=timezone.now(),
            last_logged_user='mariana.souza',
        )
        Ticket.objects.create(
            number=9011,
            title='Aberto',
            description='x',
            requester_ad_user=self.ad_user,
            requester_username='mariana.souza',
            status=Ticket.STATUS_NEW,
            category=self.category,
        )

        open_response = self.client.get(reverse('tickets:users'), {'state': 'with-open-tickets'}, HTTP_HOST=self.host)
        endpoint_response = self.client.get(reverse('tickets:users'), {'state': 'with-endpoint'}, HTTP_HOST=self.host)
        ou_response = self.client.get(reverse('tickets:users'), {'ou': 'Financeiro'}, HTTP_HOST=self.host)
        search_response = self.client.get(reverse('tickets:users'), {'q': 'mariana.souza'}, HTTP_HOST=self.host)

        for response in [open_response, endpoint_response, ou_response, search_response]:
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'Mariana Souza')

    def test_users_filters_are_applied_before_pagination(self):
        from agents.models import AgentMachine
        from access_inventory.models import ADUser
        from tickets.models import Ticket

        for index in range(30):
            user = ADUser.objects.create(
                sid=f'S-1-5-21-PAGE-{index}',
                sam_account_name=f'page.user.{index}',
                display_name=f'Page User {index:02d}',
                user_principal_name=f'page.user.{index}@nalen.local',
                email=f'page.user.{index}@nalen.local',
                distinguished_name=f'CN=Page User {index:02d},OU=Financeiro,DC=nalen,DC=local',
                ou=self.ou,
            )
            if index == 29:
                Ticket.objects.create(
                    title='Chamado da pagina filtrada',
                    description='x',
                    requester_ad_user=user,
                    status=Ticket.STATUS_NEW,
                )
            if index == 28:
                AgentMachine.create_with_token(
                    hostname='PAGE-ENDPOINT',
                    domain='nalen.local',
                    status=AgentMachine.STATUS_ONLINE,
                    last_seen_at=timezone.now(),
                    last_logged_user=user.sam_account_name,
                )

        response = self.client.get(
            reverse('tickets:users'),
            {'q': 'Page User', 'state': 'with-open-tickets', 'page': 2},
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['page_obj'].paginator.count, 1)
        self.assertEqual(response.context['page_obj'].paginator.num_pages, 1)
        self.assertEqual(len(response.context['user_rows']), 1)
        self.assertContains(response, 'Page User 29')

        endpoint_response = self.client.get(
            reverse('tickets:users'),
            {'q': 'Page User', 'state': 'with-endpoint', 'page': 2},
            HTTP_HOST=self.host,
        )
        self.assertEqual(endpoint_response.context['page_obj'].paginator.count, 1)
        self.assertEqual(len(endpoint_response.context['user_rows']), 1)
        self.assertContains(endpoint_response, 'Page User 28')

    def test_users_list_query_budget_is_bounded_and_full_page_is_supported(self):
        from access_inventory.models import ADUser

        for index in range(30):
            ADUser.objects.create(
                sid=f'S-1-5-21-BUDGET-{index}',
                sam_account_name=f'budget.user.{index}',
                display_name=f'Budget User {index:02d}',
                user_principal_name=f'budget.user.{index}@nalen.local',
                email=f'budget.user.{index}@nalen.local',
                distinguished_name=f'CN=Budget User {index:02d},OU=Financeiro,DC=nalen,DC=local',
                ou=self.ou,
            )

        with CaptureQueriesContext(connection) as one_user_queries:
            one_user_response = self.client.get(
                reverse('tickets:users'), {'q': 'Budget User 00'}, HTTP_HOST=self.host
            )
        with CaptureQueriesContext(connection) as many_user_queries:
            many_user_response = self.client.get(
                reverse('tickets:users'), {'q': 'Budget User'}, HTTP_HOST=self.host
            )

        self.assertEqual(one_user_response.status_code, 200)
        self.assertEqual(many_user_response.status_code, 200)
        self.assertEqual(len(many_user_response.context['user_rows']), 25)
        self.assertLessEqual(len(many_user_queries), len(one_user_queries) + 2)

    def test_user_profile_totals_are_not_limited_to_recent_ticket_window(self):
        from tickets.models import Ticket

        tickets = [
            Ticket(
                number=10000 + index,
                title=f'Historico {index}',
                description='x',
                requester_ad_user=self.ad_user,
                status=Ticket.STATUS_RESOLVED if index < 40 else Ticket.STATUS_NEW,
            )
            for index in range(205)
        ]
        Ticket.objects.bulk_create(tickets)

        response = self.client.get(reverse('tickets:user-detail', args=[self.ad_user.pk]), HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_count'], 205)
        self.assertEqual(response.context['resolved_count'], 40)
        self.assertEqual(response.context['open_count'], 165)
        self.assertEqual(len(response.context['recent_tickets']), 5)

    def test_user_without_ticket_or_endpoint_has_empty_states(self):
        detail_response = self.client.get(reverse('tickets:user-detail', args=[self.ad_user.pk]), HTTP_HOST=self.host)

        self.assertContains(detail_response, 'Nenhum chamado associado')
        self.assertContains(detail_response, 'Nenhum endpoint associado')

    def test_profile_prefills_quick_ticket_drawer_and_api_persists_ad_user(self):
        from tickets.models import Ticket

        response = self.client.get(
            reverse('tickets:central'),
            {'new': '1', 'requester_ad_user': str(self.ad_user.pk)},
            HTTP_HOST=self.host,
        )
        self.assertContains(response, 'quick-ticket-prefill')
        self.assertContains(response, 'Mariana Souza')

        payload = {
            'requester_ad_user_id': str(self.ad_user.pk),
            'requester': 'Browser value',
            'title': 'Novo acesso',
            'description': 'Linha 1\n\nLinha 2',
            'category': 'Acesso',
            'priority': 'normal',
        }
        api_response = self.client.post(
            reverse('tickets:api-create'),
            data=payload,
            content_type='application/json',
            HTTP_HOST=self.host,
        )
        self.assertEqual(api_response.status_code, 201)
        ticket = Ticket.objects.get(title='Novo acesso')
        self.assertEqual(ticket.requester_ad_user, self.ad_user)
        self.assertEqual(ticket.requester_name, 'Mariana Souza')
        self.assertEqual(ticket.requester_username, 'mariana.souza')
        self.assertEqual(ticket.requester_email, 'mariana.souza@nalen.local')
        self.assertEqual(ticket.requester_department, 'Financeiro')
        self.assertIn('\n\n', ticket.description)

    def test_api_ignores_client_partner_flag_for_linked_non_partner(self):
        from tickets.models import Ticket

        response = self.client.post(
            reverse('tickets:api-create'),
            data={
                'requester_ad_user_id': str(self.ad_user.pk),
                'requester': 'Browser value',
                'title': 'Solicitacao normal',
                'description': 'x',
                'category': 'Acesso',
                'priority': Ticket.PRIORITY_NORMAL,
                'requester_is_partner': True,
            },
            content_type='application/json',
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 201)
        ticket = Ticket.objects.get(title='Solicitacao normal')
        self.assertFalse(ticket.requester_is_partner)
        self.assertEqual(ticket.priority, Ticket.PRIORITY_NORMAL)

    def test_endpoint_context_prefers_online_recent_endpoint_and_deduplicates_history(self):
        from agents.models import AgentMachine, InventorySnapshot
        from tickets.services.user_directory import endpoint_context_for_ad_user

        offline, _ = AgentMachine.create_with_token(
            hostname='OLD-NOTE',
            domain='nalen.local',
            status=AgentMachine.STATUS_OFFLINE,
            last_seen_at=timezone.now() - timezone.timedelta(days=2),
            last_logged_user='mariana.souza',
        )
        old_online, _ = AgentMachine.create_with_token(
            hostname='OLD-ONLINE',
            domain='nalen.local',
            status=AgentMachine.STATUS_ONLINE,
            last_seen_at=timezone.now() - timezone.timedelta(hours=2),
            last_logged_user='mariana.souza',
        )
        recent_online, _ = AgentMachine.create_with_token(
            hostname='FIN-012',
            domain='nalen.local',
            status=AgentMachine.STATUS_ONLINE,
            last_seen_at=timezone.now(),
            last_logged_user='NALEN\\mariana.souza',
        )
        InventorySnapshot.objects.create(
            machine=offline,
            collected_at=timezone.now() - timezone.timedelta(days=3),
            received_at=timezone.now() - timezone.timedelta(days=3),
            hostname='OLD-NOTE',
            logged_user='mariana.souza@nalen.local',
        )
        InventorySnapshot.objects.create(
            machine=recent_online,
            collected_at=timezone.now() - timezone.timedelta(minutes=10),
            received_at=timezone.now() - timezone.timedelta(minutes=10),
            hostname='FIN-012',
            logged_user='mariana.souza',
        )

        context = endpoint_context_for_ad_user(self.ad_user)
        self.assertEqual(context['current']['hostname'], recent_online.hostname)
        self.assertTrue(context['multiple_online'])
        self.assertEqual(context['history_count'], 3)
        self.assertEqual(len([item for item in context['history'] if item['hostname'] == 'FIN-012']), 1)

    def test_profile_renders_groups(self):
        from access_inventory.models import ADGroup, ADGroupMembership

        group = ADGroup.objects.create(
            sid='S-1-5-21-GRP',
            sam_account_name='GG_FINANCEIRO',
            name='GG Financeiro',
            distinguished_name='CN=GG Financeiro,OU=Grupos,DC=nalen,DC=local',
        )
        ADGroupMembership.objects.create(parent_group=group, member_user=self.ad_user)

        response = self.client.get(reverse('tickets:user-detail', args=[self.ad_user.pk]), HTTP_HOST=self.host)

        self.assertContains(response, 'GG Financeiro')

    def test_backfill_command_dry_run_and_apply(self):
        from tickets.models import Ticket

        ticket = Ticket.objects.create(
            number=9020,
            title='Backfill',
            description='x',
            requester_username='mariana.souza',
        )
        dry_output = StringIO()
        call_command('link_ticket_requesters', stdout=dry_output)
        ticket.refresh_from_db()
        self.assertIsNone(ticket.requester_ad_user)
        self.assertIn('DRY-RUN', dry_output.getvalue())

        apply_output = StringIO()
        call_command('link_ticket_requesters', '--apply', stdout=apply_output)
        ticket.refresh_from_db()
        self.assertEqual(ticket.requester_ad_user, self.ad_user)
        self.assertIn('APPLY', apply_output.getvalue())

    def test_backfill_command_reports_ambiguity_without_apply(self):
        from access_inventory.models import ADUser
        from tickets.models import Ticket

        ADUser.objects.create(
            sid='S-1-5-21-1002',
            sam_account_name='mariana.souza',
            display_name='Outra Mariana',
            user_principal_name='outra.mariana@nalen.local',
            email='outra.mariana@nalen.local',
            distinguished_name='CN=Outra Mariana,OU=Financeiro,OU=USUARIOS,DC=nalen,DC=local',
            ou=self.ou,
        )
        ticket = Ticket.objects.create(number=9021, title='Ambiguo cmd', description='x', requester_username='mariana.souza')

        output = StringIO()
        call_command('link_ticket_requesters', '--apply', stdout=output)
        ticket.refresh_from_db()

        self.assertIsNone(ticket.requester_ad_user)
        self.assertIn('ambiguous: 1', output.getvalue())


class TicketDetailLayoutTests(DeskTechnicalTestCase):

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
        self.assertContains(response, 'Risco do endpoint')
        self.assertContains(response, 'nw-device-metric-grid')

    def test_detail_page_renders_resolution_and_audit_drawers(self):
        response = self.client.get(reverse('tickets:detail', args=[1048]), HTTP_HOST=self.host)

        self.assertContains(response, 'desk-resolution-drawer')
        self.assertContains(response, 'Tipo de solução')
        self.assertContains(response, 'desk-audit-drawer')
        self.assertContains(response, 'Auditoria do chamado')


class TicketCreateLayoutTests(DeskTechnicalTestCase):

    def test_create_page_renders_quick_and_complete_modes(self):
        response = self.client.get(reverse('tickets:create'), HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Novo registro avançado')
        self.assertContains(response, 'Modo avançado')
        self.assertContains(response, 'Tipo de registro')
        self.assertContains(response, 'desk-create-wizard')
        self.assertContains(response, 'nw-record-type-strip')

    def test_create_page_renders_problem_and_requester_steps(self):
        response = self.client.get(reverse('tickets:create'), HTTP_HOST=self.host)

        self.assertContains(response, 'Identificação')
        self.assertContains(response, 'Solicitante')
        self.assertContains(response, 'Título')
        self.assertContains(response, 'Descrição')
        self.assertContains(response, 'Buscar por nome, e-mail ou setor')

    def test_create_page_explains_vip_priority_change(self):
        response = self.client.get(reverse('tickets:create'), HTTP_HOST=self.host)

        self.assertContains(response, 'Selecione um solicitante para ver dados compactos')
        self.assertContains(response, 'Prioridade sugerida')
        self.assertContains(response, 'data-vip-explanation')

    def test_create_page_renders_rmm_endpoint_and_duplicate_notice(self):
        response = self.client.get(
            reverse('tickets:create'),
            {'category': 'Seguranca', 'endpoint': 'FIN-012'},
            HTTP_HOST=self.host,
        )

        self.assertContains(response, 'Endpoint / ativo relacionado')
        self.assertContains(response, 'FIN-012')
        self.assertContains(response, 'Possível chamado duplicado')
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


class TicketDashboardLayoutTests(DeskTechnicalTestCase):

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


class TicketAdminExperienceTests(DeskTechnicalTestCase):

    def test_categories_render_inline_table_and_drawer(self):
        response = self.client.get(reverse('tickets:categories'), HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Tabela de categorias')
        self.assertContains(response, 'desk-admin-table')
        self.assertContains(response, 'Editor de categoria')
        self.assertContains(response, 'Mesclar categorias')

    def test_categories_warn_when_open_tickets_exist(self):
        response = self.client.get(reverse('tickets:categories'), HTTP_HOST=self.host)

        self.assertContains(response, 'data-category-toggle')
        self.assertContains(response, 'Checklist')

    def test_automation_rules_render_shared_rmm_rule_and_builder(self):
        response = self.client.get(reverse('tickets:automation'), HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Motor de automa')
        self.assertContains(response, 'Criar chamados para alertas RMM relevantes')
        self.assertContains(response, 'Mapeamento compartilhado RMM')
        self.assertContains(response, 'Construtor de regra')

    def test_settings_render_hub_navigation(self):
        response = self.client.get(reverse('tickets:settings'), HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'nw-settings-shell')
        self.assertContains(response, 'Categorias')
        self.assertContains(response, 'Filas')
        self.assertContains(response, 'SLAs')
        self.assertContains(response, 'Templates')

    def test_settings_integrations_use_shared_alert_mapping(self):
        response = self.client.get(reverse('tickets:settings'), {'section': 'integrations'}, HTTP_HOST=self.host)

        self.assertContains(response, 'Configurações do Desk')
        self.assertContains(response, 'Resposta automática')
        self.assertContains(response, 'Automação: SLA próximo')
        self.assertContains(response, 'data-settings-tab="templates"')

    def test_settings_sla_permissions_and_audit_sections_render(self):
        sla = self.client.get(reverse('tickets:settings'), {'section': 'sla'}, HTTP_HOST=self.host)
        permissions = self.client.get(reverse('tickets:settings'), {'section': 'permissions'}, HTTP_HOST=self.host)
        audit = self.client.get(reverse('tickets:settings'), {'section': 'audit'}, HTTP_HOST=self.host)

        self.assertContains(sla, 'Gerencie tempos de resposta')
        self.assertContains(permissions, 'Resumo')
        self.assertContains(audit, 'Últimas alterações')
