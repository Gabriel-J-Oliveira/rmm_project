import re
import unicodedata

from django.db.models import Count, Q

from access_inventory.models import ADGroup, ADGroupMembership, ADUser, AccessReviewFolder, AccessReviewPlan, AccessReviewRule, AclEntry


# Escopo executivo temporario da reestruturacao: mostrar apenas areas selecionadas
# para a primeira apresentacao. Centralizado aqui para nao espalhar nomes na UI.
EXECUTIVE_VISIBLE_ROOT_PATHS = {
    'controlsul\\administrativo',
    'controlsul\\juridico',
}

TECHNICAL_REVIEW_USER_NAMES = {
    'administrador',
    'administrator',
    'backup cs',
    'infraestrutura',
    'meraki firewall',
    'nectunt tecnologia',
    'rocket chat',
    'saggin constel',
    'suporte nextcloud',
    'wts 1',
}

PERMISSION_STRENGTH = {
    AccessReviewRule.PERMISSION_NONE: 0,
    AccessReviewRule.PERMISSION_RO: 1,
    AccessReviewRule.PERMISSION_CUSTOM: 2,
    AccessReviewRule.PERMISSION_RW: 3,
    AccessReviewRule.PERMISSION_FULL: 4,
}


PERMISSION_EXPLANATIONS = {
    AccessReviewRule.PERMISSION_NONE: 'Sem acesso previsto.',
    AccessReviewRule.PERMISSION_RO: 'Pode abrir, listar e visualizar arquivos. Nao pode criar, editar nem excluir.',
    AccessReviewRule.PERMISSION_RW: 'Pode abrir, criar, editar e excluir arquivos.',
    AccessReviewRule.PERMISSION_FULL: 'Pode administrar a pasta, alterar permissoes e controlar todos os arquivos.',
    AccessReviewRule.PERMISSION_CUSTOM: 'Permissao personalizada. Requer analise tecnica.',
}

ACL_RIGHT_TRANSLATIONS = {
    'readdata': 'Listar conteudo da pasta ou ler dados de arquivos',
    'listdirectory': 'Listar conteudo da pasta ou ler dados de arquivos',
    'readattributes': 'Ler atributos, como data de criacao e propriedades',
    'readextendedattributes': 'Ler atributos estendidos',
    'createfiles': 'Criar arquivos ou gravar dados',
    'writedata': 'Criar arquivos ou gravar dados',
    'createdirectories': 'Criar subpastas ou adicionar dados',
    'appenddata': 'Criar subpastas ou adicionar dados',
    'writeattributes': 'Alterar atributos, como propriedades e datas',
    'writeextendedattributes': 'Alterar atributos estendidos',
    'delete': 'Excluir a pasta ou arquivo',
    'deletesubdirectoriesandfiles': 'Excluir subpastas e arquivos dentro desta pasta',
    'readpermissions': 'Visualizar permissoes',
    'changepermissions': 'Alterar permissoes',
    'takeownership': 'Assumir propriedade',
    'fullcontrol': 'Controle total sobre a pasta',
    'modify': 'Modificar conteudo, incluindo criacao, alteracao e exclusao',
    'readandexecute': 'Ler e executar arquivos',
    'read': 'Ler conteudo',
    'write': 'Gravar ou alterar conteudo',
    'synchronize': 'Sincronizacao tecnica usada pelo Windows',
}

PERMISSION_SUMMARIES = {
    'Controle total': 'Pode abrir, criar, alterar, excluir e administrar permissoes desta pasta.',
    'Leitura e escrita': 'Pode abrir, criar, alterar e excluir arquivos nesta pasta.',
    'Somente leitura': 'Pode abrir, listar e visualizar arquivos. Nao pode criar, alterar ou excluir.',
    'Negado': 'Acesso negado explicitamente por uma regra de permissao.',
    'Personalizada': 'Permissao especial com acoes especificas. Requer atencao antes de alterar.',
}


def explain_permission(permission_level):
    return PERMISSION_EXPLANATIONS.get(
        permission_level,
        PERMISSION_EXPLANATIONS[AccessReviewRule.PERMISSION_CUSTOM],
    )


def rule_explanation(rule):
    return rule.permission_explanation or explain_permission(rule.permission_level)


