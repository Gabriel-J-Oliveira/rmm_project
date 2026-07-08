# Processamento de e-mail inbound do NightOwl Desk

## Objetivo

O inbound de e-mail permite que respostas enviadas pelo solicitante sejam vinculadas ao chamado correto e aparecam como mensagens publicas na conversa do chamado.

Nesta fase nao ha chat em tempo real, WebSocket ou IMAP push. O processamento acontece por comando.

## Configuracao

Variaveis esperadas no `.env`:

```env
INBOUND_EMAIL_ENABLED=True
INBOUND_EMAIL_HOST=imap.exemplo.com
INBOUND_EMAIL_PORT=993
INBOUND_EMAIL_USER=suporte@empresa.com.br
INBOUND_EMAIL_PASSWORD=change-me
INBOUND_EMAIL_USE_SSL=True
INBOUND_EMAIL_FOLDER=INBOX
INBOUND_EMAIL_PROCESSED_FOLDER=NightOwl/Processed
INBOUND_EMAIL_ERROR_FOLDER=NightOwl/Error
INBOUND_EMAIL_TIMEOUT=30
```

Credenciais nao sao exibidas em tela, logs ou auditoria.

## Identificacao do chamado

O parser vincula e-mails somente por referencia confiavel:

- header `X-NightOwl-Ticket-ID`;
- ou assunto no formato `[NightOwl #1234]`.

E-mails sem uma dessas referencias sao registrados como `skipped` em `InboundEmailMessage` e nao criam comentario solto.

Os e-mails enviados pelo NightOwl passam a incluir:

- assunto com prefixo `[NightOwl #<numero>]`;
- header `X-NightOwl-Ticket-ID`;
- header `X-NightOwl-Event`.

## Comandos

Processar caixa IMAP:

```bash
python manage.py process_inbound_email --limit 20
```

Dry-run:

```bash
python manage.py process_inbound_email --dry-run --limit 5
```

Restringir a um chamado:

```bash
python manage.py process_inbound_email --ticket 1052
```

Processar arquivo `.eml` local:

```bash
python manage.py process_inbound_email_file path/to/email.eml --dry-run
python manage.py process_inbound_email_file path/to/email.eml --apply
```

## Deduplicacao

O modelo `InboundEmailMessage` guarda `message_id` unico.

Regras:

- se o `Message-ID` ja existe, o e-mail retorna `deduped`;
- nao cria novo comentario;
- nao cria novos anexos;
- se o e-mail nao tiver `Message-ID`, o sistema usa `sha256:<hash-do-eml>` como fallback de dedupe.

## Corpo do e-mail

O parser:

- prefere `text/plain`;
- converte HTML para texto quando necessario;
- remove scripts/HTML;
- tenta remover citacoes, cabecalhos de thread e assinaturas simples;
- preserva quebras de linha;
- limita o comentario salvo a 12.000 caracteres.

A limpeza de assinaturas e threads e conservadora. Algumas assinaturas complexas ainda podem aparecer e devem ser refinadas em fase futura.

## Anexos

Anexos reais sao salvos como `TicketAttachment`:

- `visibility=public`;
- vinculados ao chamado;
- vinculados ao comentario criado;
- com nome original sanitizado;
- com `content_type` e tamanho.

Anexos inline de assinatura sao ignorados quando aparecem como `inline`/`Content-ID`. Extensoes executaveis perigosas sao ignoradas.

## Regras por status

- `Novo`, `Em atendimento`, `Aguardando terceiro`: cria comentario publico.
- `Aguardando solicitante`: cria comentario publico e volta para `Em atendimento` usando o workflow.
- `Resolvido`: trata a resposta como contestacao, reabre para `Em atendimento` com comentario publico `Reabertura solicitada por e-mail` e nao envia confirmacao automatica ao solicitante.
- `Fechado`/`Cancelado`: registra `skipped`; nao cria comentario e nao reabre automaticamente.

O processamento usa `ticket_workflow.py` para evitar duplicar regra de status.

## Auditoria

O sistema registra auditoria para:

- e-mail inbound processado;
- comentario publico criado por e-mail;
- status alterado pelo workflow;
- anexos publicos criados por e-mail;
- e-mail ignorado;
- erro de processamento.

Ator humano usa o nome/e-mail do remetente. Eventos automaticos usam `Sistema`.

## Prevencao de loop

Sao ignorados:

- e-mails cujo remetente e o proprio `DEFAULT_FROM_EMAIL`, `SERVER_EMAIL` ou `EMAIL_HOST_USER`;
- e-mails com `Auto-Submitted` diferente de `no`;
- e-mails sem referencia confiavel de chamado.

O inbound nao envia confirmacao de recebimento para o solicitante, evitando loops.

## Validacoes recomendadas

1. Resposta simples com assunto `Re: [NightOwl #1052] Chamado resolvido`.
2. Resposta com anexo.
3. Reprocessar o mesmo `.eml` e confirmar `deduped`.
4. E-mail sem ticket e confirmar `skipped`.
5. E-mail em chamado `Aguardando solicitante` e confirmar retorno para `Em atendimento`.
6. E-mail em chamado `Resolvido` e confirmar reabertura.
7. E-mail em chamado `Fechado` e confirmar `skipped`.
8. E-mail do proprio NightOwl e confirmar prevencao de loop.
9. E-mail HTML-only e confirmar comentario legivel.

## Limitacoes

- Nao ha IMAP IDLE/push.
- Nao ha tela dedicada para inbound.
- Nao ha threading visual por `In-Reply-To`/`References`, embora os headers sejam armazenados em metadata.
- Nao ha politica corporativa completa de bloqueio de anexos.
