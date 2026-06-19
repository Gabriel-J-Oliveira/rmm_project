import re
import unicodedata
from dataclasses import dataclass, field

from django.db import transaction

from access_inventory.models import (
    ADGroup,
    ADGroupMembership,
    AccessReviewFolder,
    AccessReviewPrincipal,
    AccessReviewRule,
    AclEntry,
)
from access_inventory.services.access_review import (
    get_current_effective_user_access,
    is_displayable_review_user,
    is_domain_admins_principal,
)


PERMISSION_ALIASES = {
    AccessReviewRule.PERMISSION_RO: {'ro', 'read', 'leitura', 'somenteleitura'},
    AccessReviewRule.PERMISSION_RW: {'rw', 'modify', 'modificar', 'escrita', 'leituraescrita', 'leituraeescrita'},
    AccessReviewRule.PERMISSION_FULL: {'full', 'fullcontrol', 'controletotal', 'admin'},
    AccessReviewRule.PERMISSION_CUSTOM: {'custom', 'especial'},
}

PERMISSION_FALLBACK_ORDER = [
    AccessReviewRule.PERMISSION_FULL,
    AccessReviewRule.PERMISSION_CUSTOM,
    AccessReviewRule.PERMISSION_RW,
    AccessReviewRule.PERMISSION_RO,
]

PERMISSION_SUFFIX_MAP = {
    'RO': AccessReviewRule.PERMISSION_RO,
    'RW': AccessReviewRule.PERMISSION_RW,
    'FULL': AccessReviewRule.PERMISSION_FULL,
    'CUSTOM': AccessReviewRule.PERMISSION_CUSTOM,
}

COMPACT_PERMISSION_ALIASES = {
    'somenteleitura',
    'leituraescrita',
    'leituraeescrita',
    'fullcontrol',
    'controletotal',
}


@dataclass
class GroupSyncDecision:
    group: ADGroup
    folder: AccessReviewFolder | None = None
    permission_level: str = ''
    status: str = 'ignored'
    reason: str = ''
    candidates: list[AccessReviewFolder] = field(default_factory=list)
    action: str = ''


@dataclass
class GroupSyncResult:
    plan: object
    area: str = ''
    dry_run: bool = False
    groups_found: int = 0
    groups_mapped: int = 0
    groups_ambiguous: int = 0
    groups_without_folder: int = 0
    groups_without_permission: int = 0
    principals_created: int = 0
    principals_updated: int = 0
    rules_created: int = 0
    rules_updated: int = 0
    rules_deleted: int = 0
    ignored: int = 0
    errors: list[str] = field(default_factory=list)
    decisions: list[GroupSyncDecision] = field(default_factory=list)


def normalize_review_text(value):
    value = '' if value is None else str(value)
    value = unicodedata.normalize('NFKD', value)
    value = ''.join(character for character in value if not unicodedata.combining(character))
    value = re.sub(r'[_\-\\/]+', ' ', value)
    value = re.sub(r'[^a-zA-Z0-9]+', ' ', value)
    return re.sub(r'\s+', ' ', value).strip().casefold()


def compact_review_text(value):
    return normalize_review_text(value).replace(' ', '')


def token_set(value):
    return {token for token in normalize_review_text(value).split() if token}


def group_search_text(group):
    return ' '.join(
        value for value in [
            group.name,
            group.sam_account_name,
            group.description,
            group.distinguished_name,
        ]
        if value
    )


def group_tokens(group):
    return token_set(group_search_text(group))


def folder_depth(folder):
    return len([part for part in (folder.proposed_path or '').split('\\') if part])


def folder_tokens(folder):
    return token_set(f'{folder.name} {folder.proposed_path}')


def folder_name_tokens(folder):
    return token_set(folder.name)


def filter_plan_folders(plan, area=''):
    folders = list(
        AccessReviewFolder.objects.filter(plan=plan)
        .select_related('current_folder', 'parent')
        .order_by('sort_order', 'proposed_path', 'id')
    )
    if not area:
        return folders

    area_key = compact_review_text(area)
    scoped = []
    for folder in folders:
        path_parts = [compact_review_text(part) for part in (folder.proposed_path or '').split('\\')]
        if area_key in path_parts:
            scoped.append(folder)
    return scoped


def group_matches_scope(group, folders, area='', prefixes=None):
    prefixes = prefixes or []
    normalized_group = normalize_review_text(group_search_text(group))
    compact_group = normalized_group.replace(' ', '')

    if prefixes:
        return any(compact_group.startswith(compact_review_text(prefix)) for prefix in prefixes)

    if area and compact_review_text(area) in compact_group:
        return True

    group_token_values = group_tokens(group)
    return any(folder_name_tokens(folder) and folder_name_tokens(folder).issubset(group_token_values) for folder in folders)


def discover_candidate_groups(folders, area='', prefixes=None):
    groups = ADGroup.objects.all().order_by('name', 'sam_account_name', 'id')
    return [
        group for group in groups
        if group_matches_scope(group, folders, area=area, prefixes=prefixes)
    ]


