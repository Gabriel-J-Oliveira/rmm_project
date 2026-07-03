# Backend MVP 1 - Inventario tecnico

## Escopo

Fluxo implementado nesta fase:

`Central de Atendimento -> Novo chamado rapido -> Detalhe basico -> Comentarios -> Auditoria basica`

O layout atual sera preservado. Recursos fora do escopo continuam usando os mocks existentes.

## Estrutura existente

### Models reaproveitados

- `TicketCategory`: categoria persistida, status e cor.
- `Ticket`: numero sequencial, titulo, descricao, solicitante, status, prioridade, categoria, responsavel, origem, endpoint RMM opcional e timestamps.
- `TicketComment`: comentario interno/publico com autor e data.

### Lacunas de model

- `Ticket.queue`: fila operacional simples.
- `Ticket.endpoint_name`: hostname textual quando nao houver `AgentMachine` vinculado.
- `TicketAuditEvent`: registro tecnico persistido para criacao, comentarios e alteracoes.
- Origens Portal, Telefone e Monitoramento no conjunto de escolhas do chamado.

`Category` sera atendida por `TicketCategory`. Uma entidade `Queue` completa fica fora deste MVP; a fila sera armazenada como texto no chamado para evitar acoplamento prematuro com a tela mockada de Configuracoes.

## Central de Atendimento

### Botoes e acoes existentes

- Novo chamado.
- Busca e command palette.
- Filtros por view, status, prioridade, tecnico, cliente, setor, categoria, SLA e RMM.
- Chips rapidos: fila geral, atribuidos a mim, sem responsavel, criticos, SLA vencendo e RMM.
- Selecao em massa e acoes contextuais.
- Preview lateral, assumir, mudar status/prioridade e comentario rapido.

### Dados mockados atuais

- `MOCK_TICKETS` e seus comentarios/endpoints.
- SLA, telemetria RMM, anexos, relacionados e eventos do preview.
- Atualizacoes das linhas executadas somente no estado JavaScript.

### Implementado agora

- QuerySet real de `Ticket`.
- Busca por numero, titulo, solicitante e e-mail.
- Filtros reais de status, prioridade, categoria, responsavel, atribuidos a mim, sem responsavel, criticos e origem RMM.
- KPIs basicos calculados do banco.
- Payload e preview lateral alimentados por tickets persistidos.
- Criacao rapida via endpoint JSON.

### Continua mockado

- SLA vencendo (aproximado visualmente por prioridade/idade).
- Telemetria e acoes RMM.
- Acoes em massa, anexos e relacionamentos.

## Drawer Novo chamado rapido

### Campos e acoes existentes

- Solicitante, titulo, descricao, categoria, prioridade, origem e endpoint.
- Autocomplete mockado de solicitante/endpoint.
- Templates, anexos visuais, relacionados e rascunho local.
- Criar, Criar e abrir e Criar e assumir.

### Implementado agora

- Validacao equivalente no backend.
- Persistencia de `Ticket`.
- Criacao/obtencao de categoria.
- Fila padrao textual.
- Endpoint textual opcional.
- Auditoria `ticket_created`.
- Respostas JSON para atualizar a Central ou abrir o detalhe.

### Continua mockado

- Cadastro de solicitante, upload, vinculo real com endpoint e relacionados.

## Detalhe do Chamado

### Acoes existentes

- Alterar status, prioridade, categoria e responsavel.
- Assumir atendimento.
- Resolver, escalar, checklist, abas, RMM, anexos e relacionados.

### Implementado agora

- Carregamento por `Ticket.number`.
- Status, prioridade, categoria e responsavel persistidos por endpoint JSON.
- Auditoria com antes/depois.
- Dados basicos reais no header, sidebar e estado JavaScript.

### Continua mockado

- Resolver/escalar guiados, checklist persistente, RMM, anexos e mesclagem.

## Comentarios e atividade

### Acoes existentes

- Composer interno/publico, macros, anexos visuais e acoes combinadas.
- Comentario rapido no preview da Central.

### Implementado agora

- Criacao de `TicketComment` interno/publico.
- Comentarios persistidos exibidos na atividade.
- Auditoria `comment_created`.

### Continua mockado

- Anexos do comentario e acoes combinadas de resolver/escalar.

## Auditoria

### UI existente

- Drawer, resumo, busca/filtros locais, linhas expansivas, CSV e copia de resumo.

### Implementado agora

- Eventos de `TicketAuditEvent` renderizados no drawer.
- Criacao, comentarios e alteracoes de status, prioridade, categoria e responsavel.
- Metadados JSON simplificados.

### Continua mockado

- Exportacao CSV no frontend e eventos de RMM/anexos/relacionamentos.

## Endpoints MVP

- `POST /tickets/api/tickets/`: criar chamado rapido.
- `POST /tickets/<number>/api/update/`: alterar campo basico/assumir.
- `POST /tickets/<number>/api/comments/`: criar comentario.

Todos retornam JSON, usam CSRF e nao exigem reload para a interacao local.

## Seed

O comando `python manage.py seed_desk_mvp1` cria:

- categorias basicas;
- filas textuais nos chamados;
- 10 chamados;
- comentarios internos/publicos;
- eventos de auditoria.

O comando e idempotente por numero de chamado.