def normalize_review_user_value(value):
    value = '' if value is None else str(value)
    value = unicodedata.normalize('NFKD', value)
    value = ''.join(character for character in value if not unicodedata.combining(character))
    return re.sub(r'\s+', ' ', value).strip().lower()


def review_user_identity_values(ad_user):
    values = []
    for field_name in ('display_name', 'name', 'sam_account_name', 'username', 'user_principal_name', 'email'):
        if hasattr(ad_user, field_name):
            value = getattr(ad_user, field_name, '')
            if value:
                values.append(value)
                if field_name in {'email', 'user_principal_name'} and '@' in value:
                    values.append(value.split('@', 1)[0])
    return values


def is_technical_review_user(ad_user):
    normalized_values = {
        normalize_review_user_value(value)
        for value in review_user_identity_values(ad_user)
        if normalize_review_user_value(value)
    }
    return bool(normalized_values & TECHNICAL_REVIEW_USER_NAMES)


def is_inactive_review_user(ad_user):
    inactive_false_fields = ('enabled', 'is_active', 'active')
    inactive_true_fields = ('disabled', 'account_disabled', 'is_disabled', 'locked', 'locked_out', 'lockout')

    for field_name in inactive_false_fields:
        if hasattr(ad_user, field_name) and getattr(ad_user, field_name) is False:
            return True

    for field_name in inactive_true_fields:
        if hasattr(ad_user, field_name) and getattr(ad_user, field_name) is True:
            return True

    for field_name in ('user_account_control', 'userAccountControl'):
        if hasattr(ad_user, field_name):
            value = getattr(ad_user, field_name)
            try:
                if int(value) & 0x0002:
                    return True
            except (TypeError, ValueError):
                pass

    return False


def review_user_ou_values(ad_user):
    values = []
    for field_name in ('distinguished_name', 'dn', 'canonical_name', 'organizational_unit', 'path'):
        if hasattr(ad_user, field_name):
            value = getattr(ad_user, field_name, '')
            if value:
                values.append(value)

    ou = getattr(ad_user, 'ou', None)
    if ou:
        for field_name in ('name', 'distinguished_name', 'parent_distinguished_name'):
            value = getattr(ou, field_name, '')
            if value:
                values.append(value)
    return values


def is_partner_review_user(ad_user):
    for value in review_user_ou_values(ad_user):
        normalized = normalize_review_user_value(value)
        path_normalized = normalized.replace('/', '\\')
        if 'ou=socios' in normalized or '\\socios\\' in f'\\{path_normalized}\\':
            return True
    return False


def can_show_in_partner_review_card(ad_user):
    if not ad_user:
        return False
    if is_technical_review_user(ad_user):
        return False
    if is_inactive_review_user(ad_user):
        return False
    return is_partner_review_user(ad_user)


def is_displayable_review_user(ad_user):
    if not ad_user:
        return False
    if is_technical_review_user(ad_user):
        return False
    if is_inactive_review_user(ad_user):
        return False
    if is_partner_review_user(ad_user):
        return False
    return True


def get_partner_review_users():
    users = (
        ADUser.objects.select_related('ou')
        .filter(enabled=True)
        .order_by('display_name', 'sam_account_name', 'id')
    )
    return [user for user in users if can_show_in_partner_review_card(user)]


def domain_admins_identity_values(value_or_group):
    if not value_or_group:
        return []
    if isinstance(value_or_group, str):
        return [value_or_group]
    values = []
    for field_name in ('name', 'sam_account_name', 'display_name', 'distinguished_name', 'sid'):
        if hasattr(value_or_group, field_name):
            value = getattr(value_or_group, field_name, '')
            if value:
                values.append(value)
    return values


def is_domain_admins_principal(value_or_group):
    for value in domain_admins_identity_values(value_or_group):
        normalized = normalize_review_user_value(value)
        if not normalized:
            continue
        short_name = normalized.split('\\')[-1]
        if short_name == 'domain admins':
            return True
        if 'cn=domain admins' in normalized:
            return True
    return False


def is_removal_review_rule(rule):
    notes = normalize_review_user_value(getattr(rule, 'notes', ''))
    return (
        rule.permission_level == AccessReviewRule.PERMISSION_NONE
        or 'acao=remover' in notes
        or 'acao: remover' in notes
    )


