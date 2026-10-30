# DEC-04C.1 — catálogo operacional

Implementado somente `flush_dns` v1 e `query_service` v1, no job
`operational_action`. Sem telas, shell, scripts, processos filhos ou novo canal.

Contrato (campos extras rejeitados):
```json
{"action_id":"query_service","action_version":1,"parameters":{"service_name":"EventLog"}}
```
`flush_dns` recebe `parameters: {}`. O nome de serviço deve corresponder a
`[A-Za-z0-9_.-]{1,256}`. Identificadores e versões são exatos; versões futuras
não são aceitas implicitamente. O resultado v1 contém identificador, versão e
`success: true`; a consulta acrescenta somente `service_name` e `state`.

## Ativação e autorização

- Backend: `NIGHTOWL_OPERATIONAL_ACTIONS_ENABLED=true` (padrão: false).
- Agente: `operationalActionsEnabled: true`, `operational_action` em
  `allowedJobTypes` e IDs individuais em `allowedOperationalActions`.
  Padrões: desativado/lista vazia; sem migração que habilite endpoints existentes.
- Solicitação: POST autenticado por sessão, com CSRF, em
  `/api/agent/operational-actions/<endpoint_id>/`. Exige usuário técnico ativo,
  `agents.add_agentjob` e `agents.view_agentmachine` para consulta ou
  `agents.change_agentmachine` para flush. Staff sozinho não autoriza.
- Criação, entrega e confirmação de início revalidam autorização, identidade,
  flag e capacidade recente `operational_action:<id>:1`. Entrega somente v2.
  O caminho genérico de criação pelo dashboard rejeita esse tipo.
- O agente valida novamente o contrato e sua política local antes de executar.
  `flush_dns` exige token Windows elevado e chama `DnsFlushResolverCache`;
  `query_service` consulta somente status no SCM local. Sem conta, caminho de
  executável ou mensagens nativas brutas na saída.

## Execução e compatibilidade

Mesmo dispatcher, executor, journal, fila durável, ACK, auditoria e recuperação.
Categoria exclusiva e retenção de resultado crítico. Duplicatas e recuperação
não repetem operações cujo resultado local já seja final; execução interrompida
mantém a recuperação existente, sem fabricar sucesso.

Timeout entre 5 e 15 segundos, com teto de 15 no backend e no agente. APIs
nativas síncronas não são abortadas à força: se ultrapassarem o orçamento,
a posse permanece até retornarem e o resultado é timeout, conforme o mecanismo
existente. Saída de esquema fechado inferior a 1 KiB, dentro do limite geral
de 64 KiB. Nenhum processo filho é criado. Cadências não foram alteradas.

Migração aditiva `0040_operational_actions`: somente escolhas de `AgentJob`.
Nenhum modelo novo. Jobs antigos continuam no contrato anterior.

## Validação desta etapa

- Build Release dos projetos .NET afetados, sem restore/download: passou,
  zero avisos/erros.
- Modo `--operational-actions-only`: contrato, autorização local/admin,
  parâmetros e versões inválidos, duplicatas, erros sanitizados, timeout,
  posse, recuperação e compatibilidade da política antiga: passou.
- Django: `agents.test_operational_actions` e `agents.test_job_control`.
  Incluem autorização/CSRF, revogação, capacidade, confirmação de saída,
  timeout e replay de ACK: 27 testes passaram, além de 1 integração local.
- Integração loopback: dispatcher/executor .NET real → consulta nativa de
  `EventLog` → resultado Django persistido → ACK. SQLite isolado; não valida
  concorrência PostgreSQL. Flush validado com adaptador simulado, sem limpar
  o cache DNS da máquina de desenvolvimento.
- `git diff --check`: passou.

Aviso preexistente: diretório `staticfiles` ausente no ambiente de testes,
sem impacto nesses contratos. Sem impedimento funcional em DEC-04C.1;
as limitações de timeout nativo e validação estão descritas acima.

Arquivos: agente (`OperationalActions.cs`, `JobExecutionPolicy.cs`,
`JobExecutionCoordinator.cs`, `JobExecutor.cs`, `AgentConfig.cs`, `Worker.cs`);
backend (`agents/operational_actions.py`, `models.py`, `urls.py`, `views.py`,
`job_control.py`, migração `0040_operational_actions.py`, `config/settings.py`,
`dashboard/views.py`); testes (`OperationalActionTests.cs`, `Program.cs`,
`JobRuntimeTests.cs`, `agents/test_operational_actions.py`) e este documento.

Sem suíte completa, ferramentas instaladas, push, deploy ou instalação em
endpoints. DEC-04C.2, DEC-04C.3 e DEC-04D não iniciadas.
