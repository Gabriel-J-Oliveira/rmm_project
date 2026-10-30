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
endpoints nesta etapa inicial. DEC-04C.2 segue abaixo; DEC-04C.3/04D não iniciadas.

## DEC-04C.2 — biblioteca mínima de scripts aprovados

Somente `environment_diagnostic` v1: UTC e versão do Windows PowerShell 5.1,
sem parâmetros e sem mudanças de configuração no Windows. Registro congelado em
`agents/script_library.py`, fonte em `agents/approved_scripts/`, recurso embutido
no agente e pin independente nas duas implementações. SHA-256:
`8791bd2ddb422912abfa6c8909062b7c386fc5645bd7fc801992c4f816293521`.
`.gitattributes` fixa LF para os bytes permanecerem idênticos entre checkouts.
Não há upload, edição ou aprovação por API; nova versão exige alteração revisada
do código e futura release autenticada. Não há migration ou modelo novo.

Consulta: GET `/api/agent/approved-scripts/`, sessão autenticada, usuário técnico
ativo e `agents.view_agentmachine`. Retorna versão/hash, linguagem/runtime,
descrição, esquema vazio, aprovação/revogação, permissão e limites. Execução usa
o POST da DEC-04C.1 com o job existente `operational_action`:

```json
{"action_id":"run_approved_script","action_version":1,"parameters":{"script_id":"environment_diagnostic","script_version":1,"sha256":"8791bd2ddb422912abfa6c8909062b7c386fc5645bd7fc801992c4f816293521","parameters":{}}}
```

Backend exige gates operacionais anteriores, `NIGHTOWL_APPROVED_SCRIPTS_ENABLED`
(false por padrão), usuário técnico ativo com add_agentjob + view_agentmachine,
identidade inequívoca sem quarentena, capacidade recente com versão/hash e ausência
de outro job ativo. Revogação configurável em `NIGHTOWL_REVOKED_SCRIPT_VERSIONS`
(ex.: `environment_diagnostic:1`) é revalidada na criação, entrega e início.
Um resultado já executado ainda pode ser persistido/ACKado após revogação, sem
autorizar nova execução. Catálogo não muda silenciosamente uma versão aprovada.

Agente exige, além dos gates da DEC-04C.1, `approvedScriptsEnabled: true` (default
false), `run_approved_script` em allowedOperationalActions e a versão exata em
`allowedScriptVersions: ["environment_diagnostic:1"]` (default vazio). Não há
migração habilitando esses campos. Capacidade inclui ID/versão/hash. Conteúdo
remoto e hash escolhido pelo solicitante nunca são raiz de confiança: somente
o recurso embutido cujo pin coincide com a versão conhecida pode executar.

O PowerShell do sistema é escolhido por caminho absoluto, sem profiles, shell
intermediário, argumentos livres ou bypass de ExecutionPolicy. Recebe somente o
script fixo via stdin, após associação a Job Object kill-on-close com limite de
um processo; criação de filhos é bloqueada. Ambiente herda somente caminhos de
sistema necessários, não tokens/configuração do serviço. A linha vazia final é
framing necessário ao parser de stdin do PowerShell, não conteúdo fornecido pelo
operador. Limites: 15 s pelo orçamento existente, stdout+stderr de até 4096 bytes
durante leitura, concorrência exclusiva existente. Cancelamento/timeout encerra
o processo possuído; runtime/journal, recuperação, fila crítica e ACK são os mesmos
da DEC-04A. Agentes antigos não recebem este contrato; scheduler DEC-04B não foi
alterado e não admite esse tipo de operação em agendas.

Saída válida é reserializada em esquema fechado; stdout contém apenas UTC/versão,
stderr vazio no sucesso. Falhas descartam texto bruto e registram categoria segura,
exit code real quando disponível e duração. Identificador/hash/solicitante/alvo
ficam no job/auditoria, resultado e receipt. Isolamento de processos **não reduz
privilégios SYSTEM**: não existe sandbox de privilégio restrito. Scripts
administrativos exigem desenho de token/conta restrita ou broker, política de
acesso e homologação próprios; não estão autorizados por esta implementação.

Validação local: build Release afetado (zero avisos/erros), testes
`--approved-scripts-only`, `--operational-actions-only`, `--jobs-only`; 69 testes Django
script_library + operational_actions + job_control + AutomationTests; check,
makemigrations dry-run e git diff --check. Inclui script real em PowerShell5.1,
hash/versão/parâmetros inválidos, aprovação/revogação, CSRF/permissões, identidade,
cancelamento/timeout, saída limitada, filhos bloqueados, sanitização, duplicatas
e integração loopback Django → executor → resultado persistido → ACK. SQLite
isolado não comprova concorrência PostgreSQL nem execução sob SYSTEM em endpoint.
Sem suíte completa, ferramentas novas, ações em estações, push ou publicação.

Resíduo local de uma primeira fixture com ambiente de runtime incompleto:
`%SystemDrive%/` (cache Windows, não código). A remoção foi bloqueada pela política,
não foi contornada e o diretório fica fora do commit. Variáveis de sistema foram
corrigidas; a nova execução não atualizou esses arquivos. Encerrar em DEC-04C.2;
não avançar automaticamente para DEC-04C.3/04D.

Arquivos DEC-04C.2: agente (`ApprovedScripts.cs`, `OwnedProcess.cs`,
`OperationalActions.cs`, `JobExecutor.cs`, `AgentConfig.cs` e csproj); testes .NET
(`ApprovedScriptTests.cs`, `JobRuntimeTests.cs`, `Program.cs`); backend
(`agents/script_library.py`, `agents/approved_scripts/environment_diagnostic_v1.ps1`,
`agents/operational_actions.py`, `agents/urls.py`, `config/settings.py`);
`agents/test_script_library.py`, `.env.example`, `.gitattributes` e este documento.

