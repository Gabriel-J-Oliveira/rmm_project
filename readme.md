# NightOwl RMM - Contexto Atual

O NightOwl integra gestao de endpoints Windows, releases/jobs, Asset
Intelligence / OBZ, discovery Active Directory, instalacao do agente e Desk.
O contexto do Desk abaixo foi preservado; nao descreve sozinho o repositorio.

## Atualizacao de 2026-10-11

Estado de referencia: backend/producao permanece em `72d7a5920febd390bac8bdde6083b0c4291ee6fc`.
O branch `codex/agent-telemetry-management` contem trabalho local mais recente;
seus commits ainda nao significam deploy em producao. A correcao do repair
runner passou a suite Windows apos diagnostico de um `Test-Path` negado em
`ProgramData`; RC46 ainda nao foi empacotada, assinada, registrada ou publicada.

- **Telemetria remota:** o trabalho no branch permite configurar, por job
  autenticado `configure_telemetry`, somente habilitacao e os intervalos de
  amostragem/envio. O agente persiste a configuracao e confirma o estado
  efetivamente aplicado; a UI do endpoint distingue aplicacao de recebimento
  posterior de amostras. RC45 nao suporta esse job. Nao foram despachados jobs
  de configuracao nem iniciado rollout.
- **Gate RC46:** a causa do teste `TestRepairRunnerScriptPersistsCompletedResult`
  foi uma leitura observacional do caminho canonico de configuracao que podia
  lancar `UnauthorizedAccessException` e impedir a persistencia do resultado.
  O ajuste trata somente essa leitura inacessivel como ausencia; os gates de
  health do servico permanecem. A suite Windows final, testes compartilhados,
  updater, uninstaller e scripts PowerShell executados passaram. O builder
  oficial completo (incluindo etapa de pacote/assinatura) ainda nao foi rodado;
  portanto o gate de release nao deve ser considerado completo.
- **Roadmap OBZ:** M1–M6 possuem implementacao/backend/UI e validacoes
  registradas nas secoes historicas abaixo e em `DEPLOY.md`. O proximo ciclo e
  piloto controlado M7; ampliar coleta (M8), observar dataset de sete dias (M9)
  e fechar o MVP com relatorio confiavel (M10) continuam gates futuros, nao
  autorizacoes implicitas de rollout.
- **Instalacao remota / AD:** discovery P1/P2, preflight P3 e instalacao unitaria
  P4 permanecem sujeitos ao historico de operacoes com resultado desconhecido.
  A documentacao operacional deve ser consultada antes de qualquer nova
  tentativa; nao inferir que uma ausencia de heartbeat prova que instalacao
  nenhuma ocorreu.

Este resumo nao declara RC46 publicada, telemetria habilitada em frota,
encerramento do piloto de instalacao remota ou conclusao do MVP OBZ.

## Base operacional registrada em 2026-10-05 (historico)

Base funcional implantada e validada:
`67afff528532546114bc12bec7a90a0ba29d9af0`.
Producao em CS-HOST-INFRA, `/opt/nightowl`, Django/PostgreSQL, Gunicorn,
Nginx/HTTPS e `nightowl.service`. Os resultados abaixo sao gates datados,
nao uma promessa de estado perpetuo do ambiente.

### Asset Intelligence / OBZ

- Telemetria leve RC41 e inventario hardware M1 enriquecido; core isolado de
  falhas de enriquecimento. Recovery RC43 e ponte RC44 trataram problemas de
  healthcheck com updater legado encontrados no laboratorio.
- Persistencia backend de jobs de inventario endurecida, incluindo rejeicao
  de patches contendo somente metadata, sem rejeitar scan vazio legitimo.
- M4: agregacoes, fatos de qualidade e API summary.
- M5: evidence, diagnosticos CPU/RAM e capacity multi-window; gate real M5.4.
  Primary e obrigatoria, context nao a substitui; hardware e contexto, nao gate.
  Assessment tecnico nao constitui sizing, compra, downgrade ou decisao financeira.
- M6: tela Capacidade & Planejamento e drawer com contexto do endpoint.

### Instalacao do Agente / AD

