import json
from io import StringIO
from pathlib import Path

from django.core.management import call_command
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
        response = self.client.get(reverse('access_inventory:review-plan-list'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.plan.name)
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
