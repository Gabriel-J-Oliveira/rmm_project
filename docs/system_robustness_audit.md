# Auditoria de robustez geral - NightOwl Desk

Data: 2026-07-07

## Escopo verificado

- Central Tecnica em Lista e Kanban.
- Preview lateral da Central Tecnica.
- Central do Solicitante e detalhe em `/meus-chamados/`.
- Workflow de status em `tickets/services/ticket_workflow.py`.
- Comentarios publicos e internos.
- Anexos publicos e internos.
- EmailOutbox.
- Inbound e-mail.
- Auditoria de acoes criticas.

## Correcoes aplicadas

### Protecao local de area tecnica

As views tecnicas da Desk agora validam explicitamente usuario tecnico/admin no backend, alem do middleware global:

- Central Tecnica;
- Minha fila tecnica;
- Novo chamado tecnico;
- Detalhe tecnico;
- Dashboard;
- Categorias;
- Automacoes;
- Configuracoes;
- endpoints AJAX tecnicos;
- acoes fake/preview tecnicas.

Usuarios comuns continuam usando somente `/meus-chamados/` e rotas relacionadas.

### Anexos protegidos

Foi criada rota protegida:

```text
/tickets/attachments/<uuid:attachment_id>/
```

Regras:

- tecnico/admin acessa anexos do chamado;
- solicitante acessa somente anexo publico de chamado cujo `requester_email` seja igual ao e-mail do usuario logado;
- anexo interno nao fica disponivel para solicitante por essa rota;
- `/media/` foi removido da allowlist publica do middleware.

O portal e o popup de conversa publica passaram a usar a rota protegida em vez de `file.url` direto.

### Portal autenticado sem fallback por e-mail em querystring

O fallback antigo por `?email=` foi removido da resolucao de solicitante. A consulta autenticada usa `request.user.email`.

Se o usuario autenticado nao tiver e-mail, a lista fica vazia/estado amigavel em vez de buscar por parametro externo.

### Comentario publico tecnico gera EmailOutbox

Comentarios publicos criados pelo composer tecnico agora geram `ticket_public_reply` na `NotificationOutbox`.

Comentarios internos continuam sem e-mail para o solicitante.

Tambem foi bloqueado comentario publico comum quando o chamado esta em status que nao aceita resposta publica, como `Resolvido`, `Fechado` ou `Cancelado`.

### Escalonamento usando workflow

O fluxo de escalonamento deixou de alterar `ticket.status` diretamente. Quando precisa colocar o chamado em `Em atendimento`, usa `transition_ticket(...)`.

O comentario interno de escalonamento tambem passou a usar `add_ticket_comment(...)`, garantindo auditoria consistente.

### Alternancia Lista/Kanban

A alternancia de visualizacao preserva a querystring atual, trocando apenas `view=list` ou `view=kanban`. Isso reduz divergencia entre filtros ao alternar modo.

## Itens validados

### Lista, Kanban e Preview

- Lista e Kanban usam o mesmo queryset filtrado por `filtered_ticket_views`.
- Sem filtro explicito de status, `filtered_ticket_views` mostra apenas status ativos.
- `Resolvido`, `Fechado` e `Cancelado` saem da fila padrao.
- `Resolvido` aparece quando filtrado explicitamente.
- Kanban usa as colunas do workflow e nao mostra `Fechado` por padrao.
- `?view=grouped` cai para Lista; a tela antiga por responsavel foi removida.

### Workflow backend

As transicoes principais passam por `ticket_workflow.py`:

- assumir: `assign_ticket`;
- status tecnico/drag-and-drop: `transition_ticket`;
- resolver: `transition_ticket`;
- responder solicitante: `requester_reply`;
- contestar/reabrir: `requester_reopen`;
- inbound em resolvido: `transition_ticket` para reabertura;
- inbound em aguardando solicitante: `requester_reply`.

Alteracoes de campos que nao sao transicao de status, como prioridade, fila, SLA e categoria, continuam nas views e registram auditoria propria.

### Comentarios publicos/internos

- Portal filtra `TicketComment.VISIBILITY_PUBLIC`.
- Popup de conversa publica filtra apenas comentarios publicos.
- Comentario interno nao gera e-mail para solicitante.
- Mensagens enviadas pelo popup sao publicas e geram `ticket_public_reply`.
- Mensagens inbound por e-mail sao publicas.

### EmailOutbox

Eventos mapeados:

- `ticket_created`;
- `ticket_assigned`;
- `waiting_requester`;
- `ticket_resolved`;
- `ticket_reopened`;
- `ticket_public_reply`.

`ticket_closed` permanece como evento sem template ativo nesta fase; quando tentado, fica documentado/auditado como `notification_skipped`.

Validacoes existentes na fila:

- destinatario invalido vira `skipped`;
- assunto vazio vira `skipped`;
- corpo vazio vira `skipped`;
- falha SMTP fica em `failed`;
- retry nao cria novo registro;
- falha de e-mail nao quebra workflow.

### Inbound e-mail

O processamento inbound:

- identifica chamado por `X-NightOwl-Ticket-ID` ou assunto `[NightOwl #ID]`;
- deduplica por `Message-ID` ou hash SHA-256 do `.eml`;
- ignora e-mails sem chamado confiavel;
- ignora auto-respostas;
- ignora remetente do proprio NightOwl para evitar loop;
- cria comentario publico;
- salva anexos como publicos;
- bloqueia extensoes perigosas basicas;
- reabre `Resolvido` como `Em atendimento`;
- ignora `Fechado`/`Cancelado` sem reabrir automaticamente.

### Auditoria

Ha auditoria para:

- criacao;
- assumir;
- mudanca de status;
- comentario publico;
- comentario interno;
- anexo;
- resolucao;
- contestacao/reabertura;
- escalonamento;
- inbound e-mail processado/ignorado/falho;
- EmailOutbox queued/sent/failed/skipped;
- configuracoes de categoria/fila/SLA.

## Mocks e limitacoes ainda existentes

- A area visual de anexos do detalhe tecnico ainda possui partes mockadas no frontend, como preview/download simulados em alguns botoes.
- Acoes RMM como acesso remoto e algumas acoes rapidas ainda podem exibir fallback/mock em vez de integracao real.
- Nao existe RBAC por grupo AD nesta fase; a regra MVP continua sendo tecnico/admin por `is_staff`, `is_superuser` ou `NIGHTOWL_TECHNICAL_USERNAMES`.
- `/media/` foi removido da allowlist do middleware, mas em producao o Nginx tambem deve evitar servir uploads sensiveis diretamente sem passar pela rota protegida ou por regra equivalente.
- Deduplicacao de e-mail outbound ainda e por janela recente, evento, ticket, destinatario e template; nao ha `event_id` transacional persistente por acao.
- Inbound e-mail faz limpeza basica de corpo/assinatura, mas assinaturas HTML complexas e imagens inline ainda podem exigir refinamento.

## Proximos passos recomendados

- Criar permissao/RBAC real por grupos AD.
- Substituir os botoes mockados de anexo no detalhe tecnico por download/preview reais usando a rota protegida.
- Adicionar `event_id` ou correlation id persistente para deduplicacao outbound perfeita.
- Criar politica formal de upload: tamanho maximo, tipos permitidos e antivirus/scan.
- Adicionar smoke tests automatizados sem exigir criacao de banco de teste.
- Revisar configuracao Nginx para uploads privados em producao.
