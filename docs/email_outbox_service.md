# Servico global de fila de e-mails

## Visao geral

O Night Owl usa `tickets.NotificationOutbox` como fila global de e-mails. O
model nasceu no Desk e foi ampliado sem duplicacao para atender tambem RMM,
sistema, GMUD e autenticacao. O vinculo com Ticket e opcional.

O servico central esta em `tickets/services/email_outbox.py`.

## API interna

### `queue_email(...)`

Cria um item na fila com origem, evento, destinatarios, assunto, corpos,
prioridade e metadata. Destinatario ausente ou invalido gera um registro
`skipped`, mantendo rastreabilidade.

Cada item pode manter os dois formatos da mesma mensagem:

- `body_text`: fallback em texto puro, sempre legivel;
- `body_html`: layout visual opcional enviado como alternativa HTML.

### `send_email_outbox_item(email_id)`

Trava somente a linha da outbox no PostgreSQL, marca `sending`, incrementa a
tentativa e envia com `EmailMultiAlternatives`. Corpo HTML, CC e BCC sao
opcionais.

Sucesso:

- status `sent`;
- `sent_at` preenchido;
- erro anterior removido.

Falha:

- status `failed`;
- tentativa e data registradas;
- excecao resumida em `last_error`;
- auditoria criada.

### Processamento e retry

- `process_pending_emails(limit=50)`;
- `retry_failed_email(email_id)`;
- `retry_all_failed()`;
- `mark_email_pending(email_id)`;
- `cancel_email(email_id)`.

O processamento respeita prioridade (`high`, `normal`, `low`) e
`max_attempts`.

## SMTP

As configuracoes sao lidas exclusivamente do ambiente:

```text
EMAIL_HOST
EMAIL_PORT
EMAIL_HOST_USER
EMAIL_HOST_PASSWORD
EMAIL_USE_TLS
EMAIL_USE_SSL
DEFAULT_FROM_EMAIL
NIGHTOWL_PUBLIC_URL
```

Sem `EMAIL_HOST` ou `DEFAULT_FROM_EMAIL`, a tentativa falha de forma controlada
e registra a explicacao em `last_error`. A senha nunca e enviada aos templates
ou a interface.

`NIGHTOWL_PUBLIC_URL` e opcional. Quando configurada com uma URL HTTP/HTTPS
valida, o e-mail de chamado inclui um botao para o detalhe:

```text
NIGHTOWL_PUBLIC_URL=https://nightowl.exemplo.com
```

Sem essa configuracao, nenhum link publico e criado e o e-mail orienta o
destinatario a acessar o Night Owl Desk.

## Layout HTML

O renderer reutilizavel esta em `tickets/services/email_renderer.py` e usa
`templates/emails/base_email.html`. O template e composto por tabelas e CSS
inline para manter compatibilidade com clientes de e-mail comuns, sem
JavaScript ou assets externos obrigatorios.

Os eventos `ticket_created`, `ticket_assigned`, `waiting_requester`,
`ticket_resolved` e `ticket_reopened` salvam o conteudo do `DeskTemplate` em
`body_text` e inserem esse mesmo conteudo no layout padronizado de `body_html`.
Quando existe ticket, o HTML mostra codigo, titulo, status, prioridade,
categoria, fila, responsavel e endpoint.

O envio usa `EmailMultiAlternatives`: texto puro e o corpo principal, enquanto
HTML e anexado como alternativa `text/html`. Registros antigos sem `body_html`
continuam sendo enviados apenas como texto.

O drawer da fila permite alternar entre **Texto** e **HTML / Previa**. A previa
usa um iframe isolado por `sandbox` e uma politica que bloqueia scripts e
recursos externos.

## Integracao com o Desk

Os eventos abaixo continuam usando `DeskTemplate`, mas agora chamam a fila
global:

- `ticket_created`;
- `ticket_assigned`;
- `waiting_requester`;
- `ticket_resolved`;
- `ticket_reopened`.

Os itens usam `source_app=desk`, `source_model=tickets.Ticket` e mantem o ticket
relacionado. A auditoria do chamado registra `email_queued`, `email_sent`,
`email_failed`, `email_retried` e `email_cancelled`.

## Comandos

```text
python manage.py process_email_outbox
python manage.py process_email_outbox --limit 20
python manage.py retry_failed_emails
python manage.py send_email_outbox_item UUID
python manage.py send_email_outbox_item UUID --reset-attempts
```

Esses comandos fazem envio real quando o SMTP esta configurado.

Para testar o SMTP e o layout HTML sem criar um chamado:

```text
python manage.py test_smtp_email --to email@empresa.com.br
```

## Interface operacional

A pagina `/maintenance/email-outbox/` fica em Manutencao > Fila de E-mails.
Ela permite:

- buscar e filtrar por status, origem e data;
- consultar assunto, corpo, metadata, erros e tentativas;
- abrir o ticket relacionado;
- processar pendentes;
- reenviar um item ou todos os erros elegiveis;
- cancelar ou recolocar itens na fila.

O corpo completo e o erro aparecem apenas no drawer. A tabela usa versoes
resumidas.

## Limites atuais

- nao existe recebimento de e-mail;
- nao existe worker residente;
- processamento ocorre por comando ou acao manual na Manutencao;
- agendamento deve ser feito futuramente pelo orquestrador de manutencao.