P1 reutiliza `config/ad_ldap.py` para bind/TLS/timeout e busca de computadores
com base separada da busca de usuarios. Discovery e read-only, paginado,
sanitizado e efemero, sem ADComputer model ou segundo conjunto de credenciais.
P2 acrescenta correlacao exata, exclui lifecycle terminal da correlacao ativa
e falha fechado em ambiguidades/domain mismatch. DNS usa executor global com
12 workers e admission global de 24 tarefas; a UI possui testes XSS permanentes.
ADUser pode enriquecer username confiavel, nao inferir sessao ativa.

P3/P4 oferecem preflight e instalacao remota unitaria, com alvo derivado do AD
server-side, WinRM HTTPS/5986, validacao TLS, redirects bloqueados e protecoes
SSRF. Preflight remoto autenticado e instalacao sao operacoes distintas:
instalacao pode modificar o Windows e criar historico persistente.
Selecao de release e server-side; o backend valida assinatura/hash e contrato
do installer congelado. Tokens, instalacao manual avancada e selecao unitaria
foram reorganizados na UI.

### Trust WinRM implantado

Producao configura explicitamente no `.env`:
`WINRM_CA_TRUST_PATH=/etc/ssl/certs/ca-certificates.crt`.
Pywinrm recebe ca_trust_path explicito e server_cert_validation=validate.
Nao ha fallback para HTTP/certifi nem verificacao TLS desabilitada.
O bundle dedicado anterior permanece no filesystem e como default do codigo;
nao confundir esse default com a configuracao efetiva de producao.

O fix `67afff5` aceita um bundle carregavel com raiz expirada nao utilizada,
sem enfraquecer verificacao da cadeia, validade e hostname do servidor.
As duas geracoes da CA corporativa continuam no trust. Validacao pos-deploy:
servico/HTTPS PASS, pagina de instalacao PASS, discovery 304 computadores,
TCP 5986 e handshake TLS sem autenticacao de CS-RDP-02 PASS.
Gates: 136 testes Django e 15 UI/XSS PASS; check/makemigrations/diff PASS.
Nao houve autenticacao WinRM, comando Windows ou instalacao nesse deploy.

### Bloqueio Atual e Proximos Passos

O piloto CS-RDP-02 ainda nao esta encerrado. O job anterior
`ee2fd6d4-e8cb-4553-8f89-ee345e62cdf5` permanece OUTCOME_UNKNOWN, com
INSTALLER_FAILED e diagnosticos historicos insuficientes. O bloqueio
INSTALL_ALREADY_ATTEMPTED vem desse historico terminal, mesmo sem job ativo.
Ausencia de endpoint no backend nao prova ausencia de alteracoes no Windows.

Proximo gate: evidencia local autorizada e reconciliacao administrativa
auditada do job antigo; somente depois, preflight manual e eventual instalacao
unitaria explicitamente autorizada. Nao apagar historico, repetir installer
automaticamente ou habilitar telemetry/rollout em decorrencia do deploy de trust.
Validar depois identidade, heartbeat, inventario e resultado real do installer.

Historico, backups e gates: `DEPLOY.md`. Contrato de trust:
`docs/winrm-ca-trust.md`; bootstrap piloto: `docs/winrm-https-pilot.md`;
installer: `docs/remote-installer-contract.md`. O roadmap `deployMVP.md` contem
registros historicos de RC42 ainda nao consolidados; consultar o estado atual
acima e o DEPLOY antes de executar proximas etapas.

## NightOwl Desk - Contexto do Modulo de Chamados

Este repositório contém o NightOwl Desk, um módulo Django para centralizar atendimento técnico, chamados de helpdesk, contexto de endpoint/RMM, comentários, anexos, auditoria e configurações operacionais do Desk.

O módulo de chamados vive principalmente em:

- `tickets/`
- `templates/tickets/`
- `static/css/nightowl.css`
- `config/urls.py` e `tickets/urls.py`

## Objetivo do módulo

O NightOwl Desk foi evoluído para ser uma bancada operacional do técnico, não apenas uma lista de chamados. O fluxo principal atual é:

1. Central de Atendimento.
2. Abertura rápida de chamado via drawer.
3. Detalhe do chamado.
4. Comentários internos/públicos.
5. Anexos e evidências.
6. Auditoria técnica.
7. Configurações de categorias, filas, SLAs, templates e GMUD.