def review_rule_action(rule):
    notes = normalize_review_user_value(getattr(rule, 'notes', ''))
    for action in ('manter', 'adicionar', 'alterar', 'remover'):
        if f'acao={action}' in notes or f'acao: {action}' in notes:
            return action
    if rule.permission_level == AccessReviewRule.PERMISSION_NONE:
        return 'remover'
    return ''


def is_positive_review_rule(rule):
    return rule.permission_level in {
        AccessReviewRule.PERMISSION_RO,
        AccessReviewRule.PERMISSION_RW,
        AccessReviewRule.PERMISSION_FULL,
        AccessReviewRule.PERMISSION_CUSTOM,
    }


def is_maintained_review_rule(rule):
    notes = normalize_review_user_value(getattr(rule, 'notes', ''))
    return (
        is_positive_review_rule(rule)
        and not is_removal_review_rule(rule)
        and ('acao=manter' in notes or 'acao: manter' in notes)
    )


def review_person_key_from_user(ad_user):
    if ad_user and getattr(ad_user, 'id', None):
        return ('user', ad_user.id)
    if ad_user:
        return ('name', normalize_review_user_value(ad_user.display_name or ad_user.sam_account_name))
    return None


def review_person_key_from_name(name):
    return ('name', normalize_review_user_value(name))


def review_principal_key(principal):
    if principal.ad_user_id:
        return review_person_key_from_user(principal.ad_user)
    if principal.ad_group_id:
        return ('group', principal.ad_group_id)
    prefix = 'group' if principal.principal_type == 'group' else 'name'
    return (prefix, normalize_review_user_value(principal.display_name))


def permission_level_label(permission_level):
    return dict(AccessReviewRule.PERMISSION_LEVEL_CHOICES).get(permission_level, permission_level)


def permission_strength(permission_level):
    return PERMISSION_STRENGTH.get(permission_level, 0)


def resolve_review_principal_group(principal):
    if principal.ad_group_id:
        return principal.ad_group
    candidates = [
        principal.proposed_group_name,
        principal.sam_account_name,
        principal.display_name,
    ]
    normalized_candidates = {
        normalize_review_user_value(value)
        for value in candidates
        if value
    }
    if not normalized_candidates:
        return None
    for group in ADGroup.objects.all().order_by('name', 'sam_account_name', 'id'):
        values = {
            normalize_review_user_value(group.name),
            normalize_review_user_value(group.sam_account_name),
        }
        if values & normalized_candidates:
            return group
    return None


def proposed_access_row_from_rule(rule, user=None, origin_group=None):
    principal = rule.principal
    display_name = principal.display_name
    username = principal.sam_account_name
    origin_text = 'regra direta'
    origin_group_name = ''

    if user:
        display_name = user.display_name or user.sam_account_name
        username = user.sam_account_name

    if origin_group:
        origin_group_name = origin_group.name or origin_group.sam_account_name
        origin_text = f'via grupo {origin_group_name}'

    return {
        'user': user,
        'display_name': display_name,
        'username': username,
        'permission_level': rule.permission_level,
        'permission_label': permission_level_label(rule.permission_level),
        'permission_description': rule_explanation(rule),
        'origin_group_name': origin_group_name,
        'origin_text': origin_text,
        'action': review_rule_action(rule),
        'rule': rule,
    }


def merge_effective_access_row(rows_by_key, key, row):
    existing = rows_by_key.get(key)
    if not existing or permission_strength(row['permission_level']) > permission_strength(existing['permission_level']):
        rows_by_key[key] = row
        return
    if existing and row['origin_text'] not in existing.get('origin_text', ''):
        existing['origin_text'] = f'{existing["origin_text"]}; {row["origin_text"]}'


