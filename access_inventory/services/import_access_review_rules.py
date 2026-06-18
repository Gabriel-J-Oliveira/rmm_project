import csv
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from django.db import transaction

from access_inventory.models import (
    ADGroup,
    ADUser,
    AccessReviewFolder,
    AccessReviewPlan,
    AccessReviewPrincipal,
    AccessReviewRule,
)
from access_inventory.services.access_review import explain_permission


REQUIRED_COLUMNS = {
    'area',
    'pasta_base',
    'subpasta',
    'escopo',
    'principal_tipo',
    'principal_nome',
    'permissao',
    'acao',
    'observacao',
}

PERMISSION_MAP = {
    'none': AccessReviewRule.PERMISSION_NONE,
    'no': AccessReviewRule.PERMISSION_NONE,
    'sem acesso': AccessReviewRule.PERMISSION_NONE,
    'ro': AccessReviewRule.PERMISSION_RO,
    'read': AccessReviewRule.PERMISSION_RO,
    'somente leitura': AccessReviewRule.PERMISSION_RO,
    'rw': AccessReviewRule.PERMISSION_RW,
    'write': AccessReviewRule.PERMISSION_RW,
    'leitura e escrita': AccessReviewRule.PERMISSION_RW,
    'full': AccessReviewRule.PERMISSION_FULL,
    'fullcontrol': AccessReviewRule.PERMISSION_FULL,
    'controle total': AccessReviewRule.PERMISSION_FULL,
    'custom': AccessReviewRule.PERMISSION_CUSTOM,
    'personalizada': AccessReviewRule.PERMISSION_CUSTOM,
}

PRINCIPAL_TYPE_MAP = {
    'usuario': AccessReviewPrincipal.PRINCIPAL_USER,
    'user': AccessReviewPrincipal.PRINCIPAL_USER,
    'grupo': AccessReviewPrincipal.PRINCIPAL_GROUP,
    'group': AccessReviewPrincipal.PRINCIPAL_GROUP,
}


@dataclass
class ImportAccessReviewRulesResult:
    plan: AccessReviewPlan
    dry_run: bool = False
    rows_read: int = 0
    principals_created: int = 0
    principals_updated: int = 0
    rules_created: int = 0
    rules_updated: int = 0
    ignored: int = 0
    errors: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    resolution_messages: list[str] = field(default_factory=list)


def normalize_key(value):
    value = '' if value is None else str(value)
    value = unicodedata.normalize('NFKD', value)
    value = ''.join(char for char in value if not unicodedata.combining(char))
    value = re.sub(r'\s+', ' ', value).strip().lower()
    return value


def normalize_path_key(value):
    return normalize_key(str(value).replace('/', '\\')).replace(' ', '')


