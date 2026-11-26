# Grants do terminal administrativo

`NIGHTOWL_TERMINAL_ADMIN_PILOT_ONLY` é verdadeiro por padrão. Depois da homologação, fora do modo piloto, a gestão registra um vínculo persistente entre operador e endpoint, associado à identidade verificada pelo backend. Motivo e auditoria são exigidos na concessão e revogação, não a cada sessão. O acesso pode ser revogado e a sessão segue temporária, com autenticação Windows obrigatória. Voltar ao modo piloto bloqueia os vínculos persistentes sem apagá-los.

O piloto é vinculado ao UUID e Machine ID registrados no procedimento de ConPTY. O gestor recebe explicitamente `agents.manage_admin_terminal_grant`; o beneficiário recebe `agents.open_terminalsession` e explicitamente `agents.open_admin_terminalsession`. Não há autoconcessão nem atribuição automática, inclusive para superusuários.

No detalhe do piloto, o gestor abre **Autorização do terminal administrativo** e confirma Machine ID, motivo, referência de aprovação e validade entre 1 e 60 minutos. A revogação usa o mesmo painel e registra seus próprios motivo e referência. O grant é independente do assistido; registros administrativos antigos no grant ConPTY não são migrados automaticamente.

O operador recarrega o detalhe antes de abrir o terminal. O grant válido não substitui flag, identidade única, capability recente, relay saudável nem autenticação Windows real. A expiração de uma sessão não altera o grant, e a expiração ou revogação do grant impede novas sessões. A revogação fecha sessões administrativas ativas do beneficiário e deixa ConPTY intacto.

A homologação Windows permanece na publicação consolidada. Aplicar a migration 0057 somente depois de 0055/0056 da política de alertas, em janela autorizada.
