# Workflow Email Notifications

## Eventos com e-mail

O workflow do NightOwl Desk prepara e-mails via `NotificationOutbox` para:

- `ticket_created`: chamado recebido;
- `ticket_assigned`: chamado em atendimento;
- `waiting_requester`: aguardando resposta do solicitante;
- `ticket_resolved`: chamado resolvido;
- `ticket_reopened`: chamado reaberto;
- `ticket_public_reply`: resposta publica enviada pela equipe no popup de conversa.

`ticket_closed` permanece sem envio nesta fase. Quando o workflow tenta preparar esse evento, ele registra `notification_skipped` em auditoria e nao gera warning operacional.

## Assuntos padronizados

- `ticket_created`: `[NightOwl #<numero>] Chamado #<numero> recebido`
- `ticket_assigned`: `[NightOwl #<numero>] Chamado #<numero> em atendimento`
- `waiting_requester`: `[NightOwl #<numero>] Precisamos da sua resposta no chamado #<numero>`
- `ticket_resolved`: `[NightOwl #<numero>] Chamado #<numero> resolvido`
- `ticket_reopened`: `[NightOwl #<numero>] Chamado #<numero> reaberto`
- `ticket_public_reply`: `[NightOwl #<numero>] Nova resposta no chamado #<numero>`

Os assuntos ficam em `DeskTemplate.subject` e sao atualizados pelo comando:

```bash
python manage.py seed_desk_mvp2
```

## Conteudo

Todos os e-mails incluem:

- saudacao ao solicitante;
- numero e titulo do chamado;
- status atual;
- mensagem principal do evento;
- resumo/contexto seguro;
- link de acompanhamento quando `NIGHTOWL_PUBLIC_URL` estiver configurada;
- rodape automatico do NightOwl Desk.

Nao entram no e-mail:

- comentarios internos;
- auditoria tecnica;
- dados sensiveis de RMM;
- logs;
- stacktrace;
- erros internos.

## Link do chamado

O link usa:

```text
{{ NIGHTOWL_PUBLIC_URL }}/meus-chamados/<numero>/
```

Essa e a rota autenticada do solicitante. Nao ha token publico nesta fase.

Se `NIGHTOWL_PUBLIC_URL` nao estiver configurada, o HTML nao mostra botao e o texto orienta o usuario a acessar o NightOwl Desk.

## Variaveis disponiveis

- `{{ticket_code}}`
- `{{titulo}}`
- `{{solicitante}}`
- `{{tecnico}}`
- `{{categoria}}`
- `{{prioridade}}`
- `{{fila}}`
- `{{endpoint}}`
- `{{status}}`
- `{{responsavel}}`
- `{{mensagem}}`
- `{{motivo}}`
- `{{resumo}}`
- `{{solucao}}`
- `{{data}}`
- `{{data_abertura}}`
- `{{data_resolucao}}`
- `{{link_acompanhamento}}`

## Deduplicacao

`prepare_ticket_notification()` evita duplicidade recente por:

- chamado;
- evento;
- destinatario;
- template;
- janela de 20 segundos.

Isso impede duplicacao por duplo clique ou chamada repetida da mesma acao, sem bloquear eventos iguais feitos novamente depois.

Retry da fila (`process_email_outbox`, `retry_failed_emails`) nao cria novos registros; apenas processa ou reabre registros existentes.

## Preview seguro

Para renderizar sem enviar:

```bash
python manage.py preview_ticket_email --ticket 1042 --event ticket_resolved
```

Com dados mockados:

```bash
python manage.py preview_ticket_email --mock --all-events
```

Para enviar previews visuais pelo EmailOutbox:

```bash
python manage.py preview_ticket_email --mock --all-events --to email@empresa.com.br --send
```

`ticket_closed` aparece como skipped no preview porque nao possui template ativo nesta fase.

## Processamento da fila

```bash
python manage.py process_email_outbox
python manage.py retry_failed_emails
```

## Limitacoes

- Nao ha e-mail de encerramento (`ticket_closed`) ate existir template/aplicacao propria.
- Nao ha portal publico por token.
- Notificacoes internas para equipe ainda nao foram ativadas.
- O recebimento de e-mail/inbound alimenta a conversa publica quando `process_inbound_email` processa a caixa configurada.
