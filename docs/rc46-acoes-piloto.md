# RC46 — preparação do piloto de ações

O executor, fila, início confirmado, resultado, ACK, auditoria, catálogo e validação de identidade já existem. As ações e scripts continuam desativados por padrão no backend e no agente. A capacidade deve constar em heartbeat recente e corresponder ao endpoint, à identidade e à versão. Esta mudança não altera os jobs básicos nem o agente; a execução real permanece pendente.

## Escopo restrito

Para homologação futura, configurar **juntos** `NIGHTOWL_OPERATIONAL_ACTIONS_ENABLED=true`, `NIGHTOWL_APPROVED_SCRIPTS_ENABLED=true`, `NIGHTOWL_OPERATIONAL_ACTION_SCOPED_ONLY=true` e `NIGHTOWL_OPERATIONAL_ACTION_SCOPES` no ambiente do backend. O último é JSON, por exemplo (substituir os dois marcadores após verificação por UUID, nunca por hostname):

```json
{"c97045f2-35ff-48f1-afa5-095173fbb3dd":{"machine_id":"<MACHINE_ID_COMPLETO_VERIFICADO>","operator_ids":["<ID_NUMERICO_DO_OPERADOR>"],"actions":["query_service","run_approved_script","flush_dns"],"scripts":["environment_diagnostic:1:8791bd2ddb422912abfa6c8909062b7c386fc5645bd7fc801992c4f816293521"]}}
```

Sem `SCOPED_ONLY=true`, mantém-se o comportamento legado: não utilizar essa combinação para o piloto. Com a restrição ativa, escopo vazio ou inválido, outra identidade, operador ou ação falham fechados; a verificação é repetida ao entregar e iniciar o job. As permissões usuais, flags, validação de catálogo, hash e capabilities continuam obrigatórias. `query_service` exige `agents.view_agentmachine`; `flush_dns`, `agents.change_agentmachine`; ambas também exigem usuário técnico ativo e `agents.add_agentjob`. O script exige `agents.view_agentmachine`, aprovação e não revogação. A política permite consultar o estado de serviços por nome, sem comando livre.

Na VM, após autorização operacional posterior e backup da configuração protegida, preservar todos os valores existentes e acrescentar `operational_action` em `allowedJobTypes`; habilitar `operationalActionsEnabled`, permitir somente `query_service`, `run_approved_script` e `flush_dns` em `allowedOperationalActions`, habilitar `approvedScriptsEnabled` e permitir apenas `environment_diagnostic:1` em `allowedScriptVersions`. Validar heartbeat e capabilities exatas antes do teste. Verificar que o serviço tem privilégio para `flush_dns`; não supor sucesso. Não imprimir tokens nem substituir a configuração inteira por este exemplo. A RC46 já contém esses componentes no código; confirmar binário e hash instalados antes de afirmar compatibilidade real. Nenhuma configuração foi aplicada nesta tarefa.

## Homologação e reversão

1. Confirmar UUID, machine_id completo, usuário técnico, permissões, fila sem job conflitante e capabilities. Recarregar a página após a mudança de capabilities: a disponibilidade de scripts é calculada ao abrir a página. Testar um endpoint e um operador fora do escopo: ambos devem ser rejeitados.
2. Executar `query_service` com nome válido; verificar enfileiramento, entrega, início, resultado tipado, ACK e auditoria, sem repetição após novo pull.
3. Executar `environment_diagnostic` com ID/versão/hash fixos e sem parâmetros; verificar saída, resultado, ACK, revogação e timeout, sem aceitar scripts externos.
4. Executar `flush_dns` apenas na VM autorizada; confirmar resultado real e ACK. Não interpretar despacho como conclusão.
5. Após homologação separada, avaliar `gpupdate` somente em endpoint de domínio elegível e controle de serviços somente com nomes explicitamente autorizados em `NIGHTOWL_ADMIN_ACTION_SCOPES` e `allowedServiceOperations`, além da flag e permissões administrativas. O escopo deste piloto não os autoriza. A associação ao domínio requer evidência verificável; esta implementação não a infere pelo hostname.
6. Para encerrar o piloto, desabilitar as flags e retirar o escopo pelo procedimento operacional autorizado; jobs ainda não iniciados são revalidados. Preservar resultados e ACKs de execuções já iniciadas. Mudanças de ambiente exigem aplicação consistente em todos os workers e revisão do plano de rollback.

Não habilitar a restrição seletiva sem conferir que `SCOPED_ONLY=true` é efetivo em todos os workers **antes** de liberar as flags. A ausência deliberada dessa opção preserva a compatibilidade das instalações antigas e, com flags ligadas, não limita a frota. A implantação exige revisão e reinício controlado do backend; não executados aqui. Se for necessária mudança no executável ou na configuração padrão do agente, produzir uma nova release assinada, sem substituir a RC46 publicada.
