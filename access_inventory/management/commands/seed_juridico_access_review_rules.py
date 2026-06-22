from dataclasses import dataclass, field

from django.core.management.base import BaseCommand, CommandError

from access_inventory.models import ADUser, AccessReviewFolder, AccessReviewPlan, AccessReviewPrincipal, AccessReviewRule
from access_inventory.services.access_review import (
    is_displayable_review_user,
    is_inactive_review_user,
    is_technical_review_user,
    normalize_review_user_value,
)


SOURCE = 'juridico_rules'
SOURCE_ALIASES = ('juridico_rules', 'juridico_business_rules', 'juridico_business')
CUSTOM_LABEL = 'Leitura e gravação sem exclusão'
CUSTOM_EXPLANATION = 'Pode abrir, criar e editar arquivos, mas não deve excluir arquivos ou subpastas.'
RO_LABEL = 'Somente leitura'
RO_EXPLANATION = 'Pode abrir, listar e visualizar arquivos. Não pode criar, editar nem excluir.'
RW_LABEL = 'Leitura e escrita'
RW_EXPLANATION = 'Pode abrir, criar, editar e excluir arquivos.'
FULL_LABEL = 'Controle total'
FULL_EXPLANATION = 'Pode administrar permissões e alterar tudo.'

PRAZOS_USERS = [
    ('Denise Monteiro de Oliveira', 'denise.oliveira'),
    ('Gabriela Bonin', 'gabriela.bonin'),
    ('Larissa Zampiva Florencio Odin', 'larissa.odin'),
    ('Marcia dos Santos Silva', 'marcia.silva'),
]

SICOOB_SICREDI_USERS = [
    ('Alessandra Rabel da Fonseca', 'alessandra.rabel'),
    ('Alexsandra Caetano Kozman', 'alexsandra.kozman'),
    ('Anderson Roberto Favero', 'anderson.favero'),
    ('Anna Carolina Akemi Amanuma', 'anna.amamuma'),
    ('Bruno Alexander de Souza', 'bruno.souza'),
    ('Carlos Henrique Macedo', 'carlos.macedo'),
    ('Denise Monteiro de Oliveira', 'denise.oliveira'),
    ('Francieli Marquardt Sganderla', 'francieli.sganderla'),
    ('Gabriel Vinicius Guarda', 'gabriel.guarda'),
    ('Gabriela Bonin', 'gabriela.bonin'),
    ('Gabriella Mendes Passos', 'gabriella.passos'),
    ('Isabela Olivotto', 'isabela.olivotto'),
    ('Isabelly Pereira Sganzerla', 'isabelly.sganzerla'),
    ('Jesse Marques dos Santos', 'jesse.santos'),
    ('Joao Paulo Martins Joaquim', 'joao.martins'),
    ('Julia Batista', 'julia.batista'),
    ('Kelly Cristine Padovim', 'kelly.padovim'),
    ('Larissa Zampiva Florencio Odin', 'larissa.odin'),
    ('Leticia da Cruz Pereira', 'leticia.pereira'),
    ('Leticia Rodrigues Daros', 'leticia.daros'),
    ('Marcia dos Santos Silva', 'marcia.silva'),
    ('Millena Pereira Rahmeier', 'millena.rahmeier'),
    ('Recepcao Cascavel', 'recepcao cascavel'),
]