## DEC-04C.3 — ações administrativas tipadas

Continuação local sobre `31452e21`, sem sincronização automática. Usa o mesmo
`operational_action` v1, endpoint POST, dispatcher/journal/resultados críticos e
ACK; não cria shell, serviço, canal, tela ou agenda administrativa.

| Ação | Parâmetros exatos | Execução nesta etapa |
|---|---|---|
| start_service / stop_service / restart_service | service_name | SCM local via ServiceController; implementação nativa, testes simulados |
| gpupdate | {} | RefreshPolicyEx(machine=true, RP_FORCE); apenas solicitação, nunca prova de processamento |
| create_local_user | user_name, secret_reference (UUID) | Bloqueada: entrega protegida de segredo indisponível |
| reset_local_password | user_name, account_sid, secret_reference | Bloqueada pelo mesmo motivo |
| set_local_user_enabled | user_name, account_sid, enabled (bool) | Bloqueada: falta isolamento/validação nativa de identidade e privilégios da conta |

Senhas nunca são parâmetros aceitos. Referências não são emitidas/resolvidas nem
garantem por si uso único/TTL: esse mecanismo ainda não existe. O backend não cria
jobs de contas e nenhuma API de conta Windows é chamada, mesmo com flags/permissões
habilitadas. No agente só pode persistir o resultado sanitizado de uma rejeição,
nunca o payload de conta rejeitado ou senha recebida em campo extra.
Nomes de domínio, contas conhecidas protegidas e SIDs de sistema/RID<=1000 são
rejeitados; isso não substitui prova nativa de SAM/grupos, ainda pendente.

Defaults: `NIGHTOWL_ADMIN_ACTIONS_ENABLED=false`, `NIGHTOWL_ADMIN_ACTION_SCOPES={}`;
agente `administrativeActionsEnabled=false`, `allowedServiceOperations={}`,
`allowComputerPolicyRefresh=false`, além dos gates anteriores. Escopo backend:
UUID do endpoint → ação → lista positiva de serviços (ou true para gpupdate).
Exemplo apenas estrutural: `{ "<endpoint-uuid>": {"start_service":["AcmePilotService"],"gpupdate":true} }`.
No agente: `allowedServiceOperations: {"AcmePilotService":["start_service"]}`.
Não habilitar esses exemplos sem homologação e aprovação por alvo.

Migração aditiva `0041_administrative_action_permissions`: sete permissões em
AgentJob, uma por operação; nenhuma é concedida automaticamente. Exige add_agentjob
mais a permissão específica, usuário técnico ativo, identidade sem conflito/quarentena,
capacidade recente e recurso autorizado. A criação audita vínculo congelado do ID
do operador/endpoint/identidade/ação/parâmetros/timeout/expiração. Entrega e início
revalidam esse vínculo e a autorização atual; mudança de parâmetros, alvo, usuário
ou escopo bloqueia a execução. O agente valida novamente sua própria allowlist.

Serviços: bloqueia alvos críticos conhecidos e NightOwl, drivers/shared-process,
launch protegido, binário sob Windows, imagem com argumentos/expansões e reparse
points. Somente serviços own-process de terceiros revisados, na allowlist. Stop
não para dependentes; start exige dependências já running. Restart exige running
inicial e pode deixar estado parcial se falhar: não há rollback fictício. Revisar
ACLs/configuração/binário dos serviços piloto antes de habilitar. Não há sandbox
ou redução de privilégios SYSTEM; autorização é por operação/recurso fechado.

Orçamento existente de 5..15 s, exclusão mútua e idempotência preservados. Esperas
cooperam com cancelamento, mas uma chamada nativa já iniciada ou atualização de GPO
aceita não pode ser desfeita por cancelamento. Posse fica retida até retorno nativo;
15 s não é SLA de retorno de API bloqueada. Falha/timeout exige reconciliação,
nunca replay automático da mutação. Saída fechada e curta, sem erros nativos brutos.
Admissão de operational_action valida antes de persistir payload no journal; teste
sentinela confirma que pedido contendo senha rejeitada não chega a arquivos/resultados.

Validação: build Release afetado (zero avisos/erros); modo
`--administrative-actions-only` com mocks, guards de recurso, rejeição de contas,
privilégio, timeout/cancelamento, recuperação e rejeição antes do journal/ACK;
modos de ações/scripts/jobs anteriores aprovados. 77 testes Django selecionados
aprovados, incluindo permissões/vínculo/revogação, resultados e integrações reais
locais de leitura (query_service e script diagnóstico) até persistência/ACK. Check,
makemigrations dry-run e git diff --check aprovados. Sem suíte completa/dependências
novas. Mutações administrativas não foram executadas no Windows de desenvolvimento.
SCM/GPUpdate reais sob SYSTEM, serviços piloto e PostgreSQL exigem homologação
descartável posterior; credenciais TTL/uso único e contas locais continuam bloqueadas.

Arquivos: AdministrativeActions.cs, OperationalActions.cs, JobRuntime.cs,
AgentConfig.cs, AdministrativeActionTests.cs e Program.cs; backend
agents/administrative_actions.py, operational_actions.py, models.py, teste
test_administrative_actions.py e migração0041; settings/.env.example e este documento.
Sem push, merge, deploy, publicação, instalação ou avanço para DEC-04D.