Parte do módulo já usa backend real em Django/PostgreSQL. Algumas áreas avançadas ainda são visuais/mockadas para preservar a experiência enquanto o backend definitivo é construído.

## Navegação principal

A navegação do Desk foi simplificada para as áreas operacionais principais:

- Central de Atendimento
- Filas
- GMUD
- Dashboard
- Configurações

Itens antigos como `Meus chamados`, `Categorias` e `Automações` foram incorporados como filtros ou abas internas.

## Rotas principais

As rotas do módulo estão em `tickets/urls.py`.

Principais telas:

- `/tickets/` e `/tickets/central/`: Central de Atendimento.
- `/tickets/users/`: área de usuários do Desk baseada em identidades do AD.
- `/tickets/users/<id>/`: perfil operacional do usuário, chamados e endpoints associados.
- `/tickets/new/`: Novo registro avançado.
- `/tickets/<numero>/`: Detalhe do chamado.
- `/tickets/dashboard/`: Dashboard do Desk.
- `/tickets/settings/`: Configurações do Desk.
- `/tickets/portal/`: Portal do solicitante.

Principais APIs internas:

- `POST /tickets/api/tickets/`: cria chamado pelo drawer rápido.
- `POST /tickets/<numero>/api/update/`: altera campos básicos do chamado.
- `POST /tickets/<numero>/api/comments/`: cria comentário interno/público.
- `POST /tickets/<numero>/api/public-conversation/`: atualiza conversa pública.
- `POST /tickets/<numero>/api/attachments/`: envia anexos.
- `POST /tickets/<numero>/api/actions/`: ações operacionais como resolver, reabrir, escalar e assumir.
- `POST /tickets/settings/api/config/`: cria/edita configurações do Desk.

## Models principais

Os models ficam em `tickets/models.py`.

### Ticket

Representa o chamado real.

Campos importantes:

- número sequencial (`number`);
- título e descrição;
- status;
- prioridade;
- categoria;
- solicitante;
- vínculo opcional com `access_inventory.ADUser` em `requester_ad_user`;
- fila;
- responsável;
- endpoint/RMM relacionado;
- SLA e vencimento;
- origem;
- datas de criação, atualização, atribuição, resolução e fechamento.

O `save()` do Ticket:

- atribui número sequencial;
- eleva prioridade para crítica quando o solicitante é sócio/VIP;
- preenche timestamps operacionais;
- calcula `due_at` quando há SLA.

## Área de usuários do Desk

O primeiro MVP da área de usuários conecta a Desk ao inventário de identidades do `access_inventory`.

Fonte primária:

- `access_inventory.ADUser`

Funcionalidades atuais:

- lista real de usuários importados do AD;
- busca por nome, usuário de rede, UPN ou e-mail;
- filtros por ativo/inativo, chamados abertos, endpoint associado e OU;
- perfil do usuário com resumo, chamados reais e histórico de endpoints;
- abertura de chamado a partir do perfil usando o drawer rápido da Central;
- persistência de `requester_ad_user` em chamados criados para usuário AD conhecido;
- comando de backfill `python manage.py link_ticket_requesters` com `--apply` opcional.

Regras de associação:

1. Usa `Ticket.requester_ad_user` quando já existir.
2. Usa `requester_username` contra `ADUser.sam_account_name` ou UPN.
3. Usa `requester_email` contra `ADUser.email`.
4. Nunca associa automaticamente por nome de exibição.

Associação de endpoints:

- compara identidades normalizadas com `AgentMachine.last_logged_user`;
- compara identidades normalizadas com `InventorySnapshot.logged_user`;
- prefere endpoint online;
- entre equivalentes, usa o mais recente por `last_seen_at`/inventário;
- quando há múltiplos endpoints online, o perfil sinaliza o risco operacional.

### TicketComment

Comentários do chamado.

- Pode ser interno ou público.
- Comentários públicos aparecem no portal/conversa do solicitante.
- Comentários internos alimentam a atividade operacional e auditoria.

### TicketAttachment

Anexos reais do chamado.

- Usa `FileField`.
- Guarda nome original, tipo, tamanho, visibilidade e autor.
- Suporta vínculo opcional com comentário.

### TicketAuditEvent

Registro técnico de auditoria.

Guarda:

- ator;
- tipo de evento;
- ação;
- campo alterado;
- valor anterior;
- valor novo;
- metadados JSON;
- data.