FOLDER_USER_RULES = [
    (
        'controlsul\\Juridico\\PRE',
        [('Alexandre Paetzhold Bracht', 'alexandre.bracht'), ('Laercio Lisboa', 'laercio.lisboa')],
        AccessReviewRule.PERMISSION_RW,
        'Acesso proposto para Juridico\\PRE.',
    ),
    (
        'controlsul\\Juridico\\OPER',
        [('Ana Silva', 'ana.silva'), ('Fabiana Lorenzetti', 'fabiana.lorenzetti'), ('Kelly Cristine Padovim', 'kelly.padovim')],
        AccessReviewRule.PERMISSION_RW,
        'Acesso proposto para Juridico\\OPER.',
    ),
    (
        'controlsul\\Juridico\\TRAB',
        [
            ('Bruna Oliveira', 'bruna.oliveira'),
            ('Leovanir Lisboa', 'leovanir.lisboa'),
            ('Marcelle Prado', 'marcelle.prado'),
            ('Sabrina Soares', 'sabrina.soares'),
        ],
        AccessReviewRule.PERMISSION_RW,
        'Acesso proposto para Juridico\\TRAB.',
    ),
    (
        'controlsul\\Juridico\\TRIB',
        [
            ('Bruna Oliveira', 'bruna.oliveira'),
            ('Leovanir Lisboa', 'leovanir.lisboa'),
            ('Marcelle Prado', 'marcelle.prado'),
            ('Sabrina Soares', 'sabrina.soares'),
        ],
        AccessReviewRule.PERMISSION_RW,
        'Acesso proposto para Juridico\\TRIB.',
    ),
    (
        'controlsul\\Juridico\\PRAZOS',
        [
            ('Alexandre Paetzhold Bracht', 'alexandre.bracht'),
            ('Ana Carolina Weiler Silva', 'ana.weiler'),
            ('Bianca Mizael Popowicz Genguini', 'bianca.genguini'),
            ('Denise Monteiro de Oliveira', 'denise.oliveira'),
            ('Fabiana China Lorenzetti Pacagnan', 'fabiana.lorenzetti'),
            ('Gabriela Bonin', 'gabriela.bonin'),
            ('Kelly Cristine Padovim', 'kelly.padovim'),
            ('Larissa Zampiva Florencio Odin', 'larissa.odin'),
            ('Marcia dos Santos Silva', 'marcia.silva'),
        ],
        AccessReviewRule.PERMISSION_CUSTOM,
        'Acesso proposto para Juridico\\PRAZOS sem permissão de exclusão.',
    ),
    (
        'controlsul\\Juridico\\CIVEL',
        [('Alexandre Paetzhold Bracht', 'alexandre.bracht'), ('Laercio Losso Lisboa', 'laercio.lisboa')],
        AccessReviewRule.PERMISSION_RW,
        'Acesso proposto para Juridico\\CIVEL.',
    ),
]


@dataclass
class SeedResult:
    dry_run: bool
    target_folders: list = field(default_factory=list)
    missing_folders: list = field(default_factory=list)
    users_found: set = field(default_factory=set)
    users_not_found: list = field(default_factory=list)
    users_ambiguous: list = field(default_factory=list)
    users_ignored: list = field(default_factory=list)
    observations: list = field(default_factory=list)
    validation_warnings: list = field(default_factory=list)
    rules_created: int = 0
    rules_updated: int = 0
    rules_would_create: int = 0
    rules_would_update: int = 0
    rules_ignored: int = 0
    rules_deleted: int = 0


def normalize_folder_path(value):
    value = '' if value is None else str(value)
    value = value.replace('/', '\\').replace(';', '\\')
    parts = [
        ' '.join(part.strip().split())
        for part in value.split('\\')
        if part.strip()
    ]
    return normalize_review_user_value('\\'.join(parts))


def user_identity_values(user):
    values = [
        user.sam_account_name,
        user.display_name,
        user.email,
        user.user_principal_name,
    ]
    return {normalize_review_user_value(value) for value in values if value}


def permission_payload(permission_level):
    if permission_level == AccessReviewRule.PERMISSION_RO:
        return {
            'permission_level': AccessReviewRule.PERMISSION_RO,
            'permission_label': RO_LABEL,
            'permission_explanation': RO_EXPLANATION,
        }
    if permission_level == AccessReviewRule.PERMISSION_CUSTOM:
        return {
            'permission_level': AccessReviewRule.PERMISSION_CUSTOM,
            'permission_label': CUSTOM_LABEL,
            'permission_explanation': CUSTOM_EXPLANATION,
        }
    if permission_level == AccessReviewRule.PERMISSION_RW:
        return {
            'permission_level': AccessReviewRule.PERMISSION_RW,
            'permission_label': RW_LABEL,
            'permission_explanation': RW_EXPLANATION,
        }
    if permission_level == AccessReviewRule.PERMISSION_FULL:
        return {
            'permission_level': AccessReviewRule.PERMISSION_FULL,
            'permission_label': FULL_LABEL,
            'permission_explanation': FULL_EXPLANATION,
        }
    return {
        'permission_level': permission_level,
        'permission_label': dict(AccessReviewRule.PERMISSION_LEVEL_CHOICES).get(permission_level, permission_level),
        'permission_explanation': '',
    }


