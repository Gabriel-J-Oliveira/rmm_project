# Fluxo de templates do Night Owl Desk

## Estrutura real

Os templates usam o model `tickets.DeskTemplate`. Ele armazena nome, descricao,
tipo, aplicacao, canal, categoria opcional, assunto, conteudo, variaveis e status.
O assunto e o conteudo ficam prontos para uma futura integracao de e-mail, mas
nenhum envio ou recebimento de e-mail ocorre nesta fase.

Aplicacoes suportadas:

- `composer_publico`
- `composer_interno`
- `resolver_chamado`
- `escalar_chamado`
- `automacao_chamado_criado`
- `automacao_chamado_resolvido`
- `automacao_chamado_reaberto`
- `automacao_aguardando_solicitante`

## Renderizacao

O helper `tickets.services.desk_templates.render_template()` recebe template,
ticket, usuario e contexto adicional. As variaveis conhecidas sao substituidas
por dados reais do chamado. Variaveis desconhecidas viram texto vazio, evitando
que marcadores invalidos sejam enviados ao operador.

Variaveis essenciais:

- `{{ticket_code}}`
- `{{titulo}}`
- `{{solicitante}}`
- `{{tecnico}}`
- `{{categoria}}`
- `{{prioridade}}`
- `{{fila}}`
- `{{endpoint}}`
- `{{solucao}}`
- `{{data}}`

## Telas conectadas

- Configuracoes > Templates lista e edita registros reais, incluindo assunto.
- O Composer filtra templates publicos e internos conforme a visibilidade.
- Selecionar uma macro apenas insere texto renderizado e editavel.
- Resolver permite escolher um template, preparar o comentario publico e
  persistir resolucao, comentarios e auditoria.
- Escalar permite escolher um template, preparar motivo/comentario interno e
  persistir fila, responsavel, prioridade, comentario e auditoria.

## Auditoria

O uso no Composer registra `template_used` quando o comentario e salvo.
A resolucao registra `ticket_resolved` e uma auditoria informando que a
notificacao foi preparada, com `email_sent=false`. O escalonamento registra
`ticket_escalated` com o template usado, sem incluir dados sensiveis.

## Seed e validacao

O comando `python manage.py seed_desk_mvp2` usa `update_or_create`, portanto pode
ser executado repetidamente. Ele mantem os oito templates essenciais solicitados.

Comandos de validacao:

```text
python manage.py check
python manage.py makemigrations --check
python manage.py seed_desk_mvp2
```

## Fora do escopo

Envio e recebimento de e-mail, disparos automaticos e automacoes completas
continuam desativados. Os campos `subject` e `content` apenas preparam essa fase.
