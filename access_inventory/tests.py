import json
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from .agent_auth import create_inventory_agent_with_token
from .models import (
    ADGroup,
    ADGroupMembership,
    ADOrganizationalUnit,
    ADUser,
    AccessReviewFolder,
    AccessReviewPlan,
    AccessReviewPrincipal,
    AccessReviewRule,
    AclEntry,
    FileServer,
    Folder,
    InventoryAgent,
    InventoryAgentRun,
    Share,
)
from .services.access_review import (
    describe_acl_rights,
    explain_permission,
    get_current_effective_user_access,
    is_partner_review_user,
    is_displayable_review_user,
)
from .services.import_access_review_rules import import_access_review_rules_from_rows
from .services.resolve_acl_identities import resolve_acl_identities


BASE_DIR = Path(__file__).resolve().parent.parent


class InventoryAgentApiTests(TestCase):
    def setUp(self):
        self.agent, self.token = create_inventory_agent_with_token(
            name='FS01 Collector',
            hostname='FS01',
        )

    def auth_headers(self, token=None):
        return {'HTTP_X_NIGHTOWL_AGENT_TOKEN': token or self.token}

    def load_sample(self, name):
        path = BASE_DIR / 'sample_data' / 'access_inventory' / name
        return json.loads(path.read_text(encoding='utf-8'))

    def test_create_inventory_agent_hashes_token(self):
        agent = InventoryAgent.objects.get(pk=self.agent.pk)

        self.assertTrue(agent.token_hash)
        self.assertNotEqual(agent.token_hash, self.token)
        self.assertTrue(agent.enabled)

    def test_rejects_invalid_token(self):
        response = self.client.post(
            reverse('access-inventory-agent-heartbeat'),
            data={'version': '0.1.0'},
            content_type='application/json',
            **self.auth_headers(token='wrong-token'),
        )

        self.assertEqual(response.status_code, 401)

    def test_accepts_valid_token_and_records_heartbeat(self):
        response = self.client.post(
            reverse('access-inventory-agent-heartbeat'),
            data={'version': '0.1.0'},
            content_type='application/json',
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.version, '0.1.0')
        self.assertIsNotNone(self.agent.last_seen_at)
        self.assertTrue(
            InventoryAgentRun.objects.filter(
                agent=self.agent,
                run_type=InventoryAgentRun.RUN_HEARTBEAT,
                status=InventoryAgentRun.STATUS_SUCCESS,
            ).exists()
        )

    def test_file_acl_payload_imports_sample(self):
        payload = self.load_sample('file_acl_sample.json')

        response = self.client.post(
            reverse('access-inventory-agent-file-acl'),
            data=json.dumps(payload),
            content_type='application/json',
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(FileServer.objects.count(), 1)
        self.assertEqual(Share.objects.count(), 1)
        self.assertEqual(Folder.objects.count(), 2)
        self.assertEqual(AclEntry.objects.count(), 2)
        run = InventoryAgentRun.objects.get(run_type=InventoryAgentRun.RUN_FILE_ACL)
        self.assertEqual(run.status, InventoryAgentRun.STATUS_SUCCESS)
        self.assertGreater(run.items_created, 0)

    def test_ad_inventory_payload_imports_sample(self):
        payload = self.load_sample('ad_inventory_sample.json')

        response = self.client.post(
            reverse('access-inventory-agent-ad-inventory'),
            data=json.dumps(payload),
            content_type='application/json',
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(ADOrganizationalUnit.objects.count(), 2)
        self.assertEqual(ADUser.objects.count(), 2)
        self.assertEqual(ADGroup.objects.count(), 2)
        run = InventoryAgentRun.objects.get(run_type=InventoryAgentRun.RUN_AD_INVENTORY)
        self.assertEqual(run.status, InventoryAgentRun.STATUS_SUCCESS)
        self.assertGreater(run.items_created, 0)


class ResolveAclIdentityTests(TestCase):
    def create_folder(self):
        file_server = FileServer.objects.create(name='FS01')
        share = Share.objects.create(file_server=file_server, name='Dados', unc_path='\\\\FS01\\Dados')
        return Folder.objects.create(share=share, path='Financeiro')

    def test_resolves_acl_identity_to_user(self):
        folder = self.create_folder()
        user = ADUser.objects.create(
            sid='S-1-5-21-1-1001',
            sam_account_name='j.silva',
            display_name='Joao Silva',
        )
        acl = AclEntry.objects.create(
            folder=folder,
            identity_sid=user.sid,
            identity_name='CONTROL\\j.silva',
            rights='ReadAndExecute',
            access_type=AclEntry.ACCESS_ALLOW,
        )

        result = resolve_acl_identities()

        acl.refresh_from_db()
        self.assertEqual(result.resolved_users, 1)
        self.assertEqual(acl.resolved_ad_user, user)
        self.assertEqual(acl.resolved_identity_type, AclEntry.IDENTITY_USER)
        self.assertIsNotNone(acl.resolved_at)

    def test_resolves_acl_identity_to_group(self):
        folder = self.create_folder()
        group = ADGroup.objects.create(
            sid='S-1-5-21-1-2001',
            sam_account_name='GG_FINANCEIRO_RW',
            name='GG Financeiro RW',
        )
        acl = AclEntry.objects.create(
            folder=folder,
            identity_sid=group.sid,
            identity_name='CONTROL\\GG_FINANCEIRO_RW',
            rights='Modify',
            access_type=AclEntry.ACCESS_ALLOW,
        )

        result = resolve_acl_identities()

        acl.refresh_from_db()
        self.assertEqual(result.resolved_groups, 1)
        self.assertEqual(acl.resolved_ad_group, group)
        self.assertEqual(acl.resolved_identity_type, AclEntry.IDENTITY_GROUP)

    def test_marks_unknown_when_sid_does_not_match(self):
        folder = self.create_folder()
        acl = AclEntry.objects.create(
            folder=folder,
            identity_sid='S-1-5-21-1-9999',
            identity_name='CONTROL\\UNKNOWN',
            rights='Read',
            access_type=AclEntry.ACCESS_ALLOW,
        )

        result = resolve_acl_identities()

        acl.refresh_from_db()
        self.assertEqual(result.unknown, 1)
        self.assertEqual(acl.resolved_identity_type, AclEntry.IDENTITY_UNKNOWN)
        self.assertIsNone(acl.resolved_ad_user)
        self.assertIsNone(acl.resolved_ad_group)


class AccessReviewTests(TestCase):
    def setUp(self):
        self.plan = AccessReviewPlan.objects.create(
            name='Plano dinamico de acessos - 2026',
            description='Proposta executiva baseada em dados do plano.',
            status=AccessReviewPlan.STATUS_DRAFT,
        )
        self.folder = AccessReviewFolder.objects.create(
            plan=self.plan,
            area_name='controlsul',
            name='Pasta de teste',
            proposed_path='controlsul\\Pasta de teste',
        )
        self.principal = AccessReviewPrincipal.objects.create(
            plan=self.plan,
            principal_type=AccessReviewPrincipal.PRINCIPAL_GROUP,
            display_name='Grupo tecnico RW',
            proposed_group_name='GG_CONTROLSUL_RW',
        )
        self.rule = AccessReviewRule.objects.create(
            plan=self.plan,
            folder=self.folder,
            principal=self.principal,
            permission_level=AccessReviewRule.PERMISSION_RW,
        )

    def test_permission_explanation_is_executive_friendly(self):
        self.assertIn('criar', explain_permission(AccessReviewRule.PERMISSION_RW))
        self.assertIn('Sem acesso', explain_permission(AccessReviewRule.PERMISSION_NONE))

    def test_review_plan_list_view(self):
        older_plan = AccessReviewPlan.objects.create(
            name='Plano antigo',
            description='Nao deve aparecer na listagem executiva.',
        )
        current_plan = AccessReviewPlan.objects.create(
            name='Plano mais recente',
            description='Deve aparecer na listagem executiva.',
        )

        response = self.client.get(reverse('access_inventory:review-plan-list'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, current_plan.name)
        self.assertNotContains(response, older_plan.name)
        self.assertNotContains(response, self.plan.name)
        self.assertContains(response, 'Abrir an&aacute;lise')

    def test_review_plan_detail_view(self):
        response = self.client.get(reverse('access_inventory:review-plan-detail', args=[self.plan.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Pastas principais')
        self.assertContains(response, self.folder.proposed_path)

    def test_review_folder_detail_view(self):
        response = self.client.get(reverse('access_inventory:review-folder-detail', args=[self.plan.id, self.folder.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.principal.display_name)
        self.assertContains(response, 'Pode abrir, criar, editar e excluir arquivos.')

    def test_review_folder_detail_renders_first_direct_child(self):
        root = AccessReviewFolder.objects.create(
            plan=self.plan,
            area_name='controlsul',
            name='controlsul',
            proposed_path='controlsul',
        )
        area = AccessReviewFolder.objects.create(
            plan=self.plan,
            area_name='Administrativo',
            name='Administrativo',
            proposed_path='controlsul\\Administrativo',
            parent=root,
        )
        finance = AccessReviewFolder.objects.create(
            plan=self.plan,
            area_name='Administrativo',
            name='FINANCEIRO',
            proposed_path='controlsul\\Administrativo\\FINANCEIRO',
            parent=area,
        )
        AccessReviewFolder.objects.create(
            plan=self.plan,
            area_name='Administrativo',
            name='CAFOFO',
            proposed_path='controlsul\\Administrativo\\FINANCEIRO\\CAFOFO',
            parent=finance,
            sort_order=1,
        )
        AccessReviewFolder.objects.create(
            plan=self.plan,
            area_name='Administrativo',
            name='CAIXA',
            proposed_path='controlsul\\Administrativo\\FINANCEIRO\\CAIXA',
            parent=finance,
            sort_order=2,
        )

        response = self.client.get(reverse('access_inventory:review-folder-detail', args=[self.plan.id, finance.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Subpastas')
        self.assertContains(response, 'CAFOFO')
        self.assertContains(response, 'CAIXA')

    def test_final_review_folder_detail_does_not_render_empty_subfolders_panel(self):
        response = self.client.get(reverse('access_inventory:review-folder-detail', args=[self.plan.id, self.folder.id]))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '<h2>Subpastas</h2>', html=True)
        self.assertNotContains(response, 'Nenhuma subpasta direta cadastrada neste ramo.')

    def test_review_folder_detail_final_result_is_after_current_access(self):
        response = self.client.get(reverse('access_inventory:review-folder-detail', args=[self.plan.id, self.folder.id]))

        content = response.content.decode()
        self.assertLess(
            content.index('Permiss&otilde;es atuais encontradas'),
            content.index('Resultado final dos acessos revistos'),
        )

    def test_review_plan_detail_applies_temporary_executive_scope_when_paths_exist(self):
        current_plan = AccessReviewPlan.objects.create(name='Plano executivo atual')
        root = AccessReviewFolder.objects.create(
            plan=current_plan,
            area_name='controlsul',
            name='controlsul',
            proposed_path='controlsul',
        )
        admin = AccessReviewFolder.objects.create(
            plan=current_plan,
            area_name='controlsul',
            name='Administrativo',
            proposed_path='controlsul\\Administrativo',
            parent=root,
        )
        juridico = AccessReviewFolder.objects.create(
            plan=current_plan,
            area_name='controlsul',
            name='Juridico',
            proposed_path='controlsul\\Juridico',
            parent=root,
        )
        AccessReviewFolder.objects.create(
            plan=current_plan,
            area_name='controlsul',
            name='Comum',
            proposed_path='controlsul\\Comum',
            parent=root,
        )

        response = self.client.get(reverse('access_inventory:review-plan-detail', args=[current_plan.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, admin.proposed_path)
        self.assertContains(response, juridico.proposed_path)
        self.assertNotContains(response, 'controlsul\\Comum')

    def test_current_effective_user_access_expands_group_acl_to_users(self):
        file_server = FileServer.objects.create(name='FS-ACL')
        share = Share.objects.create(file_server=file_server, name='Dados', unc_path='\\\\FS-ACL\\Dados')
        current_folder = Folder.objects.create(share=share, path='controlsul\\Administrativo')
        review_folder = AccessReviewFolder.objects.create(
            plan=self.plan,
            area_name='controlsul',
            name='Administrativo',
            proposed_path='controlsul\\Administrativo',
            current_folder=current_folder,
        )
        user = ADUser.objects.create(
            sid='S-1-5-21-5001',
            sam_account_name='ana',
            display_name='Ana',
        )
        group = ADGroup.objects.create(
            sid='S-1-5-21-6001',
            sam_account_name='GG_ADMIN_RW',
            name='GG Admin RW',
        )
        ADGroupMembership.objects.create(parent_group=group, member_user=user)
        AclEntry.objects.create(
            folder=current_folder,
            identity_sid=group.sid,
            identity_name='CONTROL\\GG_ADMIN_RW',
            resolved_ad_group=group,
            resolved_identity_type=AclEntry.IDENTITY_GROUP,
            rights='Modify, Synchronize',
        )

        result = get_current_effective_user_access(review_folder)

        self.assertEqual(len(result['rows']), 1)
        self.assertEqual(result['rows'][0]['user'], user)
        self.assertEqual(result['rows'][0]['via_group'], group)
        self.assertEqual(result['rows'][0]['permission'], 'Leitura e escrita')

    def test_review_folder_detail_shows_direct_current_user_access(self):
        file_server = FileServer.objects.create(name='FS-DIRECT')
        share = Share.objects.create(file_server=file_server, name='Dados Direct', unc_path='\\\\FS-DIRECT\\Dados')
        current_folder = Folder.objects.create(share=share, path='controlsul\\Administrativo')
        self.folder.current_folder = current_folder
        self.folder.save(update_fields=['current_folder'])
        user = ADUser.objects.create(
            sid='S-1-5-21-7001',
            sam_account_name='gabriel',
            display_name='Gabriel Oliveira',
        )
        AclEntry.objects.create(
            folder=current_folder,
            identity_sid=user.sid,
            identity_name='CONTROL\\gabriel',
            resolved_ad_user=user,
            resolved_identity_type=AclEntry.IDENTITY_USER,
            rights='ReadAndExecute, Synchronize',
        )

        response = self.client.get(reverse('access_inventory:review-folder-detail', args=[self.plan.id, self.folder.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Permiss&otilde;es atuais encontradas')
        self.assertContains(response, 'Gabriel Oliveira')
        self.assertContains(response, 'Somente leitura')
        self.assertContains(response, 'Pode abrir, listar e visualizar arquivos.')
        self.assertContains(response, 'acesso direto na pasta')

    def test_review_folder_detail_hides_technical_direct_current_user(self):
        file_server = FileServer.objects.create(name='FS-TECH-DIRECT')
        share = Share.objects.create(file_server=file_server, name='Dados Tech Direct', unc_path='\\\\FS-TECH-DIRECT\\Dados')
        current_folder = Folder.objects.create(share=share, path='controlsul\\Administrativo\\Tecnica')
        self.folder.current_folder = current_folder
        self.folder.save(update_fields=['current_folder'])
        technical_user = ADUser.objects.create(
            sid='S-1-5-21-7101',
            sam_account_name='administrador',
            display_name='  ÁDMINISTRADOR  ',
        )
        normal_user = ADUser.objects.create(
            sid='S-1-5-21-7102',
            sam_account_name='ana.souza',
            display_name='Ana Souza',
        )
        for user in (technical_user, normal_user):
            AclEntry.objects.create(
                folder=current_folder,
                identity_sid=user.sid,
                identity_name=f'CONTROL\\{user.sam_account_name}',
                resolved_ad_user=user,
                resolved_identity_type=AclEntry.IDENTITY_USER,
                rights='ReadAndExecute, Synchronize',
            )

        response = self.client.get(reverse('access_inventory:review-folder-detail', args=[self.plan.id, self.folder.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ana Souza')
        self.assertNotContains(response, 'ÁDMINISTRADOR')
        self.assertContains(response, 'Contas t&eacute;cnicas, usu&aacute;rios inativos e s&oacute;cios com acesso geral foram ocultados')

    def test_review_folder_detail_hides_backup_cs_and_inactive_users_from_group_access(self):
        file_server = FileServer.objects.create(name='FS-TECH-GROUP')
        share = Share.objects.create(file_server=file_server, name='Dados Tech Group', unc_path='\\\\FS-TECH-GROUP\\Dados')
        current_folder = Folder.objects.create(share=share, path='controlsul\\Administrativo\\Grupo')
        self.folder.current_folder = current_folder
        self.folder.save(update_fields=['current_folder'])
        group = ADGroup.objects.create(
            sid='S-1-5-21-8101',
            sam_account_name='GG_TESTE_RW',
            name='GG Teste RW',
        )
        backup_user = ADUser.objects.create(
            sid='S-1-5-21-8102',
            sam_account_name='backup.cs',
            display_name='Backup   CS',
        )
        inactive_user = ADUser.objects.create(
            sid='S-1-5-21-8103',
            sam_account_name='usuario.inativo',
            display_name='Usuario Inativo',
            enabled=False,
        )
        normal_user = ADUser.objects.create(
            sid='S-1-5-21-8104',
            sam_account_name='carlos.lima',
            display_name='Carlos Lima',
        )
        for user in (backup_user, inactive_user, normal_user):
            ADGroupMembership.objects.create(parent_group=group, member_user=user)
        AclEntry.objects.create(
            folder=current_folder,
            identity_sid=group.sid,
            identity_name='CONTROL\\GG_TESTE_RW',
            resolved_ad_group=group,
            resolved_identity_type=AclEntry.IDENTITY_GROUP,
            rights='Modify, Synchronize',
        )

        response = self.client.get(reverse('access_inventory:review-folder-detail', args=[self.plan.id, self.folder.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Carlos Lima')
        self.assertNotContains(response, 'Backup   CS')
        self.assertNotContains(response, 'Usuario Inativo')
        self.assertContains(response, 'via grupo GG Teste RW')

    def test_review_folder_detail_shows_group_current_access_as_users(self):
        file_server = FileServer.objects.create(name='FS-GROUP')
        share = Share.objects.create(file_server=file_server, name='Dados Group', unc_path='\\\\FS-GROUP\\Dados')
        current_folder = Folder.objects.create(share=share, path='controlsul\\Juridico')
        self.folder.current_folder = current_folder
        self.folder.save(update_fields=['current_folder'])
        user = ADUser.objects.create(
            sid='S-1-5-21-7002',
            sam_account_name='ana',
            display_name='Ana Souza',
        )
        group = ADGroup.objects.create(
            sid='S-1-5-21-8002',
            sam_account_name='GG_JURIDICO_RW',
            name='GG Juridico RW',
        )
        ADGroupMembership.objects.create(parent_group=group, member_user=user)
        AclEntry.objects.create(
            folder=current_folder,
            identity_sid=group.sid,
            identity_name='CONTROL\\GG_JURIDICO_RW',
            resolved_ad_group=group,
            resolved_identity_type=AclEntry.IDENTITY_GROUP,
            rights='Modify, Synchronize',
            inherited=True,
        )

        response = self.client.get(reverse('access_inventory:review-folder-detail', args=[self.plan.id, self.folder.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ana Souza')
        self.assertContains(response, 'Leitura e escrita')
        self.assertContains(response, 'Pode abrir, criar, alterar e excluir arquivos nesta pasta.')
        self.assertContains(response, 'via grupo GG Juridico RW')
        self.assertContains(response, 'herdado')

    def test_domain_admins_current_access_is_visible_and_revoked_from_business_access(self):
        file_server = FileServer.objects.create(name='FS-DA')
        share = Share.objects.create(file_server=file_server, name='Dados DA', unc_path='\\\\FS-DA\\Dados')
        current_folder = Folder.objects.create(share=share, path='controlsul\\Administrativo\\DA')
        self.folder.current_folder = current_folder
        self.folder.save(update_fields=['current_folder'])
        user = ADUser.objects.create(
            sid='S-1-5-21-8201',
            sam_account_name='carlos.lima',
            display_name='Carlos Lima',
        )
        domain_admins = ADGroup.objects.create(
            sid='S-1-5-21-8202',
            sam_account_name='Domain Admins',
            name='Domain Admins',
            distinguished_name='CN=Domain Admins,CN=Users,DC=control,DC=local',
        )
        ADGroupMembership.objects.create(parent_group=domain_admins, member_user=user)
        AclEntry.objects.create(
            folder=current_folder,
            identity_sid=domain_admins.sid,
            identity_name='CONTROL\\Domain Admins',
            resolved_ad_group=domain_admins,
            resolved_identity_type=AclEntry.IDENTITY_GROUP,
            rights='FullControl',
        )

        response = self.client.get(reverse('access_inventory:review-folder-detail', args=[self.plan.id, self.folder.id]))
        content = response.content.decode()
        current_access_section = content[
            content.index('Permiss&otilde;es atuais encontradas'):
            content.index('Usu&aacute;rios com acesso revogado')
        ]
        revoked_section = content[
            content.index('Usu&aacute;rios com acesso revogado'):
            content.index('Resultado final dos acessos revistos')
        ]
        final_result_section = content[content.index('Resultado final dos acessos revistos'):]

        self.assertEqual(response.status_code, 200)
        self.assertIn('Carlos Lima', current_access_section)
        self.assertIn('acesso administrativo via Domain Admins', current_access_section)
        self.assertIn('Carlos Lima', revoked_section)
        self.assertIn('ACESSO ADMINISTRATIVO SERA REMOVIDO', revoked_section)
        self.assertNotIn('Carlos Lima', final_result_section)
        self.assertNotIn('Domain Admins', final_result_section)

    def test_domain_admins_revoked_card_filters_partners_technical_and_inactive_users(self):
        socios_ou = ADOrganizationalUnit.objects.create(
            distinguished_name='OU=Sócios,DC=control,DC=local',
            name='Sócios',
        )
        file_server = FileServer.objects.create(name='FS-DA-FILTER')
        share = Share.objects.create(file_server=file_server, name='Dados DA Filter', unc_path='\\\\FS-DA-FILTER\\Dados')
        current_folder = Folder.objects.create(share=share, path='controlsul\\Administrativo\\DAFilter')
        self.folder.current_folder = current_folder
        self.folder.save(update_fields=['current_folder'])
        common_user = ADUser.objects.create(
            sid='S-1-5-21-8301',
            sam_account_name='bruna',
            display_name='Bruna Regular',
        )
        partner_user = ADUser.objects.create(
            sid='S-1-5-21-8302',
            sam_account_name='socio',
            display_name='Socio Regular',
            distinguished_name='CN=Socio Regular,OU=Sócios,DC=control,DC=local',
            ou=socios_ou,
        )
        technical_user = ADUser.objects.create(
            sid='S-1-5-21-8303',
            sam_account_name='administrator',
            display_name='Administrator',
        )
        inactive_user = ADUser.objects.create(
            sid='S-1-5-21-8304',
            sam_account_name='inactive',
            display_name='Inactive User',
            enabled=False,
        )
        domain_admins = ADGroup.objects.create(
            sid='S-1-5-21-8305',
            sam_account_name='DOMAIN ADMINS',
            name='DOMAIN ADMINS',
        )
        for user in (common_user, partner_user, technical_user, inactive_user):
            ADGroupMembership.objects.create(parent_group=domain_admins, member_user=user)
        AclEntry.objects.create(
            folder=current_folder,
            identity_sid=domain_admins.sid,
            identity_name='CN=Domain Admins,CN=Users,DC=control,DC=local',
            resolved_ad_group=domain_admins,
            resolved_identity_type=AclEntry.IDENTITY_GROUP,
            rights='FullControl',
        )

        response = self.client.get(reverse('access_inventory:review-folder-detail', args=[self.plan.id, self.folder.id]))
        content = response.content.decode()
        revoked_section = content[
            content.index('Usu&aacute;rios com acesso revogado'):
            content.index('Resultado final dos acessos revistos')
        ]

        self.assertEqual(response.status_code, 200)
        self.assertIn('Bruna Regular', revoked_section)
        self.assertNotIn('Socio Regular', revoked_section)
        self.assertNotIn('Administrator', revoked_section)
        self.assertNotIn('Inactive User', revoked_section)

    def test_explicit_removal_has_priority_over_domain_admins_revocation(self):
        file_server = FileServer.objects.create(name='FS-DA-DEDUP')
        share = Share.objects.create(file_server=file_server, name='Dados DA Dedup', unc_path='\\\\FS-DA-DEDUP\\Dados')
        current_folder = Folder.objects.create(share=share, path='controlsul\\Administrativo\\DADedup')
        self.folder.current_folder = current_folder
        self.folder.save(update_fields=['current_folder'])
        user = ADUser.objects.create(
            sid='S-1-5-21-8401',
            sam_account_name='ana.remover',
            display_name='Ana Remover',
        )
        principal = AccessReviewPrincipal.objects.create(
            plan=self.plan,
            principal_type=AccessReviewPrincipal.PRINCIPAL_USER,
            display_name='Ana Remover',
            sam_account_name='ana.remover',
            ad_user=user,
        )
        AccessReviewRule.objects.create(
            plan=self.plan,
            folder=self.folder,
            principal=principal,
            permission_level=AccessReviewRule.PERMISSION_NONE,
            notes='acao=remover; remover na revisao',
        )
        domain_admins = ADGroup.objects.create(
            sid='S-1-5-21-8402',
            sam_account_name='Domain Admins',
            name='Domain Admins',
        )
        ADGroupMembership.objects.create(parent_group=domain_admins, member_user=user)
        AclEntry.objects.create(
            folder=current_folder,
            identity_sid=domain_admins.sid,
            identity_name='CONTROL\\Domain Admins',
            resolved_ad_group=domain_admins,
            resolved_identity_type=AclEntry.IDENTITY_GROUP,
            rights='FullControl',
        )

        response = self.client.get(reverse('access_inventory:review-folder-detail', args=[self.plan.id, self.folder.id]))
        content = response.content.decode()
        revoked_section = content[
            content.index('Usu&aacute;rios com acesso revogado'):
            content.index('Resultado final dos acessos revistos')
        ]
        final_result_section = content[content.index('Resultado final dos acessos revistos'):]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(revoked_section.count('Ana Remover'), 1)
        self.assertIn('ACESSO SERA REVOGADO', revoked_section)
        self.assertIn('Acesso sera revogado nesta pasta.', revoked_section)
        self.assertNotIn('Ana Remover', final_result_section)

    def test_review_folder_detail_keeps_unknown_acl_discreet(self):
        file_server = FileServer.objects.create(name='FS-UNKNOWN')
        share = Share.objects.create(file_server=file_server, name='Dados Unknown', unc_path='\\\\FS-UNKNOWN\\Dados')
        current_folder = Folder.objects.create(share=share, path='controlsul\\SemResolucao')
        self.folder.current_folder = current_folder
        self.folder.save(update_fields=['current_folder'])
        AclEntry.objects.create(
            folder=current_folder,
            identity_sid='S-1-5-21-unknown',
            identity_name='CONTROL\\DESCONHECIDO',
            resolved_identity_type=AclEntry.IDENTITY_UNKNOWN,
            rights='Read',
        )

        response = self.client.get(reverse('access_inventory:review-folder-detail', args=[self.plan.id, self.folder.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Identidades n&atilde;o resolvidas')
        self.assertContains(response, 'CONTROL\\DESCONHECIDO')

    def test_review_folder_detail_without_current_folder_shows_empty_state(self):
        response = self.client.get(reverse('access_inventory:review-folder-detail', args=[self.plan.id, self.folder.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Nenhuma permiss&atilde;o atual resolvida para usu&aacute;rios nesta pasta.')

    def test_displayable_review_user_helper_handles_service_and_inactive_accounts(self):
        service_user = ADUser.objects.create(
            sid='S-1-5-21-9001',
            sam_account_name='wts.1',
            display_name='WTS 1',
        )
        inactive_user = ADUser.objects.create(
            sid='S-1-5-21-9002',
            sam_account_name='inativo',
            display_name='Usuario Inativo',
            enabled=False,
        )
        normal_user = ADUser.objects.create(
            sid='S-1-5-21-9003',
            sam_account_name='bruna',
            display_name='Bruna',
        )

        self.assertFalse(is_displayable_review_user(service_user))
        self.assertFalse(is_displayable_review_user(inactive_user))
        self.assertTrue(is_displayable_review_user(normal_user))

    def test_partner_review_user_is_detected_by_ou(self):
        socios_ou = ADOrganizationalUnit.objects.create(
            distinguished_name='OU=Sócios,DC=control,DC=local',
            name='Sócios',
        )
        socios_plain_ou = ADOrganizationalUnit.objects.create(
            distinguished_name='OU=Socios,DC=control,DC=local',
            name='Socios',
        )
        other_ou = ADOrganizationalUnit.objects.create(
            distinguished_name='OU=Administrativo,DC=control,DC=local',
            name='Administrativo',
        )
        partner_with_accent = ADUser.objects.create(
            sid='S-1-5-21-9101',
            sam_account_name='ana.partner',
            display_name='Ana Partner',
            distinguished_name='CN=Ana Partner,OU=Sócios,DC=control,DC=local',
            ou=socios_ou,
        )
        partner_without_accent = ADUser.objects.create(
            sid='S-1-5-21-9102',
            sam_account_name='carlos.partner',
            display_name='Carlos Partner',
            distinguished_name='CN=Carlos Partner,OU=Socios,DC=control,DC=local',
            ou=socios_plain_ou,
        )
        regular_user = ADUser.objects.create(
            sid='S-1-5-21-9103',
            sam_account_name='regular',
            display_name='Regular User',
            distinguished_name='CN=Regular User,OU=Administrativo,DC=control,DC=local',
            ou=other_ou,
        )

        self.assertTrue(is_partner_review_user(partner_with_accent))
        self.assertTrue(is_partner_review_user(partner_without_accent))
        self.assertFalse(is_partner_review_user(regular_user))
        self.assertFalse(is_displayable_review_user(partner_with_accent))

    def test_review_folder_detail_shows_partners_only_in_general_access_card(self):
        socios_ou = ADOrganizationalUnit.objects.create(
            distinguished_name='OU=Sócios,DC=control,DC=local',
            name='Sócios',
        )
        file_server = FileServer.objects.create(name='FS-PARTNER')
        share = Share.objects.create(file_server=file_server, name='Dados Partner', unc_path='\\\\FS-PARTNER\\Dados')
        current_folder = Folder.objects.create(share=share, path='controlsul\\Administrativo\\Partner')
        self.folder.current_folder = current_folder
        self.folder.save(update_fields=['current_folder'])
        partner = ADUser.objects.create(
            sid='S-1-5-21-9201',
            sam_account_name='ana.partner',
            display_name='Ana Partner',
            distinguished_name='CN=Ana Partner,OU=Sócios,DC=control,DC=local',
            ou=socios_ou,
        )
        regular_user = ADUser.objects.create(
            sid='S-1-5-21-9202',
            sam_account_name='bruna',
            display_name='Bruna Regular',
        )
        for user in (partner, regular_user):
            AclEntry.objects.create(
                folder=current_folder,
                identity_sid=user.sid,
                identity_name=f'CONTROL\\{user.sam_account_name}',
                resolved_ad_user=user,
                resolved_identity_type=AclEntry.IDENTITY_USER,
                rights='ReadAndExecute, Synchronize',
            )
        AccessReviewPrincipal.objects.create(
            plan=self.plan,
            principal_type=AccessReviewPrincipal.PRINCIPAL_USER,
            display_name='Ana Partner',
            sam_account_name='ana.partner',
            ad_user=partner,
        )
        partner_principal = AccessReviewPrincipal.objects.get(plan=self.plan, display_name='Ana Partner')
        AccessReviewRule.objects.create(
            plan=self.plan,
            folder=self.folder,
            principal=partner_principal,
            permission_level=AccessReviewRule.PERMISSION_RW,
        )

        response = self.client.get(reverse('access_inventory:review-folder-detail', args=[self.plan.id, self.folder.id]))
        content = response.content.decode()
        current_access_section = content[
            content.index('Permiss&otilde;es atuais encontradas'):
            content.index('Resultado final dos acessos revistos')
        ]
        final_result_section = content[content.index('Resultado final dos acessos revistos'):]

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'S&oacute;cios t&ecirc;m acesso geral')
        self.assertContains(response, 'ai-review-partner-grid')
        self.assertContains(response, 'ai-review-partner-grid-item')
        self.assertContains(response, 'Ana Partner')
        self.assertContains(response, 'Bruna Regular')
        self.assertNotIn('Ana Partner', current_access_section)
        self.assertNotIn('Ana Partner', final_result_section)
        self.assertIn('Bruna Regular', current_access_section)
        self.assertContains(response, 's&oacute;cios com acesso geral foram ocultados')

    def test_inactive_partner_is_hidden_from_card_and_tables(self):
        socios_ou = ADOrganizationalUnit.objects.create(
            distinguished_name='OU=Socios,DC=control,DC=local',
            name='Socios',
        )
        inactive_partner = ADUser.objects.create(
            sid='S-1-5-21-9301',
            sam_account_name='inactive.partner',
            display_name='Inactive Partner',
            distinguished_name='CN=Inactive Partner,OU=Socios,DC=control,DC=local',
            ou=socios_ou,
            enabled=False,
        )
        file_server = FileServer.objects.create(name='FS-IP')
        share = Share.objects.create(file_server=file_server, name='Dados IP', unc_path='\\\\FS-IP\\Dados')
        current_folder = Folder.objects.create(share=share, path='controlsul\\Administrativo\\IP')
        self.folder.current_folder = current_folder
        self.folder.save(update_fields=['current_folder'])
        AclEntry.objects.create(
            folder=current_folder,
            identity_sid=inactive_partner.sid,
            identity_name='CONTROL\\inactive.partner',
            resolved_ad_user=inactive_partner,
            resolved_identity_type=AclEntry.IDENTITY_USER,
            rights='ReadAndExecute, Synchronize',
        )

        response = self.client.get(reverse('access_inventory:review-folder-detail', args=[self.plan.id, self.folder.id]))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Inactive Partner')
        self.assertContains(response, 'Lista de s&oacute;cios n&atilde;o identificada automaticamente pela OU do AD.')

    def test_review_folder_detail_keeps_unresolved_textual_proposed_user_visible(self):
        principal = AccessReviewPrincipal.objects.create(
            plan=self.plan,
            principal_type=AccessReviewPrincipal.PRINCIPAL_USER,
            display_name='Bruna',
        )
        AccessReviewRule.objects.create(
            plan=self.plan,
            folder=self.folder,
            principal=principal,
            permission_level=AccessReviewRule.PERMISSION_RW,
        )

        response = self.client.get(reverse('access_inventory:review-folder-detail', args=[self.plan.id, self.folder.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Bruna')

    def create_acl_for_rights(self, rights, access_type=AclEntry.ACCESS_ALLOW):
        file_server = FileServer.objects.create(name=f'FS-RIGHTS-{Folder.objects.count()}')
        share = Share.objects.create(
            file_server=file_server,
            name=f'Dados Rights {Folder.objects.count()}',
            unc_path=f'\\\\{file_server.name}\\Dados',
        )
        folder = Folder.objects.create(share=share, path=f'Pasta {Folder.objects.count()}')
        return AclEntry.objects.create(
            folder=folder,
            identity_name='CONTROL\\user',
            rights=rights,
            access_type=access_type,
        )

    def test_full_control_rights_are_described(self):
        description = describe_acl_rights(self.create_acl_for_rights('FullControl'))

        self.assertEqual(description['permission_label'], 'Controle total')
        self.assertIn('administrar permissoes', description['permission_summary'])

    def test_modify_write_delete_rights_are_read_write(self):
        description = describe_acl_rights(self.create_acl_for_rights('Modify, Synchronize'))

        self.assertEqual(description['permission_label'], 'Leitura e escrita')
        self.assertTrue(any('Modificar conteudo' in detail for detail in description['permission_details']))

    def test_read_list_directory_rights_are_read_only(self):
        description = describe_acl_rights(self.create_acl_for_rights('ListDirectory, ReadAttributes, Synchronize'))

        self.assertEqual(description['permission_label'], 'Somente leitura')
        self.assertIn('Listar conteudo da pasta ou ler dados de arquivos', description['permission_details'])

    def test_special_partial_rights_are_custom_and_translated(self):
        description = describe_acl_rights(
            self.create_acl_for_rights('WriteAttributes, DeleteSubdirectoriesAndFiles, ReadPermissions')
        )

        self.assertEqual(description['permission_label'], 'Personalizada')
        self.assertIn('Alterar atributos, como propriedades e datas', description['permission_details'])
        self.assertIn('Excluir subpastas e arquivos dentro desta pasta', description['permission_details'])
        self.assertIn('Visualizar permissoes', description['permission_details'])

    def test_empty_rights_are_custom_without_breaking(self):
        description = describe_acl_rights(self.create_acl_for_rights(''))

        self.assertEqual(description['permission_label'], 'Personalizada')
        self.assertEqual(description['technical_rights'], '')

    def test_deny_acl_is_described_as_denied(self):
        description = describe_acl_rights(self.create_acl_for_rights('Read', access_type=AclEntry.ACCESS_DENY))

        self.assertEqual(description['permission_label'], 'Negado')
        self.assertIn('Acesso negado', description['permission_summary'])


class SeedAccessReviewFoldersCommandTests(TestCase):
    def setUp(self):
        self.plan = AccessReviewPlan.objects.create(
            name='Plano piloto',
            description='Plano para popular pastas reais.',
        )
        self.file_server = FileServer.objects.create(name='FS01')
        self.share = Share.objects.create(
            file_server=self.file_server,
            name='controlsul',
            unc_path='\\\\FS01\\controlsul',
        )
        self.root = Folder.objects.create(
            share=self.share,
            path='controlsul',
            parent_path='',
        )
        self.finance = Folder.objects.create(
            share=self.share,
            path='controlsul\\Financeiro',
            parent_path='controlsul',
        )
        self.dividendos = Folder.objects.create(
            share=self.share,
            path='controlsul\\Financeiro\\Dividendos',
            parent_path='controlsul\\Financeiro',
        )

    def call_seed(self, *args):
        output = StringIO()
        call_command(
            'seed_access_review_folders',
            '--plan-id',
            str(self.plan.id),
            *args,
            stdout=output,
        )
        return output.getvalue()

    def test_seed_dry_run_does_not_create_review_folders(self):
        output = self.call_seed('--dry-run')

        self.assertIn('DRY-RUN', output)
        self.assertEqual(AccessReviewFolder.objects.filter(plan=self.plan).count(), 0)

    def test_seed_creates_review_folders_from_current_folders(self):
        self.call_seed()

        self.assertEqual(AccessReviewFolder.objects.filter(plan=self.plan).count(), 3)
        review_folder = AccessReviewFolder.objects.get(plan=self.plan, current_folder=self.finance)
        self.assertEqual(review_folder.name, 'Financeiro')
        self.assertEqual(review_folder.proposed_path, 'controlsul\\Financeiro')
        self.assertEqual(review_folder.area_name, 'controlsul')

    def test_seed_preserves_parent_hierarchy(self):
        self.call_seed()

        root = AccessReviewFolder.objects.get(plan=self.plan, current_folder=self.root)
        finance = AccessReviewFolder.objects.get(plan=self.plan, current_folder=self.finance)
        dividendos = AccessReviewFolder.objects.get(plan=self.plan, current_folder=self.dividendos)
        self.assertIsNone(root.parent)
        self.assertEqual(finance.parent, root)
        self.assertEqual(dividendos.parent, finance)

    def test_seed_is_idempotent(self):
        self.call_seed()
        self.call_seed()

        self.assertEqual(AccessReviewFolder.objects.filter(plan=self.plan).count(), 3)

    def test_review_plan_view_shows_seeded_folder_structure(self):
        self.call_seed()

        response = self.client.get(reverse('access_inventory:review-plan-detail', args=[self.plan.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'controlsul\\Financeiro')
        self.assertNotContains(response, 'controlsul\\Financeiro\\Dividendos')
        self.assertContains(response, 'snapshot atual vinculado')

    def test_review_folder_view_shows_only_direct_children(self):
        self.call_seed()
        finance = AccessReviewFolder.objects.get(plan=self.plan, proposed_path='controlsul\\Financeiro')

        response = self.client.get(reverse('access_inventory:review-folder-detail', args=[self.plan.id, finance.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'controlsul\\Financeiro\\Dividendos')

    def test_review_folder_view_does_not_mix_sibling_branches(self):
        sibling = Folder.objects.create(
            share=self.share,
            path='controlsul\\Juridico',
            parent_path='controlsul',
        )
        Folder.objects.create(
            share=self.share,
            path='controlsul\\Juridico\\Contratos',
            parent_path='controlsul\\Juridico',
        )
        self.call_seed()
        finance = AccessReviewFolder.objects.get(plan=self.plan, proposed_path='controlsul\\Financeiro')

        response = self.client.get(reverse('access_inventory:review-folder-detail', args=[self.plan.id, finance.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'controlsul\\Financeiro\\Dividendos')
        self.assertNotContains(response, 'controlsul\\Juridico')
        self.assertNotContains(response, sibling.path)

    def test_seed_deduplicates_duplicate_logical_roots_and_keeps_canonical_tree(self):
        other_share = Share.objects.create(
            file_server=self.file_server,
            name='controlsul',
            unc_path='\\\\FS01\\controlsul-legacy',
        )
        duplicate_root = Folder.objects.create(
            share=other_share,
            path='controlsul',
            parent_path='',
        )

        output = self.call_seed('--share', 'controlsul')

        self.assertIn('Path duplicado "controlsul"', output)
        self.assertEqual(AccessReviewFolder.objects.filter(plan=self.plan, proposed_path='controlsul').count(), 1)
        review_root = AccessReviewFolder.objects.get(plan=self.plan, proposed_path='controlsul')
        self.assertEqual(review_root.current_folder, self.root)
        self.assertNotEqual(review_root.current_folder, duplicate_root)
        finance = AccessReviewFolder.objects.get(plan=self.plan, proposed_path='controlsul\\Financeiro')
        self.assertEqual(finance.parent, review_root)

    def test_seed_share_id_filters_exact_share(self):
        other_share = Share.objects.create(
            file_server=self.file_server,
            name='controlsul',
            unc_path='\\\\FS01\\controlsul-legacy',
        )
        Folder.objects.create(
            share=other_share,
            path='controlsul',
            parent_path='',
        )

        output = self.call_seed('--share-id', str(self.share.id), '--dry-run')

        self.assertIn('folders encontrados: 3', output)
        self.assertIn('dedup warnings: 0', output)


class ImportAccessReviewRulesCommandTests(TestCase):
    def setUp(self):
        self.plan = AccessReviewPlan.objects.create(name='Plano regras CSV')
        self.root = AccessReviewFolder.objects.create(
            plan=self.plan,
            area_name='controlsul',
            name='controlsul',
            proposed_path='controlsul',
        )
        self.area = AccessReviewFolder.objects.create(
            plan=self.plan,
            area_name='Administrativo',
            name='Administrativo',
            proposed_path='controlsul\\Administrativo',
            parent=self.root,
        )
        self.base = AccessReviewFolder.objects.create(
            plan=self.plan,
            area_name='Administrativo',
            name='FINANCEIRO',
            proposed_path='controlsul\\Administrativo\\FINANCEIRO',
            parent=self.area,
        )
        self.movimentacao = AccessReviewFolder.objects.create(
            plan=self.plan,
            area_name='Administrativo',
            name='MOVIMENTACAO BANCARIA',
            proposed_path='controlsul\\Administrativo\\FINANCEIRO\\MOVIMENTACAO BANCARIA',
            parent=self.base,
        )
        self.fatur = AccessReviewFolder.objects.create(
            plan=self.plan,
            area_name='Administrativo',
            name='FATUR',
            proposed_path='controlsul\\Administrativo\\FINANCEIRO\\FATUR',
            parent=self.base,
        )
        self.caixa = AccessReviewFolder.objects.create(
            plan=self.plan,
            area_name='Administrativo',
            name='CAIXA',
            proposed_path='controlsul\\Administrativo\\FINANCEIRO\\CAIXA',
            parent=self.base,
        )

    def rows(self):
        return [
            {
                'area': 'Administrativo',
                'pasta_base': 'FINANCEIRO',
                'subpasta': 'MOVIMENTAÇÃO BANCÁRIA',
                'escopo': 'exata',
                'principal_tipo': 'grupo',
                'principal_nome': 'GS_ADMINISTRATIVO_FIN',
                'permissao': 'RW',
                'acao': 'manter',
                'observacao': 'Grupo atualizado planilha',
            },
            {
                'area': 'Administrativo',
                'pasta_base': 'FINANCEIRO',
                'subpasta': 'demais',
                'escopo': 'demais_subpastas',
                'principal_tipo': 'usuario',
                'principal_nome': 'Roseli',
                'permissao': 'NONE',
                'acao': 'remover',
                'observacao': 'Tirar das demais pastas',
            },
        ]

    def test_import_review_rules_from_rows_creates_principals_and_rules(self):
        result = import_access_review_rules_from_rows(self.plan, self.rows())

        self.assertEqual(result.errors, [])
        self.assertEqual(result.principals_created, 2)
        self.assertEqual(AccessReviewPrincipal.objects.filter(plan=self.plan).count(), 2)
        group_rule = AccessReviewRule.objects.get(folder=self.movimentacao)
        self.assertEqual(group_rule.permission_level, AccessReviewRule.PERMISSION_RW)
        self.assertEqual(group_rule.source, AccessReviewRule.SOURCE_SPREADSHEET)

    def test_demais_subpastas_excludes_explicit_subfolders(self):
        import_access_review_rules_from_rows(self.plan, self.rows())

        roseli = AccessReviewPrincipal.objects.get(plan=self.plan, display_name='Roseli')
        folders = set(
            AccessReviewRule.objects.filter(principal=roseli).values_list('folder__name', flat=True)
        )
        self.assertEqual(folders, {'FATUR', 'CAIXA'})
        self.assertNotIn('MOVIMENTACAO BANCARIA', folders)

    def test_import_review_rules_is_idempotent(self):
        import_access_review_rules_from_rows(self.plan, self.rows())
        result = import_access_review_rules_from_rows(self.plan, self.rows())

        self.assertEqual(result.rules_created, 0)
        self.assertGreater(result.ignored, 0)
        self.assertEqual(AccessReviewRule.objects.filter(plan=self.plan).count(), 3)

    def test_import_review_rules_dry_run_does_not_write(self):
        result = import_access_review_rules_from_rows(self.plan, self.rows(), dry_run=True)

        self.assertEqual(result.rules_created, 3)
        self.assertEqual(AccessReviewRule.objects.filter(plan=self.plan).count(), 0)
        self.assertEqual(AccessReviewPrincipal.objects.filter(plan=self.plan).count(), 0)

    def test_import_review_rules_command_reads_csv(self):
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', newline='', suffix='.csv', delete=False) as handle:
            handle.write('area,pasta_base,subpasta,escopo,principal_tipo,principal_nome,permissao,acao,observacao\n')
            handle.write('Administrativo,FINANCEIRO,FATUR,exata,grupo,GS_ADMINISTRATIVO_FIN,RW,manter,Grupo atualizado planilha\n')
            csv_path = handle.name

        output = StringIO()
        call_command(
            'import_access_review_rules',
            '--plan-id',
            str(self.plan.id),
            '--file',
            csv_path,
            '--dry-run',
            stdout=output,
        )

        self.assertIn('DRY-RUN', output.getvalue())
        self.assertIn('regras criadas: 1', output.getvalue())