def get_proposed_effective_access_for_rules(rules, action_filter=None):
    rows_by_key = {}
    positive_rules = [
        rule for rule in rules
        if is_positive_review_rule(rule) and not is_removal_review_rule(rule)
    ]
    if action_filter:
        positive_rules = [rule for rule in positive_rules if review_rule_action(rule) in action_filter]

    group_ids = []
    group_by_rule_id = {}
    for rule in positive_rules:
        principal = rule.principal
        if principal.principal_type != 'group':
            continue
        group = resolve_review_principal_group(principal)
        if not group or is_domain_admins_principal(group):
            continue
        group_by_rule_id[rule.id] = group
        group_ids.append(group.id)

    memberships_by_group = {}
    if group_ids:
        memberships = (
            ADGroupMembership.objects.filter(parent_group_id__in=group_ids, member_user__isnull=False)
            .select_related('parent_group', 'member_user')
            .order_by('member_user__display_name', 'member_user__sam_account_name')
        )
        for membership in memberships:
            memberships_by_group.setdefault(membership.parent_group_id, []).append(membership)

    for rule in positive_rules:
        principal = rule.principal
        if principal.ad_user_id:
            user = principal.ad_user
            if not is_displayable_review_user(user):
                continue
            merge_effective_access_row(rows_by_key, review_person_key_from_user(user), proposed_access_row_from_rule(rule, user=user))
            continue

        if principal.principal_type == 'user':
            merge_effective_access_row(
                rows_by_key,
                review_person_key_from_name(principal.display_name),
                proposed_access_row_from_rule(rule),
            )
            continue

        group = group_by_rule_id.get(rule.id)
        if not group:
            continue
        for membership in memberships_by_group.get(group.id, []):
            user = membership.member_user
            if not is_displayable_review_user(user):
                continue
            row = proposed_access_row_from_rule(rule, user=user, origin_group=group)
            merge_effective_access_row(rows_by_key, review_person_key_from_user(user), row)

    return sorted(
        rows_by_key.values(),
        key=lambda item: normalize_review_user_value(item.get('display_name')),
    )


def get_proposed_effective_access_for_folder(review_folder, action_filter=None):
    rules = list(
        review_folder.rules.select_related('principal', 'principal__ad_user', 'principal__ad_group').all()
    )
    return get_proposed_effective_access_for_rules(rules, action_filter=action_filter)


def can_show_maintained_review_principal(principal):
    if principal.ad_user_id:
        return is_displayable_review_user(principal.ad_user)
    if principal.ad_group_id and is_domain_admins_principal(principal.ad_group):
        return False
    if is_domain_admins_principal(principal.display_name) or is_domain_admins_principal(principal.proposed_group_name):
        return False
    return True


def build_maintained_review_access_rows(rules):
    rows = get_proposed_effective_access_for_rules(list(rules), action_filter={'manter'})
    for row in rows:
        row['badge'] = 'ACESSO MANTIDO'
        row['message'] = 'Permissao mantida pela proposta.'
        row['source'] = row.get('origin_text') or row['rule'].notes
    return rows


def build_revoked_review_access_rows(current_access_rows, user_rules):
    revoked_by_key = {}

    for rule in user_rules:
        if not is_removal_review_rule(rule):
            continue
        principal = rule.principal
        ad_user = principal.ad_user
        if ad_user and not is_displayable_review_user(ad_user):
            continue
        display_name = principal.display_name
        username = principal.sam_account_name
        key = review_person_key_from_user(ad_user) if ad_user else review_person_key_from_name(display_name)
        revoked_by_key[key] = {
            'display_name': display_name,
            'username': username,
            'badge': 'ACESSO SERA REVOGADO',
            'message': 'Acesso sera revogado nesta pasta.',
            'source': 'regra proposta de remocao',
            'priority': 0,
        }

    for row in current_access_rows:
        if not row.get('is_domain_admins_access'):
            continue
        user = row.get('user')
        if not is_displayable_review_user(user):
            continue
        key = review_person_key_from_user(user)
        if key in revoked_by_key:
            continue
        revoked_by_key[key] = {
            'display_name': row.get('display_name'),
            'username': row.get('username'),
            'badge': 'ACESSO ADMINISTRATIVO SERA REMOVIDO',
            'message': 'Acesso administrativo via Domain Admins sera removido da permissao de negocio desta pasta.',
            'source': row.get('origin_label'),
            'priority': 1,
        }

    return sorted(
        revoked_by_key.values(),
        key=lambda item: (item['priority'], normalize_review_user_value(item.get('display_name'))),
    )


def get_executive_review_plans():
    latest_plan = (
        AccessReviewPlan.objects.exclude(status=AccessReviewPlan.STATUS_ARCHIVED)
        .order_by('-id')
        .first()
    )
    if not latest_plan:
        return AccessReviewPlan.objects.none()
    return AccessReviewPlan.objects.filter(pk=latest_plan.pk)