def detect_permission_suffix(value):
    match = re.search(r'(?:^|[_\s-])(RO|RW|FULL|CUSTOM)$', (value or '').strip(), flags=re.IGNORECASE)
    if not match:
        return ''
    return PERMISSION_SUFFIX_MAP[match.group(1).upper()]


def detect_permission_from_group(group, default_permission=''):
    for value in (group.name, group.sam_account_name):
        permission = detect_permission_suffix(value)
        if permission:
            return permission

    fallback_text = ' '.join(
        value for value in [
            group.description,
            group.distinguished_name,
            group.name,
            group.sam_account_name,
        ]
        if value
    )
    compact_text = compact_review_text(fallback_text)
    tokens = token_set(fallback_text)

    for permission in PERMISSION_FALLBACK_ORDER:
        aliases = PERMISSION_ALIASES[permission]
        for alias in aliases:
            if alias in tokens or (alias in COMPACT_PERMISSION_ALIASES and alias in compact_text):
                return permission

    if default_permission:
        normalized = compact_review_text(default_permission)
        for permission, aliases in PERMISSION_ALIASES.items():
            if normalized == permission or normalized in aliases:
                return permission
    return ''


def detect_permission(group, default_permission=''):
    return detect_permission_from_group(group, default_permission=default_permission)


def map_group_to_folder(group, folders):
    tokens = group_tokens(group)
    scored = []
    for folder in folders:
        name_tokens = folder_name_tokens(folder)
        if not name_tokens or not name_tokens.issubset(tokens):
            continue
        overlap = len(folder_tokens(folder) & tokens)
        score = (len(name_tokens), overlap, folder_depth(folder))
        scored.append((score, folder))

    if not scored:
        return None, []

    best_score = max(score for score, _folder in scored)
    candidates = [folder for score, folder in scored if score == best_score]
    if len(candidates) == 1:
        return candidates[0], candidates
    return None, candidates


def group_exists_in_current_acl(folder, group):
    current_folder = folder.current_folder
    if not current_folder:
        return False
    return AclEntry.objects.filter(folder=current_folder, resolved_ad_group=group).exists()


def current_acl_permission_for_group(folder, group):
    current_folder = folder.current_folder
    if not current_folder:
        return ''
    acl = AclEntry.objects.filter(folder=current_folder, resolved_ad_group=group).order_by('id').first()
    if not acl:
        return ''
    rights = compact_review_text(acl.rights)
    if 'fullcontrol' in rights:
        return AccessReviewRule.PERMISSION_FULL
    if 'modify' in rights or 'write' in rights:
        return AccessReviewRule.PERMISSION_RW
    if 'read' in rights or 'readandexecute' in rights:
        return AccessReviewRule.PERMISSION_RO
    return ''


def infer_action(folder, group, permission_level):
    if not group_exists_in_current_acl(folder, group):
        return 'adicionar'
    current_permission = current_acl_permission_for_group(folder, group)
    if current_permission and current_permission != permission_level:
        return 'alterar'
    return 'manter'


def get_or_build_principal(plan, group, result, dry_run=False):
    principal = AccessReviewPrincipal.objects.filter(plan=plan, ad_group=group).first()
    defaults = {
        'principal_type': AccessReviewPrincipal.PRINCIPAL_GROUP,
        'display_name': group.name or group.sam_account_name,
        'sam_account_name': group.sam_account_name,
        'proposed_group_name': group.sam_account_name or group.name,
        'ad_group': group,
        'notes': f'Gerado automaticamente a partir do grupo AD {group.name or group.sam_account_name}.',
    }

    if principal:
        changed = any(getattr(principal, field) != value for field, value in defaults.items())
        if changed:
            result.principals_updated += 1
            if not dry_run:
                for field, value in defaults.items():
                    setattr(principal, field, value)
                principal.save()
        return principal

    result.principals_created += 1
    if dry_run:
        return AccessReviewPrincipal(plan=plan, **defaults)
    return AccessReviewPrincipal.objects.create(plan=plan, **defaults)