class Command(BaseCommand):
    help = 'Cria regras propostas de negocio do ramo Juridico no AccessReviewPlan.'

    PERMISSION_MAP = {
        'RO': AccessReviewRule.PERMISSION_RO,
        'RW': AccessReviewRule.PERMISSION_RW,
        'FULL': AccessReviewRule.PERMISSION_FULL,
        'CUSTOM': AccessReviewRule.PERMISSION_CUSTOM,
    }

    def add_arguments(self, parser):
        parser.add_argument('--plan-id', type=int, required=True)
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--clear-existing', action='store_true')
        parser.add_argument(
            '--kelly-localizador-permission',
            default='RW',
            choices=sorted(self.PERMISSION_MAP.keys()),
        )

    def handle(self, *args, **options):
        try:
            plan = AccessReviewPlan.objects.get(pk=options['plan_id'])
        except AccessReviewPlan.DoesNotExist as exc:
            raise CommandError(f'AccessReviewPlan nao encontrado: {options["plan_id"]}') from exc

        result = SeedResult(dry_run=options['dry_run'])
        folders = {normalize_folder_path(folder.proposed_path): folder for folder in plan.folders.all()}
        juridico = folders.get(normalize_folder_path('controlsul\\Juridico'))
        if not juridico:
            raise CommandError('Pasta controlsul\\Juridico nao encontrada no plano.')

        scoped_folders = self.collect_descendants(juridico)
        if options['clear_existing']:
            existing = AccessReviewRule.objects.filter(plan=plan, folder__in=scoped_folders, source__in=SOURCE_ALIASES)
            result.rules_deleted = existing.count()
            if not options['dry_run']:
                existing.delete()

        self.add_prazos_rules(plan, folders, result)
        self.add_sicoob_sicredi_rules(plan, folders, result)
        self.add_kelly_localizador_rules(plan, folders, result, self.PERMISSION_MAP[options['kelly_localizador_permission']])
        self.add_adm_ou_rules(plan, folders, result)
        self.add_specific_folder_rules(plan, folders, result)
        self.add_penal_observation(folders, result)

        self.print_summary(plan, result)

    def collect_descendants(self, root):
        folders = [root]
        children = list(root.children.filter(plan=root.plan))
        for child in children:
            folders.extend(self.collect_descendants(child))
        return folders

    def folder(self, folders, path, result, required=False):
        folder = folders.get(normalize_folder_path(path))
        if folder:
            result.target_folders.append(folder.proposed_path)
            return folder
        result.missing_folders.append(path)
        if required:
            raise CommandError(f'Pasta obrigatoria nao encontrada: {path}')
        return None

    def add_prazos_rules(self, plan, folders, result):
        folder = self.folder(folders, 'controlsul\\Juridico', result, required=True)
        for name, sam in PRAZOS_USERS:
            user = self.resolve_user(name, sam, result)
            if user:
                self.upsert_rule(
                    plan,
                    folder,
                    user,
                    AccessReviewRule.PERMISSION_CUSTOM,
                    'Usuário do setor PRAZOS com acesso herdado a todo o ramo Jurídico, sem permissão de exclusão.',
                    result,
                )

    def add_sicoob_sicredi_rules(self, plan, folders, result):
        for folder_path in ('controlsul\\Juridico\\SICOOB', 'controlsul\\Juridico\\SICREDI'):
            folder = self.folder(folders, folder_path, result)
            if not folder:
                continue
            for name, sam in SICOOB_SICREDI_USERS:
                user = self.resolve_user(name, sam, result)
                if user:
                    self.upsert_rule(
                        plan,
                        folder,
                        user,
                    AccessReviewRule.PERMISSION_RW,
                        'Usuário listado para acesso ao escopo SICOOB/SICREDI do Jurídico.',
                        result,
                    )

    def add_kelly_localizador_rules(self, plan, folders, result, permission_level):
        for folder_path in (
            'controlsul\\Juridico\\SICOOB\\Localizador',
            'controlsul\\Juridico\\SICREDI\\Localizador',
        ):
            folder = self.folder(folders, folder_path, result)
            if not folder:
                continue
            user = self.resolve_user('Kelly Cristine Padovim', 'kelly.padovim', result)
            if user:
                self.upsert_rule(
                    plan,
                    folder,
                    user,
                    permission_level,
                    'Acesso específico da Kelly aos Localizadores SICOOB/SICREDI.',
                    result,
                )

    def add_adm_ou_rules(self, plan, folders, result):
        folder = self.folder(folders, 'controlsul\\Juridico\\ADM', result)
        if not folder:
            return
        for user in ADUser.objects.select_related('ou').filter(enabled=True).order_by('display_name', 'sam_account_name'):
            if not self.is_blazius_lorenzetti_user(user):
                continue
            if not is_displayable_review_user(user):
                result.users_ignored.append(user.display_name or user.sam_account_name)
                continue
            result.users_found.add(user.sam_account_name or user.display_name)
            self.upsert_rule(
                plan,
                folder,
                user,
                AccessReviewRule.PERMISSION_CUSTOM,
                'Usuário das OUs Blazius/Lorenzetti com acesso ao ADM Jurídico sem exclusão.',
                result,
            )

    def add_specific_folder_rules(self, plan, folders, result):
        for folder_path, users, permission_level, notes in FOLDER_USER_RULES:
            folder = self.folder(folders, folder_path, result)
            if not folder:
                continue
            for name, sam in users:
                user = self.resolve_user(name, sam, result)
                if user:
                    self.upsert_rule(plan, folder, user, permission_level, notes, result)

    def add_penal_observation(self, folders, result):
        folder = self.folder(folders, 'controlsul\\Juridico\\PENAL', result)
        if folder:
            result.observations.append('PENAL: apenas socios; nenhuma regra individual criada.')

    def is_blazius_lorenzetti_user(self, user):
        values = [user.distinguished_name]
        if user.ou_id:
            values.extend([user.ou.name, user.ou.distinguished_name, user.ou.parent_distinguished_name])
        normalized_values = ' '.join(normalize_review_user_value(value) for value in values if value)
        return 'blazius' in normalized_values or 'lorenzetti' in normalized_values

    def resolve_user(self, display_name, sam_account_name, result):
        sam = normalize_review_user_value(sam_account_name)
        display = normalize_review_user_value(display_name)
        users = list(ADUser.objects.all().order_by('display_name', 'sam_account_name', 'id'))
        matches = [user for user in users if normalize_review_user_value(user.sam_account_name) == sam]
        if not matches:
            matches = [user for user in users if normalize_review_user_value(user.user_principal_name) == sam]
        if not matches:
            matches = [user for user in users if normalize_review_user_value(user.email) == sam]
        if not matches:
            matches = [user for user in users if normalize_review_user_value(user.display_name) == display]
        if not matches and display:
            matches = [
                user for user in users
                if display in normalize_review_user_value(user.display_name)
            ]

        if not matches:
            result.users_not_found.append(f'{display_name} / {sam_account_name}')
            return None
        if len(matches) > 1:
            result.users_ambiguous.append(f'{display_name} / {sam_account_name}')
            return None
        user = matches[0]
        if is_inactive_review_user(user) or is_technical_review_user(user):
            result.users_ignored.append(user.display_name or user.sam_account_name)
            return None
        result.users_found.add(user.sam_account_name or user.display_name)
        return user

    def upsert_rule(self, plan, folder, user, permission_level, notes, result):
        principal_lookup = {
            'plan': plan,
            'principal_type': AccessReviewPrincipal.PRINCIPAL_USER,
            'ad_user': user,
        }
        principal_defaults = {
            'display_name': user.display_name or user.sam_account_name,
            'sam_account_name': user.sam_account_name,
            'proposed_group_name': '',
            'ad_user': user,
            'ad_group': None,
        }
        payload = {
            **permission_payload(permission_level),
            'source': SOURCE,
            'notes': notes,
        }
        self.validate_payload_lengths(payload, result)

        principal = AccessReviewPrincipal.objects.filter(**principal_lookup).first()
        rule = None
        if principal:
            rule = AccessReviewRule.objects.filter(plan=plan, folder=folder, principal=principal).first()
        if rule and rule.source not in SOURCE_ALIASES:
            result.rules_ignored += 1
            return

        if result.dry_run:
            if rule:
                result.rules_would_update += 1
            else:
                result.rules_would_create += 1
            return

        principal, _ = AccessReviewPrincipal.objects.update_or_create(
            **principal_lookup,
            defaults=principal_defaults,
        )
        rule, created = AccessReviewRule.objects.update_or_create(
            plan=plan,
            folder=folder,
            principal=principal,
            defaults=payload,
        )
        if created:
            result.rules_created += 1
        else:
            result.rules_updated += 1

    def print_summary(self, plan, result):
        mode = 'DRY-RUN' if result.dry_run else 'EXECUCAO'
        self.stdout.write(self.style.SUCCESS(f'Carga de regras do Juridico concluida ({mode}).'))
        self.stdout.write(f'Plano: {plan.id}')
        self.stdout.write(f'Source usado: {SOURCE}')
        self.stdout.write(f'Pastas alvo encontradas: {len(set(result.target_folders))}')
        for path in sorted(set(result.target_folders)):
            self.stdout.write(f'- {path}')
        if result.missing_folders:
            self.stdout.write('Pastas nao encontradas:')
            for path in sorted(set(result.missing_folders)):
                self.stdout.write(f'- {path}')
        self.stdout.write(f'Usuarios localizados: {len(result.users_found)}')
        if result.users_not_found:
            self.stdout.write('Usuarios nao encontrados:')
            for user in sorted(set(result.users_not_found)):
                self.stdout.write(f'- {user}')
        if result.users_ambiguous:
            self.stdout.write('Usuarios ambiguos:')
            for user in sorted(set(result.users_ambiguous)):
                self.stdout.write(f'- {user}')
        if result.users_ignored:
            self.stdout.write('Usuarios ignorados:')
            for user in sorted(set(result.users_ignored)):
                self.stdout.write(f'- {user}')
        if result.observations:
            self.stdout.write('Observacoes:')
            for observation in result.observations:
                self.stdout.write(f'- {observation}')
        if result.dry_run:
            self.stdout.write(f'Regras que seriam criadas: {result.rules_would_create}')
            self.stdout.write(f'Regras que seriam atualizadas: {result.rules_would_update}')
            self.stdout.write(f'Regras que seriam removidas por --clear-existing: {result.rules_deleted}')
        else:
            self.stdout.write(f'Regras criadas: {result.rules_created}')
            self.stdout.write(f'Regras atualizadas: {result.rules_updated}')
            self.stdout.write(f'Regras removidas por --clear-existing: {result.rules_deleted}')
        self.stdout.write(f'Regras ignoradas: {result.rules_ignored}')

        if result.validation_warnings:
            self.stdout.write('Avisos de validacao:')
            for warning in sorted(set(result.validation_warnings)):
                self.stdout.write(f'- {warning}')

    def validate_payload_lengths(self, payload, result):
        for field_name in ('permission_level', 'permission_label', 'source'):
            field = AccessReviewRule._meta.get_field(field_name)
            max_length = getattr(field, 'max_length', None)
            value = payload.get(field_name) or ''
            if max_length and len(value) > max_length:
                result.validation_warnings.append(
                    f'{field_name} excede max_length={max_length}: {value}'
                )
                if not result.dry_run:
                    raise CommandError(f'{field_name} excede max_length={max_length}: {value}')
