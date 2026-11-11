# RC47-04A — Políticas de Execução

## Base e arquitetura existente

Esta entrega parte da `main` `c1ff295a3cbe4e8e846532f5b5014fa79ac1f12d`. A auditoria encontrou a infraestrutura operacional já implementada em `AutomationDefinition`, `AutomationOccurrence`, `AgentJob`, na API autenticada de automações e no scheduler existente. A definição guarda tipo de job allowlisted, payload tipado, alvos com snapshots de UUID, `machine_id` e hostname, agenda, fuso, validade, política offline, revisão e estado. As ocorrências preservam resultado/motivo e apontam para o `AgentJob` real.

O scheduler existente continua sendo responsável por materializar ocorrências; `AgentJob` e o protocolo atual continuam responsáveis pela fila, execução Windows, ACK e resultado. Não foram criados modelos, migrations, fila, scheduler ou executor adicionais. Salvar política futura não cria job; a opção imediata continua sendo uma ação explícita, confirmada na interface e autorizada pelo contrato existente.

## Organização e navegação

Foi criada uma entrada independente **Políticas de Execução**, que abre `/jobs/?tab=automations` e ativa a aba existente de automações. A página **Tarefas** continua disponível e a navegação **Políticas de Software** permanece separada e sem alteração de contrato. A experiência central reutiliza formulário, filtros, revisão otimista, pause/resume/cancel, execução imediata autorizada, cancelamento pré-despacho e histórico persistido.

A tabela central mostra finalidade pelo nome descritivo da definição, tipo de job permitido, endpoints guardados na definição, recorrência/fuso, próxima execução, última ocorrência real, estado, endpoint e motivo de falha/bloqueio. O resumo de última ocorrência é obtido do banco por subqueries limitadas às 200 definições retornadas; não inventa estado quando não há ocorrência. A tabela de ocorrências continua sendo a fonte detalhada para resultados e links dos `AgentJob`.

O modelo não possui campo separado de descrição/finalidade. Para evitar uma migration sem necessidade demonstrada, o nome continua sendo o campo descritivo; a interface orienta o operador a registrar nele a finalidade. Scripts arbitrários e payloads livres seguem rejeitados pela allowlist/validação atual.

## Segurança e compatibilidade preservadas

- Permissões técnicas e permissões específicas de ver, editar/controlar e executar permanecem validadas no backend.
- Endpoints permanecem selecionados por UUID e snapshot de identidade; validações existentes do scheduler continuam bloqueando identidade alterada/ambígua, quarentena, endpoint offline conforme política e falta de capability.
- CSRF, revisão otimista, idempotência, auditoria e cancelamento apenas antes do despacho permanecem nos contratos atuais.
- A separação entre Jobs e scheduler de telemetria, assim como as flags desabilitadas por padrão, não foi alterada.
- Nenhum job real foi executado por esta alteração.

## RC47-04B

A próxima etapa poderá apresentar políticas atribuídas na página do endpoint e oferecer ações manuais ali, mas deve reutilizar as definições e ações existentes, preservar UUID/`machine_id`, permissões, revisão e auditoria, e não criar segundo fluxo de execução. Esta PR não altera `endpoint_detail.js`, a aba Monitoramento, flags ou configuração de produção.

## Validação

Validação local: `agents.test_automation` e `dashboard.test_execution_policies` passaram em SQLite; `manage.py check`, `makemigrations --check --dry-run`, `node --check` e `git diff --check` passaram. O gate browser Windows instala o cliente Node Playwright fixado em `1.63.0`, sem baixar outro navegador; os testes reais usam o Chrome for Testing pinado por `NIGHTOWL_TEST_CHROME`. O gate browser isolado chama tanto o smoke de terminal quanto `AutomationBrowserTests.test_real_ui_and_persisted_automations`, com banco Django de teste, endpoints sintéticos e usuário temporário não administrativo. Não foram executados jobs reais, ativadas flags ou alterada configuração operacional.