def sync_access_review_rules_from_ad_groups(
    plan,
    area='',
    group_prefixes=None,
    default_permission='',
    dry_run=False,
    clear_existing=False,
):
    result = GroupSyncResult(plan=plan, area=area, dry_run=dry_run)
    folders = filter_plan_folders(plan, area=area)
    groups = discover_candidate_groups(folders, area=area, prefixes=group_prefixes)
    result.groups_found = len(groups)

    folder_ids_for_clear = [folder.id for folder in folders]
    with transaction.atomic():
        if clear_existing and folder_ids_for_clear:
            queryset = AccessReviewRule.objects.filter(plan=plan, folder_id__in=folder_ids_for_clear)
            result.rules_deleted = queryset.count()
            if not dry_run:
                queryset.delete()

        for group in groups:
            permission_level = detect_permission(group, default_permission=default_permission)
            folder, candidates = map_group_to_folder(group, folders)
            decision = GroupSyncDecision(group=group, folder=folder, permission_level=permission_level, candidates=candidates)

            if not permission_level:
                decision.status = 'without_permission'
                decision.reason = 'permissao nao identificada'
                result.groups_without_permission += 1
                result.decisions.append(decision)
                continue

            if not folder and candidates:
                decision.status = 'ambiguous'
                decision.reason = 'pasta ambigua'
                result.groups_ambiguous += 1
                result.decisions.append(decision)
                continue

            if not folder:
                decision.status = 'without_folder'
                decision.reason = 'pasta nao encontrada'
                result.groups_without_folder += 1
                result.decisions.append(decision)
                continue

            action = infer_action(folder, group, permission_level)
            decision.status = 'ok'
            decision.action = action
            result.groups_mapped += 1

            principal = get_or_build_principal(plan, group, result, dry_run=dry_run)
            label = dict(AccessReviewRule.PERMISSION_LEVEL_CHOICES).get(permission_level, permission_level)
            notes = f'acao={action}; Gerado automaticamente a partir do grupo AD {group.name or group.sam_account_name}.'

            if dry_run:
                result.rules_created += 1
                result.decisions.append(decision)
                continue

            rule = AccessReviewRule.objects.filter(plan=plan, folder=folder, principal=principal).first()
            if rule:
                changed = (
                    rule.permission_level != permission_level
                    or rule.permission_label != label
                    or rule.source != AccessReviewRule.SOURCE_IMPORTED
                    or rule.notes != notes
                )
                if changed:
                    rule.permission_level = permission_level
                    rule.permission_label = label
                    rule.permission_explanation = ''
                    rule.source = AccessReviewRule.SOURCE_IMPORTED
                    rule.notes = notes
                    rule.save()
                    result.rules_updated += 1
                else:
                    result.ignored += 1
            else:
                AccessReviewRule.objects.create(
                    plan=plan,
                    folder=folder,
                    principal=principal,
                    permission_level=permission_level,
                    permission_label=label,
                    source=AccessReviewRule.SOURCE_IMPORTED,
                    notes=notes,
                )
                result.rules_created += 1
            result.decisions.append(decision)

    return result


def get_proposed_effective_users_for_folder(review_folder):
    rows = []
    seen = set()
    rules = (
        AccessReviewRule.objects.filter(folder=review_folder)
        .select_related('principal', 'principal__ad_user', 'principal__ad_group')
        .exclude(permission_level=AccessReviewRule.PERMISSION_NONE)
    )

    group_ids = [rule.principal.ad_group_id for rule in rules if rule.principal.ad_group_id]
    memberships_by_group = {}
    if group_ids:
        memberships = (
            ADGroupMembership.objects.filter(parent_group_id__in=group_ids, member_user__isnull=False)
            .select_related('parent_group', 'member_user')
            .order_by('member_user__display_name', 'member_user__sam_account_name')
        )
        for membership in memberships:
            memberships_by_group.setdefault(membership.parent_group_id, []).append(membership)

    for rule in rules:
        principal = rule.principal
        if principal.ad_user_id:
            user = principal.ad_user
            if not is_displayable_review_user(user):
                continue
            key = user.id
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                'user': user,
                'display_name': user.display_name or user.sam_account_name,
                'permission_level': rule.permission_level,
                'permission_label': permission_level_label(rule.permission_level),
                'origin': 'regra proposta direta',
                'rule': rule,
            })
        elif principal.ad_group_id and not is_domain_admins_principal(principal.ad_group):
            for membership in memberships_by_group.get(principal.ad_group_id, []):
                user = membership.member_user
                if not is_displayable_review_user(user):
                    continue
                key = user.id
                if key in seen:
                    continue
                seen.add(key)
                rows.append({
                    'user': user,
                    'display_name': user.display_name or user.sam_account_name,
                    'permission_level': rule.permission_level,
                    'permission_label': permission_level_label(rule.permission_level),
                    'origin': f'via grupo {principal.ad_group.name or principal.ad_group.sam_account_name}',
                    'rule': rule,
                })
    return rows


def compare_current_and_proposed_user_access(review_folder):
    current_rows = get_current_effective_user_access(review_folder).get('rows', [])
    proposed_rows = get_proposed_effective_users_for_folder(review_folder)
    current_by_user = {row['user'].id: row for row in current_rows if row.get('user')}
    proposed_by_user = {row['user'].id: row for row in proposed_rows if row.get('user')}

    maintained = []
    added = []
    removed = []
    changed = []

    for user_id, proposed in proposed_by_user.items():
        current = current_by_user.get(user_id)
        if not current:
            added.append(proposed)
            continue
        if current.get('permission') != proposed.get('permission_label'):
            changed.append({'current': current, 'proposed': proposed})
        else:
            maintained.append(proposed)

    for user_id, current in current_by_user.items():
        if user_id not in proposed_by_user:
            removed.append(current)

    return {
        'current': current_rows,
        'proposed': proposed_rows,
        'maintained': maintained,
        'added': added,
        'removed': removed,
        'changed': changed,
    }


def permission_level_label(permission_level):
    return dict(AccessReviewRule.PERMISSION_LEVEL_CHOICES).get(permission_level, permission_level)
