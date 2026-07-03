# SMTP do Night Owl

O Night Owl usa o backend SMTP do Django para enviar os itens pendentes da fila
global de e-mails. Esta etapa implementa somente envio outbound; recebimento de
e-mail ainda nao faz parte do fluxo.

## Configuracao

Copie `.env.example` para `.env` e preencha:

```env
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.exemplo.com
EMAIL_PORT=587
EMAIL_HOST_USER=naoresponda@example.com
EMAIL_HOST_PASSWORD=change-me
EMAIL_USE_TLS=True
EMAIL_USE_SSL=False
EMAIL_TIMEOUT=20
DEFAULT_FROM_EMAIL=NightOwl Desk <naoresponda@example.com>
SERVER_EMAIL=NightOwl <naoresponda@example.com>
```

Ative somente uma opcao entre `EMAIL_USE_TLS` e `EMAIL_USE_SSL`. A senha deve
existir apenas no `.env` local ou no gerenciador de segredos do ambiente e nunca
deve ser adicionada ao Git.

Na tela **Manutencao > Fila de E-mails**, o indicador SMTP mostra apenas:

- SMTP configurado;
- SMTP incompleto;
- TLS/SSL conflitante.

Credenciais nunca sao enviadas ao template.

## Teste direto

Depois de preencher o `.env`, envie uma mensagem simples:

```powershell
python manage.py test_smtp_email --to email@empresa.com.br
```

Assunto: `Teste SMTP NightOwl`

Corpo: `Envio de teste realizado pelo NightOwl.`

## Processamento da fila

Enviar ate 50 itens pendentes:

```powershell
python manage.py process_email_outbox
```

Alterar o limite:

```powershell
python manage.py process_email_outbox --limit 100
```

Reprocessar falhas que ainda nao atingiram o limite de tentativas:

```powershell
python manage.py retry_failed_emails
```

Reenviar um item especifico:

```powershell
python manage.py send_email_outbox_item <uuid>
```

As mesmas operacoes podem ser iniciadas na tela da fila. Os resultados mantem
status, tentativas, ultimo erro, ultima tentativa e data de envio.

## Seguranca

- Destinatarios invalidos e mensagens sem assunto ou corpo sao ignorados com
  motivo registrado.
- Erros persistidos sao resumidos e qualquer ocorrencia da senha SMTP e
  removida.
- O corpo completo nao e escrito nos logs de envio.
- O arquivo `.env` esta ignorado pelo Git.
- Nenhuma senha SMTP e exibida na interface ou na auditoria.
