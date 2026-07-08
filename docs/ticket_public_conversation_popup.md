# Popup de conversa publica do chamado

## Objetivo

O popup de conversa publica adiciona uma camada de mensagens em estilo chat na tela tecnica individual do chamado (`/tickets/<numero>/`). Ele centraliza comentarios publicos enviados pela equipe, respostas do solicitante e eventos publicos ja representados como comentarios.

## Comportamento

- Um botao flutuante verde aparece no canto inferior direito da tela tecnica do chamado.
- O botao abre e fecha um popup sobre o conteudo atual, sem nova aba e sem nova pagina.
- O popup fecha pelo botao `X`, pela tecla `Esc` ou ao clicar fora.
- O header mostra solicitante, e-mail, numero do chamado e status atual.
- A lista de mensagens usa scroll interno e ordem cronologica.
- O composer fica no rodape do popup quando o workflow permite comentario publico.

## Mensagens exibidas

O popup mostra apenas dados publicos:

- evento sintetico de abertura do chamado;
- comentarios publicos do chamado;
- comentarios publicos gerados por workflow, como aguardando solicitante, resolucao e reabertura;
- respostas recebidas por e-mail inbound, processadas como comentarios publicos;
- anexos vinculados a comentarios publicos.

Mensagens do solicitante ficam alinhadas a esquerda. Mensagens da equipe ficam alinhadas a direita. Eventos de sistema ficam centralizados e discretos.

## Mensagens ocultas

O popup nao mostra:

- comentarios internos;
- auditoria tecnica;
- logs RMM;
- erros de e-mail;
- acoes remotas;
- dados sensiveis de endpoint;
- detalhes internos de fila/SLA fora do conteudo publico do chamado.

## Envio publico

Toda mensagem enviada pelo popup:

- cria `TicketComment` com `visibility=public`;
- usa o usuario autenticado como autor;
- registra auditoria via workflow;
- salva anexos como publicos quando enviados pelo popup;
- prepara uma notificacao na `NotificationOutbox` com `event_type=ticket_public_reply`.

O e-mail fica pendente na fila global e sera enviado quando a EmailOutbox for processada.

## EmailOutbox

O evento `ticket_public_reply` usa o template `Resposta publica do chamado`, criado pelo seed `seed_desk_mvp2`.

Assunto padrao:

```text
Nova resposta no chamado #{{ ticket.number }}
```

O corpo inclui saudacao, numero, titulo, status, tecnico, mensagem enviada e link de acompanhamento quando `NIGHTOWL_PUBLIC_URL` estiver configurada.

## Limites desta fase

- Nao ha WebSocket ou tempo real.
- O inbound e-mail roda por comando (`process_inbound_email`), nao em tempo real.
- Nao ha IMAP IDLE/push.
- Headers `Message-ID`, `In-Reply-To` e `References` sao armazenados, mas ainda nao viram threading visual.
- O popup nao substitui a area interna de comentarios.

## Proxima fase

- Tornar recebimento IMAP agendado em producao.
- Exibir marcadores de e-mail recebido/enviado na conversa.
- Refinar threading visual por Message-ID e referencias.
