from django.db.models import Count, Q

from access_inventory.models import AccessReviewFolder, AccessReviewRule


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
