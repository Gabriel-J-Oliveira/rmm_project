# NightOwl Desk - contexto atual do modulo de chamados

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