É diferente da timeline operacional: auditoria é rastreabilidade técnica.

### TicketCategory, DeskQueue, DeskSLA e DeskTemplate

Configurações reais do Desk.

- `TicketCategory`: categoria, ícone, cor, tipos permitidos, prioridade/fila/SLA padrão.
- `DeskQueue`: fila, responsável, membros, capacidade e tipos aceitos.
- `DeskSLA`: tempos de resposta/resolução e calendário.
- `DeskTemplate`: textos reutilizáveis para abertura, comentários, resolução, escalação, automações e GMUD.

## Central de Atendimento

Template principal:

- `templates/tickets/central.html`

Partials relevantes:

- `_central_header.html`
- `_central_filters.html`
- `_central_kpis.html`
- `_central_ticket_table.html`
- `_central_ticket_row.html`
- `_central_detail_panel.html`
- `_central_quick_ticket_drawer.html`
- `_central_scripts.html`

Funcionalidades atuais:

- Lista chamados reais.
- Mostra KPIs reais básicos.
- Busca e filtros operacionais.
- Filtros rápidos como `Atribuídos a mim`, `Sem responsável`, `Críticos`, `SLA vencendo` e `RMM`.
- Preview lateral do chamado.
- Drawer de abertura rápida.
- Ações visuais sem reload completo.

## Novo chamado rápido

O fluxo principal de criação é o drawer lateral da Central.

Campos principais:

- Solicitante.
- Título.
- Descrição.
- Categoria.
- Prioridade.
- Origem.
- Endpoint opcional.
- Anexos opcionais.

Comportamento atual:

- Cria `Ticket` real no backend.
- Valida campos obrigatórios.
- Sugere prioridade/fila/SLA com base na categoria.
- Cria auditoria `ticket_created`.
- Suporta `Criar` e `Criar e assumir`.
- Permite anexos/prints pendentes.
- Mantém busca de chamados relacionados em modo progressivo.

Ainda existem partes mockadas:

- solicitante rápido sem model próprio definitivo;
- relacionamento persistido entre chamados;
- RMM real do endpoint.

## Novo registro avançado

Rota:

- `/tickets/new/`

Template:

- `templates/tickets/form.html`

É um fluxo avançado e progressivo para:

- Incidente;
- Solicitação;
- Alerta RMM;
- GMUD/Mudança.

Esse fluxo é mais visual/estruturado e ainda não é o caminho principal de criação real.

## Detalhe do Chamado

Template principal:

- `templates/tickets/detail.html`

Partials relevantes:

- `_detail_header.html`
- `_detail_tabs.html`
- `_detail_overview.html`
- `_detail_comment_composer.html`
- `_detail_checklist.html`
- `_detail_activity.html`
- `_detail_sidebar.html`
- `_detail_device.html`
- `_detail_attachments.html`
- `_detail_related.html`
- `_detail_audit_drawer.html`
- `_detail_resolution_drawer.html`
- `_detail_escalation_drawer.html`
- `_detail_actions_script.html`

Funcionalidades atuais:

- Carrega dados reais do `Ticket`.
- Permite alterar status, prioridade, categoria, fila, SLA, responsável e título.
- Permite assumir chamado.
- Salva comentários reais.
- Salva anexos reais.
- Mostra auditoria real.
- Mantém abas ricas para Visão geral, Dispositivo/RMM, Anexos, Relacionados e outras áreas.

Áreas avançadas ainda parcialmente mockadas:

- console RMM completo;
- scripts remotos;
- mesclagem/vinculação real de chamados;
- checklist persistido definitivo;
- evidência principal persistida como regra de negócio.

## Composer e comentários

O composer suporta:

- comentário interno;
- comentário público;
- templates;
- anexos;
- envio com atalho;
- integração com timeline/atividade.

Backend real:

- `POST /tickets/<numero>/api/comments/`
- cria `TicketComment`;
- cria `TicketAuditEvent` de comentário.

## Anexos e evidências

O módulo já possui `TicketAttachment` real.

Funcionalidades atuais:

- upload de arquivo;
- colar print;
- download por rota segura;
- vínculo visual com evidências/anexos;
- integração com comentários e atividade.

Alguns recursos de evidência avançada ainda são visuais:

- evidência principal;
- classificação avançada;
- ações de preview/remoção com desfazer;
- integração final com resolução.

## Auditoria

O botão `Auditoria` no detalhe abre um drawer técnico.

Auditoria usa `TicketAuditEvent` real para:

- criação do chamado;
- alterações de campos;
- comentários;
- ações operacionais;
- anexos;
- resolução/reabertura/escalonamento quando aplicável.

Filtros e exportação podem permanecer parcialmente locais/mockados.

## Configurações do Desk

Rota:

- `/tickets/settings/`

Abas principais:

- Categorias;
- Filas;
- SLAs;
- Templates;
- GMUD;
- Automações.

Backend real atual:

- Categorias reais.
- Filas reais.
- SLAs reais.
- Templates reais.

GMUD e automações estão em fase visual/configurável, sem workflow real completo.

### Categorias

A aba Categorias usa tabela compacta e drawer lateral.

Recursos atuais:

- criar/editar;
- ativar/desativar;
- duplicar visualmente;
- seletor controlado de ícones Bootstrap Icons;
- paleta controlada de cores;
- ícone/cor persistidos no model.

Seed oficial:

- Acesso: `bi-key` / `blue`
- Hardware: `bi-pc-display` / `cyan`
- Software: `bi-window` / `purple`
- Rede: `bi-hdd-network` / `blue`
- Segurança: `bi-shield-check` / `red`
- RMM / Alerta: `bi-exclamation-triangle` / `amber`
- E-mail: `bi-envelope` / `cyan`
- Impressora: `bi-printer` / `gray`
- GMUD / Mudança: `bi-diagram-3` / `purple`

## Portal do solicitante

Rotas:

- `/tickets/portal/`
- `/tickets/portal/<numero>/`
- `/tickets/portal/<numero>/comment/`
- `/tickets/portal/<numero>/reopen/`

Funcionalidades:

- lista chamados do solicitante;
- mostra detalhe público;
- permite comentário público;
- permite reabrir chamado.

## Automações e e-mail

Existem serviços e commands para:

- renderização de e-mail;
- outbox;
- processamento de inbound e-mail;
- retentativa de envio;
- preview de e-mails;
- regras de automação.

Arquivos relevantes:

- `tickets/services/email_outbox.py`
- `tickets/services/email_renderer.py`
- `tickets/services/inbound_email.py`
- `tickets/services/automation_outbox.py`
- `tickets/services/automation_rules_context.py`
- `tickets/management/commands/process_email_outbox.py`
- `tickets/management/commands/process_inbound_email.py`
- `tickets/management/commands/retry_failed_emails.py`

## Seeds e dados de desenvolvimento

Commands úteis:

- `python manage.py seed_desk_mvp1`
- `python manage.py seed_desk_mvp2`
- `python manage.py seed_demo_tickets`
- `python manage.py clear_demo_tickets`

O seed MVP2 cria:

- categorias;
- filas;
- SLAs;
- templates;
- dados mínimos para operar o Desk.

## Estado atual do backend

Funcional com backend real:

- Central lista chamados reais.
- Drawer rápido cria chamados reais.
- Detalhe carrega Ticket real.
- Alterações básicas persistem.
- Comentários persistem.
- Anexos persistem.
- Auditoria básica persiste.
- Categorias, filas, SLAs e templates são reais.

Ainda mockado/progressivo:

- RMM remoto real;
- GMUD workflow real;
- mesclagem/vínculo persistido entre chamados;
- automações reais de SLA;
- persistência final de checklist;
- regras completas de evidência principal;
- solicitante como entidade própria.

## Comandos de validação

Usar o venv local:

```powershell
.\venv\Scripts\python.exe manage.py check
```

Rodar seed de configurações:

```powershell
.\venv\Scripts\python.exe manage.py seed_desk_mvp2
```

Subir servidor:

```powershell
.\venv\Scripts\python.exe manage.py runserver
```

## Direção recomendada

Próximas fases naturais:

1. Persistir relacionamentos/mesclagem de chamados.
2. Transformar solicitante em model próprio.
3. Persistir checklist e evidência principal.
4. Consolidar GMUD real.
5. Implementar automações reais por evento/SLA/template.
6. Integrar ações RMM reais com agente/endpoints.
