# RC47 — Gerenciador de Serviços Windows, Fase B

## Escopo

A Fase B acrescenta iniciar, parar e reiniciar serviços elegíveis dentro do modal
do Gerenciador de Serviços da Fase A. Consulta, despacho e acompanhamento usam
o `AgentJob` existente, o contrato `operational_action` e a execução nativa já
implementada em `AdministrativeActions.cs`. Não foi criado scheduler, canal,
executor Windows ou migration novo.

O agente Windows não foi alterado. A Fase B não altera tipo de inicialização,
propriedades, drivers, comandos arbitrários, GPO ou outros serviços.

## Autorização fail-closed

Uma operação só pode ser criada quando todos os gates aplicáveis forem válidos:

- `NIGHTOWL_SERVICE_CONTROL_ENABLED` (novo) está explicitamente habilitado;
- `NIGHTOWL_OPERATIONAL_ACTIONS_ENABLED` e `NIGHTOWL_ADMIN_ACTIONS_ENABLED`
  estão habilitados;
- o usuário é técnico, tem `agents.add_agentjob` e a permissão específica
  `agents.start_windows_service`, `agents.stop_windows_service` ou
  `agents.restart_windows_service`;
- endpoint está ativo e online, possui `machine_id` único e não está em ciclo
  de vida terminal;
- heartbeat recente confirma `operational_action:<ação>:1` e a identidade;
- `NIGHTOWL_ADMIN_ACTION_SCOPES` contém o UUID exato do endpoint, o ID do
  operador autorizado em `operator_ids`, e permite o nome interno do serviço
  para aquela ação;
- o agente instalado anuncia e aplica seu próprio `administrativeActionsEnabled`
  e `allowedServiceOperations` locais;
- não existe outro job queued/sent/running no endpoint.

O escopo de start/stop/restart sempre exige `operator_ids` válido e não vazio,
mesmo quando `NIGHTOWL_OPERATIONAL_ACTION_SCOPED_ONLY=False`. O operador precisa
estar explicitamente listado no escopo do UUID. Isso não altera a política
opcional de escopo dos demais jobs nem o contrato de `gpupdate`.

Os defaults permanecem fechados: `NIGHTOWL_SERVICE_CONTROL_ENABLED=False`,
`NIGHTOWL_ADMIN_ACTIONS_ENABLED=False`, scopes vazios e ações operacionais
regidas pela configuração existente. Nenhuma flag ou allowlist operacional
foi habilitada nesta implementação.

Exemplo estrutural para futura aprovação — não copiar para produção sem revisão:

```dotenv
NIGHTOWL_SERVICE_CONTROL_ENABLED=True
NIGHTOWL_ADMIN_ACTIONS_ENABLED=True
NIGHTOWL_OPERATIONAL_ACTIONS_ENABLED=True
NIGHTOWL_ADMIN_ACTION_SCOPES={"<endpoint-uuid>":{"operator_ids":["<django-user-id>"],"start_service":["AcmePilotService"],"stop_service":["AcmePilotService"],"restart_service":["AcmePilotService"]}}
```

O serviço só deve ser incluído nas listas das ações que o técnico está
autorizado a executar. UUIDs e nomes reais pertencem à configuração protegida
do ambiente, nunca ao código-fonte.

## Contrato e execução

- `POST /api/agent/service-manager/<endpoint-uuid>/actions/`
  aceita exatamente `request_id`, `action_id` e `service_name`.
- `request_id` UUID é usado como chave idempotente por operador, endpoint e
  `AgentJob`; repetição idêntica retorna o job existente e conteúdo diferente
  é rejeitado.
- O job continua sendo `operational_action` versão 1 e passa pelos validadores
  atuais de criação, despacho, início do agente, ACK e auditoria.
- Pull e start revalidam flag, permissão, escopo, identidade, capability e
  conflito. Um job ainda não despachado é cancelado/auditado se a autorização
  for revogada; um job entregue que ainda não iniciou é recusado no endpoint
  `/started/` se a revalidação falhar.
- `GET /api/agent/service-manager/<endpoint-uuid>/actions/<job-uuid>/` retorna
  somente estado e horários do job criado pelo operador. `completed` só é
  apresentado como confirmado quando identidade, serviço, ação e estado final
  passam pelo validador existente. Erros são códigos sanitizados, sem stderr,
  credenciais ou saída arbitrária.
- A confirmação humana mostra ação, nome interno do serviço e endpoint antes
  do POST. A interface não trata criação do job como sucesso e consulta
  novamente a lista após confirmação real.

O agente rejeita serviços protegidos, não próprios, drivers e recursos cujo
executável não passe pelas validações de segurança atuais. Start exige serviço
parado; stop exige serviço em execução; restart exige serviço em execução.
Estados desconhecidos ou pendentes não oferecem botões acionáveis. Dependências
que não estejam no estado esperado provocam falha real, nunca sucesso inferido.

## Auditoria

O `job.created` existente registra operador, endpoint/identidade, payload
tipado, autorização vinculada, UUID do job e request id. Eventos existentes de
despacho, início e resultado preservam status, ação, serviço, duração e erro
sanitizado. Nenhuma credencial é recebida ou persistida por este fluxo.

## Release e dependências da Fase A

Esta PR não muda código C# nem protocolo Windows, portanto não exige por si só
um novo release do agente. Antes de pilotar, confirmar em heartbeat que o
binário instalado anuncia as três capabilities `operational_action:start_service:1`,
`operational_action:stop_service:1` e `operational_action:restart_service:1`,
e que a consulta paginada da Fase A (`collect_services_paged:2`) está disponível.
Se o executável instalado não contiver esses contratos, será necessário gerar
e validar uma release assinada compatível pelo processo oficial; esta PR não a
gera nem publica.

A identidade duplicada anteriormente observada para `CS-CVEL-0281` continua
sendo bloqueio: não operar até que a identidade correta esteja demonstrada e
resolvida pelo procedimento operacional autorizado. A interface não reconcilia
registros por hostname.

## Checklist para futura homologação controlada

1. Confirmar release/hash Windows e heartbeat recente com `machine_id` e as
   capabilities exatas; confirmar também `collect_services_paged:2`.
2. Resolver documentalmente a ambiguidade de identidade do endpoint; não
   autorizar consulta ou controle por hostname.
3. Em backend isolado/piloto aprovado, cadastrar um UUID e um único serviço
   próprio não crítico em `NIGHTOWL_ADMIN_ACTION_SCOPES`, com ações mínimas.
4. Configurar no agente somente as ações e o serviço aprovados, além do
   requisito de execução local administrativa; não presumir que o serviço do
   agente possui privilégio suficiente.
5. Revisar permissões Django do operador; confirmar que cada ação precisa de
   permissão específica.
6. Habilitar as flags somente para a janela e ambiente formalmente aprovados.
7. Validar primeiro serviço parado → iniciar, serviço executando → parar e
   serviço executando → reiniciar, um por vez, com confirmação e registro do
   estado retornado pelo agente.
8. Confirmar `job.created`, despacho, `job.started`, ACK e estado real em nova
   consulta; testar revogação antes do pull e antes de `/started/` em ambiente
   não produtivo.
9. Revogar scope e flags após o teste; conferir que jobs pendentes não iniciam.

Não foram executadas operações remotas nem testes de serviços reais nesta fase.