def folder_navigation_queryset(plan):
    return AccessReviewFolder.objects.filter(plan=plan).select_related(
        'parent',
        'current_folder',
    ).annotate(
        direct_child_count=Count('children', filter=Q(children__plan=plan), distinct=True),
        direct_rule_count=Count('rules', distinct=True),
    ).order_by('sort_order', 'name', 'proposed_path')


def is_technical_container(folder):
    return (
        folder
        and folder.parent_id is None
        and (folder.proposed_path or '').strip('\\') == (folder.name or '').strip('\\')
        and getattr(folder, 'direct_child_count', None)
    )


def get_business_roots(plan):
    roots = list(folder_navigation_queryset(plan).filter(parent__isnull=True))
    if len(roots) == 1 and is_technical_container(roots[0]):
        return list(get_folder_children(roots[0]))
    return roots


def get_plan_visible_roots(plan):
    roots = get_business_roots(plan)
    latest_plan = get_executive_review_plans().first()
    if not latest_plan or plan.id != latest_plan.id:
        return roots

    scoped_roots = [
        folder for folder in roots
        if (folder.proposed_path or '').strip('\\').lower() in EXECUTIVE_VISIBLE_ROOT_PATHS
    ]
    return scoped_roots or roots


def get_folder_children(folder):
    return folder_navigation_queryset(folder.plan).filter(parent=folder)


def get_folder_breadcrumb(folder):
    nodes = []
    current = folder
    while current:
        nodes.append(current)
        current = current.parent
    nodes.reverse()

    if len(nodes) > 1:
        root = nodes[0]
        root.direct_child_count = root.children.filter(plan=folder.plan).count()
        if is_technical_container(root):
            nodes = nodes[1:]
    return nodes


def count_direct_children(folder):
    return folder.children.filter(plan=folder.plan).count()


def count_rules_for_folder(folder):
    return folder.rules.count()


def split_acl_rights(rights):
    return [
        item.strip()
        for item in (rights or '').replace(';', ',').split(',')
        if item.strip()
    ]


def normalize_acl_right(value):
    return ''.join(character for character in value.lower() if character.isalnum())


def translated_acl_rights(rights):
    details = []
    seen = set()
    for right in split_acl_rights(rights):
        normalized = normalize_acl_right(right)
        detail = ACL_RIGHT_TRANSLATIONS.get(normalized)
        if detail and detail not in seen:
            details.append(detail)
            seen.add(detail)
    return details


def classify_acl_rights(rights):
    normalized_rights = {normalize_acl_right(item) for item in split_acl_rights(rights)}
    if not normalized_rights:
        return 'Personalizada'
    if 'fullcontrol' in normalized_rights:
        return 'Controle total'
    if 'modify' in normalized_rights or {'readandexecute', 'write'}.issubset(normalized_rights):
        return 'Leitura e escrita'
    if normalized_rights and normalized_rights.issubset({
        'read',
        'readandexecute',
        'listdirectory',
        'readdata',
        'readattributes',
        'readextendedattributes',
        'readpermissions',
        'synchronize',
    }):
        return 'Somente leitura'
    return 'Personalizada'


def describe_acl_rights(acl):
    if acl.access_type == AclEntry.ACCESS_DENY:
        label = 'Negado'
    else:
        label = classify_acl_rights(acl.rights)
    details = translated_acl_rights(acl.rights)
    return {
        'permission_label': label,
        'permission_summary': PERMISSION_SUMMARIES[label],
        'permission_details': details,
        'technical_rights': acl.rights or '',
        'is_custom': label == 'Personalizada',
        'is_special_permission': label == 'Personalizada' or bool(details),
    }


def current_folder_from_review_folder(review_folder):
    return getattr(review_folder, 'current_folder', review_folder)


