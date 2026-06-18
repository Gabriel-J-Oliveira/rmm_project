import csv
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
    'NONE': AccessReviewRule.PERMISSION_NONE,
    'NO': AccessReviewRule.PERMISSION_NONE,
    'SEM ACESSO': AccessReviewRule.PERMISSION_NONE,
    'RO': AccessReviewRule.PERMISSION_RO,
    'READ': AccessReviewRule.PERMISSION_RO,
    'RW': AccessReviewRule.PERMISSION_RW,
    'WRITE': AccessReviewRule.PERMISSION_RW,
    'FULL': AccessReviewRule.PERMISSION_FULL,
    'FULLCONTROL': AccessReviewRule.PERMISSION_FULL,
    'CUSTOM': AccessReviewRule.PERMISSION_CUSTOM,
    'PERSONALIZADA': AccessReviewRule.PERMISSION_CUSTOM,
}

PRINCIPAL_TYPE_MAP = {
    'usuario': AccessReviewPrincipal.PRINCIPAL_USER,
    'usuário': AccessReviewPrincipal.PRINCIPAL_USER,
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


def normalize_key(value):
    normalized = unicodedata.normalize('NFKD', value or '')
    without_accents = ''.join(character for character in normalized if not unicodedata.combining(character))
    return ' '.join(without_accents.strip().lower().split())


def normalize_path_key(value):
    return normalize_key((value or '').replace('/', '\\')).replace(' ', '')


def read_csv_rows(csv_path):
    path = Path(csv_path)
    with path.open('r', encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        fieldnames = set(reader.fieldnames or [])
        missing = REQUIRED_COLUMNS - fieldnames
        if missing:
            raise ValueError(f'Colunas obrigatorias ausentes: {", ".join(sorted(missing))}')
        return [
            {key: (value or '').strip() for key, value in row.items()}
            for row in reader
        ]


def normalize_permission(value):
    key = normalize_key(value).upper()
    key = key.replace(' ', ' ')
    permission = PERMISSION_MAP.get(key)
    if not permission:
        raise ValueError(f'Permissao desconhecida: {value}')
    return permission


def normalize_principal_type(value):
    principal_type = PRINCIPAL_TYPE_MAP.get(normalize_key(value))
    if not principal_type:
        raise ValueError(f'Tipo de principal desconhecido: {value}')
    return principal_type


def folder_sort_key(folder):
    return (folder.sort_order, normalize_path_key(folder.proposed_path), folder.id)


def build_folder_indexes(plan):
    folders = list(
        plan.folders.select_related('parent', 'current_folder').order_by('sort_order', 'proposed_path', 'id')
    )
    children_by_parent = {}
    for folder in folders:
        children_by_parent.setdefault(folder.parent_id, []).append(folder)
    for children in children_by_parent.values():
        children.sort(key=folder_sort_key)
    return folders, children_by_parent


def descendants_of(folder, children_by_parent):
    stack = list(children_by_parent.get(folder.id, []))
    descendants = []
    while stack:
        current = stack.pop(0)
        descendants.append(current)
        stack[0:0] = children_by_parent.get(current.id, [])
    return descendants


def unique_or_error(candidates, label):
    if not candidates:
        raise ValueError(f'Pasta nao encontrada: {label}')
    candidates = sorted(candidates, key=folder_sort_key)
    return candidates[0]


def find_area_folder(area, folders):
    area_key = normalize_key(area)
    candidates = [
        folder for folder in folders
        if normalize_key(folder.name) == area_key or normalize_path_key(folder.proposed_path).endswith('\\'.join(['', normalize_path_key(area)]).strip('\\'))
    ]
    return unique_or_error(candidates, area)


def find_child_by_name(parent, name, children_by_parent, fallback_descendants=True):
    name_key = normalize_key(name)
    direct = [
        folder for folder in children_by_parent.get(parent.id, [])
        if normalize_key(folder.name) == name_key
    ]
    if direct:
        return unique_or_error(direct, f'{parent.proposed_path}\\{name}')
    if fallback_descendants:
        descendants = [
            folder for folder in descendants_of(parent, children_by_parent)
            if normalize_key(folder.name) == name_key
        ]
        if descendants:
            return unique_or_error(descendants, f'{parent.proposed_path}\\{name}')
    raise ValueError(f'Pasta nao encontrada: {parent.proposed_path}\\{name}')


def explicit_subfolders_by_base(rows):
    result = {}
    for row in rows:
        if normalize_key(row.get('escopo')) != 'exata':
            continue
        subpasta = row.get('subpasta', '')
        if not subpasta or normalize_key(subpasta) == 'demais':
            continue
        key = (normalize_key(row.get('area')), normalize_key(row.get('pasta_base')))
        result.setdefault(key, set()).add(normalize_key(subpasta))
    return result


def resolve_target_folders(row, folders, children_by_parent, explicit_by_base):
    area_folder = find_area_folder(row['area'], folders)
    base_folder = find_child_by_name(area_folder, row['pasta_base'], children_by_parent)
    escopo = normalize_key(row['escopo'])
    subpasta = row.get('subpasta', '')
    if escopo == 'exata':
        if not subpasta or normalize_key(subpasta) == normalize_key(row['pasta_base']):
            return [base_folder]
        return [find_child_by_name(base_folder, subpasta, children_by_parent)]
    if escopo == 'demais_subpastas':
        explicit_key = (normalize_key(row.get('area')), normalize_key(row.get('pasta_base')))
        explicit_subfolders = explicit_by_base.get(explicit_key, set())
        return [
            child for child in children_by_parent.get(base_folder.id, [])
            if normalize_key(child.name) not in explicit_subfolders
        ]
    raise ValueError(f'Escopo desconhecido: {row["escopo"]}')


def find_ad_user(name):
    key = normalize_key(name)
    for user in ADUser.objects.all().only('id', 'display_name', 'sam_account_name'):
        if normalize_key(user.display_name) == key or normalize_key(user.sam_account_name) == key:
            return user
    return None


def find_ad_group(name):
    key = normalize_key(name)
    for group in ADGroup.objects.all().only('id', 'name', 'sam_account_name'):
        if normalize_key(group.name) == key or normalize_key(group.sam_account_name) == key:
            return group
    return None


def build_rule_notes(row):
    parts = []
    if row.get('acao'):
        parts.append(f"acao={row['acao']}")
    if row.get('observacao'):
        parts.append(f"observacao={row['observacao']}")
    return '; '.join(parts)


def get_or_prepare_principal(plan, row, dry_run=False):
    principal_type = normalize_principal_type(row['principal_tipo'])
    display_name = row['principal_nome']
    existing = None
    for principal in plan.principals.filter(principal_type=principal_type):
        if normalize_key(principal.display_name) == normalize_key(display_name):
            existing = principal
            break

    ad_user = find_ad_user(display_name) if principal_type == AccessReviewPrincipal.PRINCIPAL_USER else None
    ad_group = find_ad_group(display_name) if principal_type == AccessReviewPrincipal.PRINCIPAL_GROUP else None
    defaults = {
        'display_name': display_name,
        'sam_account_name': ad_user.sam_account_name if ad_user else '',
        'proposed_group_name': display_name if principal_type == AccessReviewPrincipal.PRINCIPAL_GROUP else '',
        'ad_user': ad_user,
        'ad_group': ad_group,
    }
    if dry_run:
        return existing, existing is None, False

    if existing:
        changed = False
        for field, value in defaults.items():
            if getattr(existing, field) != value:
                setattr(existing, field, value)
                changed = True
        if changed:
            existing.save(update_fields=[*defaults.keys(), 'updated_at'])
        return existing, False, changed

    principal = AccessReviewPrincipal.objects.create(
        plan=plan,
        principal_type=principal_type,
        **defaults,
    )
    return principal, True, False


def import_access_review_rules_from_rows(plan, rows, dry_run=False):
    result = ImportAccessReviewRulesResult(plan=plan, dry_run=dry_run, rows_read=len(rows))
    folders, children_by_parent = build_folder_indexes(plan)
    explicit_by_base = explicit_subfolders_by_base(rows)
    touched_principals = set()

    with transaction.atomic():
        for index, row in enumerate(rows, start=2):
            try:
                target_folders = resolve_target_folders(row, folders, children_by_parent, explicit_by_base)
                if not target_folders:
                    result.ignored += 1
                    result.errors.append(f'Linha {index}: escopo sem pastas alvo.')
                    continue
                permission_level = normalize_permission(row['permissao'])
                principal, principal_created, principal_updated = get_or_prepare_principal(plan, row, dry_run=dry_run)
                principal_key = (normalize_principal_type(row['principal_tipo']), normalize_key(row['principal_nome']))
                if principal_created and principal_key not in touched_principals:
                    result.principals_created += 1
                if principal_updated and principal_key not in touched_principals:
                    result.principals_updated += 1
                touched_principals.add(principal_key)

                for folder in target_folders:
                    notes = build_rule_notes(row)
                    permission_label = dict(AccessReviewRule.PERMISSION_LEVEL_CHOICES).get(permission_level, permission_level)
                    if dry_run:
                        result.rules_created += 1
                        result.examples.append(f'{folder.proposed_path} -> {row["principal_nome"]} ({permission_level})')
                        continue

                    rule, created = AccessReviewRule.objects.get_or_create(
                        plan=plan,
                        folder=folder,
                        principal=principal,
                        defaults={
                            'permission_level': permission_level,
                            'permission_label': permission_label,
                            'permission_explanation': explain_permission(permission_level),
                            'source': AccessReviewRule.SOURCE_SPREADSHEET,
                            'notes': notes,
                        },
                    )
                    if created:
                        result.rules_created += 1
                    else:
                        changed = False
                        updates = {
                            'permission_level': permission_level,
                            'permission_label': permission_label,
                            'permission_explanation': explain_permission(permission_level),
                            'source': AccessReviewRule.SOURCE_SPREADSHEET,
                            'notes': notes,
                        }
                        for field, value in updates.items():
                            if getattr(rule, field) != value:
                                setattr(rule, field, value)
                                changed = True
                        if changed:
                            rule.save(update_fields=[*updates.keys(), 'updated_at'])
                            result.rules_updated += 1
                        else:
                            result.ignored += 1
            except ValueError as error:
                result.errors.append(f'Linha {index}: {error}')
                result.ignored += 1

        if dry_run:
            transaction.set_rollback(True)
    return result


def import_access_review_rules_from_csv(plan, csv_path, dry_run=False):
    rows = read_csv_rows(csv_path)
    return import_access_review_rules_from_rows(plan, rows, dry_run=dry_run)
