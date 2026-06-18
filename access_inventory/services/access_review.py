from django.db.models import Count, Q

from access_inventory.models import ADGroupMembership, AccessReviewFolder, AccessReviewPlan, AccessReviewRule, AclEntry


# Escopo executivo temporario da reestruturacao: mostrar apenas areas selecionadas
# para a primeira apresentacao. Centralizado aqui para nao espalhar nomes na UI.
EXECUTIVE_VISIBLE_ROOT_PATHS = {
    'controlsul\\administrativo',
    'controlsul\\juridico',
}


PERMISSION_EXPLANATIONS = {
    AccessReviewRule.PERMISSION_NONE: 'Sem acesso previsto.',
    AccessReviewRule.PERMISSION_RO: 'Pode abrir, listar e visualizar arquivos. Nao pode criar, editar nem excluir.',
    AccessReviewRule.PERMISSION_RW: 'Pode abrir, criar, editar e excluir arquivos.',
    AccessReviewRule.PERMISSION_FULL: 'Pode administrar a pasta, alterar permissoes e controlar todos os arquivos.',
    AccessReviewRule.PERMISSION_CUSTOM: 'Permissao personalizada. Requer analise tecnica.',
}


def explain_permission(permission_level):
    return PERMISSION_EXPLANATIONS.get(
        permission_level,
        PERMISSION_EXPLANATIONS[AccessReviewRule.PERMISSION_CUSTOM],
    )


def rule_explanation(rule):
    return rule.permission_explanation or explain_permission(rule.permission_level)


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


def access_level_from_rights(rights):
    normalized = (rights or '').lower()
    if 'fullcontrol' in normalized:
        return 'Controle total'
    if any(item in normalized for item in ['modify', 'write', 'create', 'delete']):
        return 'Leitura e escrita'
    if any(item in normalized for item in ['read', 'listexecute', 'readandexecute']):
        return 'Somente leitura'
    return rights or 'Permissao registrada'


def get_current_effective_user_access(folder):
    """Prepara a expansao executiva Pasta -> Usuario -> Permissao a partir das ACLs atuais.

    Ainda nao e usado na UI de comparacao. Mantem grupos apenas como detalhe "via grupo".
    """
    rows = []
    acl_entries = AclEntry.objects.filter(folder=folder).select_related('resolved_ad_user', 'resolved_ad_group')
    for acl in acl_entries:
        access_label = access_level_from_rights(acl.rights)
        if acl.resolved_ad_user_id:
            rows.append({
                'user': acl.resolved_ad_user,
                'permission': access_label,
                'via_group': None,
                'acl_entry': acl,
            })
            continue

        if acl.resolved_ad_group_id:
            memberships = ADGroupMembership.objects.filter(
                parent_group=acl.resolved_ad_group,
                member_user__isnull=False,
            ).select_related('member_user')
            for membership in memberships:
                rows.append({
                    'user': membership.member_user,
                    'permission': access_label,
                    'via_group': acl.resolved_ad_group,
                    'acl_entry': acl,
                })
    return rows
