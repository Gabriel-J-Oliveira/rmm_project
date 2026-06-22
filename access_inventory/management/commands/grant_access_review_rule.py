from django.core.management.base import BaseCommand, CommandError

from access_inventory.models import ADGroup, ADUser, AccessReviewFolder, AccessReviewPrincipal, AccessReviewRule
from access_inventory.services.access_review import explain_permission, normalize_review_user_value, permission_level_label


def normalize_folder_path(value):
    value = '' if value is None else str(value)
    value = value.replace('/', '\\').replace(';', '\\')
    parts = [
        ' '.join(part.strip().split())
        for part in value.split('\\')
        if part.strip()
    ]
    return normalize_review_user_value('\\'.join(parts))


def find_unique(queryset, predicate, label, value):
    matches = [item for item in queryset if predicate(item)]
    if not matches:
        raise CommandError(f'{label} nao encontrado: {value}')
    if len(matches) > 1:
        found = ', '.join(str(item) for item in matches[:5])
        raise CommandError(f'{label} ambiguo para "{value}". Encontrados: {found}')
    return matches[0]


class Command(BaseCommand):
    help = 'Cria ou atualiza uma regra proposta de acesso em uma pasta do plano.'

    PERMISSION_MAP = {
        'RO': AccessReviewRule.PERMISSION_RO,
        'RW': AccessReviewRule.PERMISSION_RW,
        'FULL': AccessReviewRule.PERMISSION_FULL,
        'CUSTOM': AccessReviewRule.PERMISSION_CUSTOM,
        'NONE': AccessReviewRule.PERMISSION_NONE,
    }

    def add_arguments(self, parser):
        parser.add_argument('--plan-id', type=int, required=True)
        parser.add_argument('--folder-path', required=True)
        principal_group = parser.add_mutually_exclusive_group(required=True)
        principal_group.add_argument('--user')
        principal_group.add_argument('--group')
        parser.add_argument('--permission', required=True, choices=sorted(self.PERMISSION_MAP.keys()))
        parser.add_argument('--source', default=AccessReviewRule.SOURCE_MANUAL)
        parser.add_argument('--notes', default='')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        plan_id = options['plan_id']
        folder = self.find_folder(plan_id, options['folder_path'])
        permission_level = self.PERMISSION_MAP[options['permission'].upper()]

        if options.get('user'):
            principal_defaults, lookup = self.resolve_user_principal(plan_id, options['user'])
        else:
            principal_defaults, lookup = self.resolve_group_principal(plan_id, options['group'])

        if options['dry_run']:
            self.stdout.write('DRY-RUN: nenhuma alteracao sera gravada.')
            self.stdout.write(f'Plano: {plan_id}')
            self.stdout.write(f'Pasta: {folder.proposed_path}')
            self.stdout.write(f'Principal: {principal_defaults["display_name"]}')
            self.stdout.write(f'Permissao: {permission_level_label(permission_level)} ({permission_level})')
            self.stdout.write(f'Source: {options["source"]}')
            if options['notes']:
                self.stdout.write(f'Notes: {options["notes"]}')
            return

        principal, principal_created = AccessReviewPrincipal.objects.update_or_create(
            **lookup,
            defaults=principal_defaults,
        )
        rule, rule_created = AccessReviewRule.objects.update_or_create(
            plan_id=plan_id,
            folder=folder,
            principal=principal,
            defaults={
                'permission_level': permission_level,
                'permission_label': permission_level_label(permission_level),
                'permission_explanation': explain_permission(permission_level),
                'source': options['source'],
                'notes': options['notes'],
            },
        )

        principal_status = 'criado' if principal_created else 'atualizado'
        rule_status = 'criada' if rule_created else 'atualizada'
        self.stdout.write(self.style.SUCCESS(
            f'Regra {rule_status}: {principal.display_name} -> {folder.proposed_path} ({rule.permission_level})'
        ))
        self.stdout.write(f'Principal {principal_status}: {principal.display_name}')

    def find_folder(self, plan_id, folder_path):
        requested = normalize_folder_path(folder_path)
        folders = AccessReviewFolder.objects.filter(plan_id=plan_id).select_related('plan')
        matches = [
            folder for folder in folders
            if normalize_folder_path(folder.proposed_path) == requested
        ]
        if not matches:
            raise CommandError(f'Pasta do plano nao encontrada: {folder_path}')
        if len(matches) > 1:
            found = ', '.join(f'id={folder.id} {folder.proposed_path}' for folder in matches[:5])
            raise CommandError(f'Pasta ambigua para "{folder_path}". Encontradas: {found}')
        return matches[0]

    def resolve_user_principal(self, plan_id, value):
        normalized = normalize_review_user_value(value)

        def matches(user):
            values = [
                user.sam_account_name,
                user.display_name,
                user.user_principal_name,
                user.email,
            ]
            return any(normalize_review_user_value(item) == normalized for item in values if item)

        user = find_unique(ADUser.objects.all().order_by('display_name', 'sam_account_name', 'id'), matches, 'Usuario AD', value)
        lookup = {
            'plan_id': plan_id,
            'principal_type': AccessReviewPrincipal.PRINCIPAL_USER,
            'ad_user': user,
        }
        defaults = {
            'display_name': user.display_name or user.sam_account_name,
            'sam_account_name': user.sam_account_name,
            'proposed_group_name': '',
            'ad_user': user,
            'ad_group': None,
        }
        return defaults, lookup

    def resolve_group_principal(self, plan_id, value):
        normalized = normalize_review_user_value(value)

        def matches(group):
            values = [
                group.name,
                group.sam_account_name,
            ]
            return any(normalize_review_user_value(item) == normalized for item in values if item)

        group = find_unique(ADGroup.objects.all().order_by('name', 'sam_account_name', 'id'), matches, 'Grupo AD', value)
        group_name = group.name or group.sam_account_name
        lookup = {
            'plan_id': plan_id,
            'principal_type': AccessReviewPrincipal.PRINCIPAL_GROUP,
            'ad_group': group,
        }
        defaults = {
            'display_name': group_name,
            'sam_account_name': group.sam_account_name,
            'proposed_group_name': group_name,
            'ad_user': None,
            'ad_group': group,
        }
        return defaults, lookup