def read_csv_rows(csv_path):
    path = Path(csv_path)
    with path.open('r', encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        headers = set(reader.fieldnames or [])
        missing = sorted(REQUIRED_COLUMNS - headers)
        if missing:
            raise ValueError(f'CSV sem colunas obrigatorias: {", ".join(missing)}')
        return [
            {key: (value or '').strip() for key, value in row.items()}
            for row in reader
        ]


def normalize_permission(value):
    key = normalize_key(value)
    permission = PERMISSION_MAP.get(key)
    if not permission:
        raise ValueError(f'Permissao invalida: {value}')
    return permission


def normalize_principal_type(value):
    key = normalize_key(value)
    principal_type = PRINCIPAL_TYPE_MAP.get(key)
    if not principal_type:
        raise ValueError(f'Tipo de principal invalido: {value}')
    return principal_type


def build_folder_indexes(plan):
    folders = list(
        AccessReviewFolder.objects.filter(plan=plan)
        .select_related('parent', 'current_folder')
        .order_by('sort_order', 'proposed_path', 'id')
    )
    children_by_parent = {}
    for folder in folders:
        children_by_parent.setdefault(folder.parent_id, []).append(folder)
    return folders, children_by_parent


def descendants_of(folder, children_by_parent):
    stack = list(children_by_parent.get(folder.id, []))
    descendants = []
    while stack:
        child = stack.pop(0)
        descendants.append(child)
        stack[0:0] = list(children_by_parent.get(child.id, []))
    return descendants


def _folder_sort_key(folder):
    return (folder.sort_order, normalize_path_key(folder.proposed_path), folder.id)


def _unique_folder(candidates, label):
    candidates = sorted(candidates, key=_folder_sort_key)
    if not candidates:
        raise ValueError(f'Pasta nao encontrada: {label}')
    return candidates[0]


def find_area_folder(area, folders):
    area_key = normalize_key(area)
    path_suffix = '\\' + normalize_path_key(area)
    candidates = [
        folder for folder in folders
        if normalize_key(folder.name) == area_key
        or normalize_path_key(folder.proposed_path).endswith(path_suffix)
        or normalize_path_key(folder.proposed_path) == normalize_path_key(area)
    ]
    return _unique_folder(candidates, area)


def find_child_by_name(parent, name, children_by_parent, fallback_descendants=True):
    name_key = normalize_key(name)
    direct = [
        folder for folder in children_by_parent.get(parent.id, [])
        if normalize_key(folder.name) == name_key
    ]
    if direct:
        return _unique_folder(direct, name)
    if not fallback_descendants:
        raise ValueError(f'Pasta "{name}" nao encontrada abaixo de "{parent.proposed_path}"')
    descendants = [
        folder for folder in descendants_of(parent, children_by_parent)
        if normalize_key(folder.name) == name_key
    ]
    if descendants:
        return _unique_folder(descendants, name)
    raise ValueError(f'Pasta "{name}" nao encontrada abaixo de "{parent.proposed_path}"')


def explicit_subfolders_by_base(rows):
    explicit = {}
    for row in rows:
        if normalize_key(row.get('escopo')) != 'exata':
            continue
        subfolder = row.get('subpasta', '')
        if not subfolder or normalize_key(subfolder) == 'demais':
            continue
        key = (normalize_key(row.get('area')), normalize_key(row.get('pasta_base')))
        explicit.setdefault(key, set()).add(normalize_key(subfolder))
    return explicit


def resolve_target_folders(row, folders, children_by_parent, explicit_by_base):
    area_folder = find_area_folder(row.get('area'), folders)
    base_folder = find_child_by_name(area_folder, row.get('pasta_base'), children_by_parent)
    scope = normalize_key(row.get('escopo'))
    subfolder = row.get('subpasta', '')

    if scope == 'exata':
        if not subfolder or normalize_key(subfolder) in {'demais', normalize_key(base_folder.name)}:
            return [base_folder]
        return [find_child_by_name(base_folder, subfolder, children_by_parent)]

    if scope == 'demais_subpastas':
        key = (normalize_key(row.get('area')), normalize_key(row.get('pasta_base')))
        explicit = explicit_by_base.get(key, set())
        return [
            child for child in children_by_parent.get(base_folder.id, [])
            if normalize_key(child.name) not in explicit
        ]

    raise ValueError(f'Escopo invalido: {row.get("escopo")}')


def _candidate_label(user):
    label = user.display_name or user.sam_account_name
    if user.sam_account_name and user.sam_account_name != label:
        label = f'{label} ({user.sam_account_name})'
    return label


def resolve_ad_user(name):
    query = normalize_key(name)
    users = list(ADUser.objects.all().order_by('display_name', 'sam_account_name', 'id'))

    exact_matches = []
    for user in users:
        values = [
            user.sam_account_name,
            user.display_name,
            user.user_principal_name,
            user.email,
        ]
        if any(normalize_key(value) == query for value in values if value):
            exact_matches.append(user)

    if len(exact_matches) == 1:
        return {'status': 'resolved', 'user': exact_matches[0], 'candidates': []}
    if len(exact_matches) > 1:
        return {'status': 'ambiguous', 'user': None, 'candidates': exact_matches[:5]}

    startswith_matches = [
        user for user in users
        if normalize_key(user.display_name).startswith(query)
    ]
    if len(startswith_matches) == 1:
        return {'status': 'resolved', 'user': startswith_matches[0], 'candidates': []}
    if len(startswith_matches) > 1:
        return {'status': 'ambiguous', 'user': None, 'candidates': startswith_matches[:5]}

    contains_matches = [
        user for user in users
        if query and query in normalize_key(user.display_name)
    ]
    if len(contains_matches) == 1:
        return {'status': 'resolved', 'user': contains_matches[0], 'candidates': []}
    if len(contains_matches) > 1:
        return {'status': 'ambiguous', 'user': None, 'candidates': contains_matches[:5]}

    return {'status': 'not_found', 'user': None, 'candidates': []}


def find_ad_group(name):
    name_key = normalize_key(name)
    groups = [
        group for group in ADGroup.objects.all().order_by('name', 'sam_account_name', 'id')
        if normalize_key(group.name) == name_key
        or normalize_key(group.sam_account_name) == name_key
    ]
    return groups[0] if len(groups) == 1 else None


def _merge_notes(*parts):
    return '; '.join(part for part in parts if part)


def build_rule_notes(row):
    return _merge_notes(
        f'acao={row.get("acao")}' if row.get('acao') else '',
        row.get('observacao', ''),
    )


def _principal_notes(row, resolution):
    if not resolution:
        return row.get('observacao', '')

    status = resolution['status']
    original = row.get('principal_nome', '')
    candidates = ', '.join(_candidate_label(user) for user in resolution.get('candidates', []))
    details = [f'user_resolution={status}', f'original={original}']
    if candidates:
        details.append(f'candidates={candidates}')
    return _merge_notes(row.get('observacao', ''), '; '.join(details))


def _find_existing_principal(plan, principal_type, display_name, ad_user=None, ad_group=None):
    queryset = AccessReviewPrincipal.objects.filter(plan=plan, principal_type=principal_type)
    if ad_user:
        existing = queryset.filter(ad_user=ad_user).first()
        if existing:
            return existing
    if ad_group:
        existing = queryset.filter(ad_group=ad_group).first()
        if existing:
            return existing

    display_key = normalize_key(display_name)
    for principal in queryset:
        if normalize_key(principal.display_name) == display_key:
            return principal
    return None


def get_or_prepare_principal(plan, row, result):
    principal_type = normalize_principal_type(row.get('principal_tipo'))
    original_name = row.get('principal_nome', '')
    if not original_name:
        raise ValueError('principal_nome vazio')

    display_name = original_name
    sam_account_name = ''
    proposed_group_name = ''
    ad_user = None
    ad_group = None
    resolution = None

    if principal_type == AccessReviewPrincipal.PRINCIPAL_USER:
        resolution = resolve_ad_user(original_name)
        ad_user = resolution['user']
        if ad_user:
            display_name = ad_user.display_name or ad_user.sam_account_name or original_name
            sam_account_name = ad_user.sam_account_name
        candidates = ', '.join(_candidate_label(user) for user in resolution.get('candidates', []))
        message = f'usuario "{original_name}": {resolution["status"]}'
        if ad_user:
            message += f' -> {display_name}'
        if candidates:
            message += f' ({candidates})'
        result.resolution_messages.append(message)
    else:
        ad_group = find_ad_group(original_name)
        if ad_group:
            display_name = ad_group.name or ad_group.sam_account_name or original_name
            sam_account_name = ad_group.sam_account_name
        else:
            proposed_group_name = original_name

    existing = _find_existing_principal(
        plan,
        principal_type,
        display_name,
        ad_user=ad_user,
        ad_group=ad_group,
    )
    notes = _principal_notes(row, resolution)

    defaults = {
        'display_name': display_name,
        'sam_account_name': sam_account_name,
        'proposed_group_name': proposed_group_name,
        'ad_user': ad_user,
        'ad_group': ad_group,
        'notes': notes,
    }

    if existing:
        changed = any(getattr(existing, key) != value for key, value in defaults.items())
        if changed:
            if not result.dry_run:
                for key, value in defaults.items():
                    setattr(existing, key, value)
                existing.save()
            result.principals_updated += 1
        return existing

    if result.dry_run:
        result.principals_created += 1
        return AccessReviewPrincipal(plan=plan, principal_type=principal_type, **defaults)

    principal = AccessReviewPrincipal.objects.create(
        plan=plan,
        principal_type=principal_type,
        **defaults,
    )
    result.principals_created += 1
    return principal


def _rule_matches(rule, permission_level, permission_label, permission_explanation, notes):
    return (
        rule.permission_level == permission_level
        and rule.permission_label == permission_label
        and rule.permission_explanation == permission_explanation
        and rule.source == AccessReviewRule.SOURCE_SPREADSHEET
        and rule.notes == notes
    )


@transaction.atomic
def import_access_review_rules_from_rows(plan, rows, dry_run=False):
    rows = list(rows)
    result = ImportAccessReviewRulesResult(plan=plan, dry_run=dry_run, rows_read=len(rows))
    folders, children_by_parent = build_folder_indexes(plan)
    explicit_by_base = explicit_subfolders_by_base(rows)
    dry_run_principal_keys = set()

    for row_number, row in enumerate(rows, start=2):
        try:
            permission_level = normalize_permission(row.get('permissao'))
            target_folders = resolve_target_folders(row, folders, children_by_parent, explicit_by_base)
            if not target_folders:
                result.ignored += 1
                continue

            principal = get_or_prepare_principal(plan, row, result)
            if dry_run:
                principal_key = (
                    normalize_principal_type(row.get('principal_tipo')),
                    normalize_key(principal.display_name),
                )
                if principal_key in dry_run_principal_keys:
                    result.principals_created -= 1
                dry_run_principal_keys.add(principal_key)

            permission_label = dict(AccessReviewRule.PERMISSION_LEVEL_CHOICES)[permission_level]
            permission_explanation = explain_permission(permission_level)
            notes = build_rule_notes(row)

            for folder in target_folders:
                if dry_run:
                    result.rules_created += 1
                    if len(result.examples) < 8:
                        result.examples.append(
                            f'{folder.proposed_path} -> {principal.display_name} ({permission_label})'
                        )
                    continue

                rule = AccessReviewRule.objects.filter(
                    plan=plan,
                    folder=folder,
                    principal=principal,
                ).first()

                if rule:
                    if _rule_matches(rule, permission_level, permission_label, permission_explanation, notes):
                        result.ignored += 1
                        continue
                    rule.permission_level = permission_level
                    rule.permission_label = permission_label
                    rule.permission_explanation = permission_explanation
                    rule.source = AccessReviewRule.SOURCE_SPREADSHEET
                    rule.notes = notes
                    rule.save()
                    result.rules_updated += 1
                else:
                    AccessReviewRule.objects.create(
                        plan=plan,
                        folder=folder,
                        principal=principal,
                        permission_level=permission_level,
                        permission_label=permission_label,
                        permission_explanation=permission_explanation,
                        source=AccessReviewRule.SOURCE_SPREADSHEET,
                        notes=notes,
                    )
                    result.rules_created += 1
        except Exception as exc:
            result.errors.append(f'linha {row_number}: {exc}')

    return result


def import_access_review_rules_from_csv(plan, csv_path, dry_run=False):
    rows = read_csv_rows(csv_path)
    return import_access_review_rules_from_rows(plan, rows, dry_run=dry_run)
