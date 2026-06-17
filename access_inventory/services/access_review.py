from access_inventory.models import AccessReviewRule


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
