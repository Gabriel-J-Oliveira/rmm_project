import json
from pathlib import Path

from django.test import TestCase
from django.urls import reverse

from .agent_auth import create_inventory_agent_with_token
from .models import (
    ADGroup,
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
from .services.access_review import explain_permission
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
            name='Reestruturação Administrativo e Jurídico - 2026',
            description='Proposta executiva de reorganização de acessos.',
            status=AccessReviewPlan.STATUS_DRAFT,
        )
        self.folder = AccessReviewFolder.objects.create(
            plan=self.plan,
            area_name='Administrativo',
            name='Financeiro',
            proposed_path='Administrativo\\Financeiro',
        )
        self.principal = AccessReviewPrincipal.objects.create(
            plan=self.plan,
            principal_type=AccessReviewPrincipal.PRINCIPAL_GROUP,
            display_name='GG Administrativo RW',
            proposed_group_name='GG_ADMINISTRATIVO_RW',
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
        response = self.client.get(reverse('access_inventory:review-plan-list'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.plan.name)
        self.assertContains(response, 'Abrir análise')

    def test_review_plan_detail_view(self):
        response = self.client.get(reverse('access_inventory:review-plan-detail', args=[self.plan.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Pastas planejadas')
        self.assertContains(response, self.folder.proposed_path)

    def test_review_folder_detail_view(self):
        response = self.client.get(reverse('access_inventory:review-folder-detail', args=[self.plan.id, self.folder.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.principal.display_name)
        self.assertContains(response, 'Pode abrir, criar, editar e excluir arquivos.')