def get_current_effective_user_access(review_folder, limit=200):
    """Expande ACLs atuais em linhas executivas Pasta -> Usuario -> Permissao.

    Mantem grupos apenas como detalhe "via grupo" e usa identidades ja resolvidas.
    """
    current_folder = current_folder_from_review_folder(review_folder)
    result = {
        'current_folder': current_folder,
        'rows': [],
        'unknown_acl_entries': [],
        'groups_without_members': [],
        'hidden_users_count': 0,
        'total_rows': 0,
        'is_limited': False,
        'empty_reason': '',
    }
    if not current_folder:
        result['empty_reason'] = 'Pasta planejada sem vinculo com snapshot atual.'
        return result

    rows = []
    unknown_acl_entries = []
    groups_without_members = []
    seen = set()
    acl_entries = list(
        AclEntry.objects.filter(folder=current_folder)
        .select_related('resolved_ad_user', 'resolved_ad_group')
        .order_by('identity_name', 'rights', 'access_type', 'inherited')
    )
    group_ids = [
        acl.resolved_ad_group_id
        for acl in acl_entries
        if acl.resolved_identity_type == AclEntry.IDENTITY_GROUP and acl.resolved_ad_group_id
    ]
    memberships_by_group = {}
    if group_ids:
        memberships = (
            ADGroupMembership.objects.filter(
                parent_group_id__in=group_ids,
                member_user__isnull=False,
            )
            .select_related('parent_group', 'member_user')
            .order_by('member_user__display_name', 'member_user__sam_account_name')
        )
        for membership in memberships:
            memberships_by_group.setdefault(membership.parent_group_id, []).append(membership)

    for acl in acl_entries:
        permission_description = describe_acl_rights(acl)
        permission_label = permission_description['permission_label']
        permission_level = permission_label.lower().replace(' ', '_')
        inheritance_label = 'herdado' if acl.inherited else 'direto'
        inheritance_summary = 'vem de uma pasta acima' if acl.inherited else 'definido nesta pasta'
        if acl.resolved_identity_type == AclEntry.IDENTITY_USER and acl.resolved_ad_user_id:
            if not is_displayable_review_user(acl.resolved_ad_user):
                result['hidden_users_count'] += 1
                continue
            key = (acl.resolved_ad_user_id, permission_label, 'direct', acl.id)
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                'user': acl.resolved_ad_user,
                'display_name': acl.resolved_ad_user.display_name or acl.resolved_ad_user.sam_account_name,
                'username': acl.resolved_ad_user.sam_account_name,
                'permission': permission_label,
                'permission_level': permission_level,
                **permission_description,
                'origin_type': 'direct',
                'origin_label': 'acesso direto na pasta',
                'via_group': None,
                'is_domain_admins_access': False,
                'acl_entry': acl,
                'is_inherited': acl.inherited,
                'inheritance_label': inheritance_label,
                'inheritance_summary': inheritance_summary,
            })
            continue

        if acl.resolved_identity_type == AclEntry.IDENTITY_GROUP and acl.resolved_ad_group_id:
            memberships = memberships_by_group.get(acl.resolved_ad_group_id, [])
            is_domain_admins_access = is_domain_admins_principal(acl.resolved_ad_group) or is_domain_admins_principal(acl.identity_name)
            origin_label = (
                'acesso administrativo via Domain Admins'
                if is_domain_admins_access
                else f'via grupo {acl.resolved_ad_group.name or acl.resolved_ad_group.sam_account_name}'
            )
            if not memberships:
                groups_without_members.append(acl.resolved_ad_group)
            for membership in memberships[:limit]:
                if not is_displayable_review_user(membership.member_user):
                    result['hidden_users_count'] += 1
                    continue
                key = (membership.member_user_id, permission_label, 'group', acl.resolved_ad_group_id, acl.id)
                if key in seen:
                    continue
                seen.add(key)
                rows.append({
                    'user': membership.member_user,
                    'display_name': membership.member_user.display_name or membership.member_user.sam_account_name,
                    'username': membership.member_user.sam_account_name,
                    'permission': permission_label,
                    'permission_level': permission_level,
                    **permission_description,
                    'origin_type': 'group',
                    'origin_label': origin_label,
                    'via_group': acl.resolved_ad_group,
                    'is_domain_admins_access': is_domain_admins_access,
                    'acl_entry': acl,
                    'is_inherited': acl.inherited,
                    'inheritance_label': inheritance_label,
                    'inheritance_summary': inheritance_summary,
                })
            continue

        unknown_acl_entries.append(acl)

    rows.sort(key=lambda item: ((item['display_name'] or '').lower(), item['permission'], item['origin_label']))
    result['total_rows'] = len(rows)
    result['rows'] = rows[:limit]
    result['is_limited'] = len(rows) > limit
    result['unknown_acl_entries'] = unknown_acl_entries
    result['groups_without_members'] = groups_without_members
    if not rows:
        result['empty_reason'] = 'Nenhuma permissao atual resolvida para usuarios nesta pasta.'
    return result
