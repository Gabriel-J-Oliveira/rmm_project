# Portal de Chamados V1

## Rotas

- `/portal/chamados/`: lista de chamados do solicitante.
- `/portal/chamados/<numero>/`: detalhe publico do chamado.
- `/portal/chamados/<numero>/comment/`: cria resposta publica do solicitante.
- `/portal/chamados/<numero>/reopen/`: reabre chamado resolvido com motivo publico.

As rotas equivalentes em `/tickets/portal/` tambem existem para compatibilidade interna de namespace.

## Dados visiveis

- Codigo, titulo, status, categoria, prioridade, fila/equipe, responsavel e previsao/SLA simples.
- Descricao original do chamado com quebras de linha preservadas.
- Comentarios com `visibility=public`.
- Anexos com `visibility=public`.
- Timeline publica com abertura, comentarios publicos e resolucao.
- Solucao aplicada quando o chamado estiver resolvido, usando o ultimo comentario publico como fallback.

## Dados ocultos

- Comentarios internos.
- Auditoria tecnica.
- Eventos RMM, acoes remotas, logs e erros de e-mail.
- Anexos com `visibility=internal`.
- Relacionamentos internos, mesclagem e dados operacionais da equipe.

## Anexos

Foi criado `TicketAttachment` com visibilidade publica/interna. Anexos enviados pelo portal sao publicos por padrao. Anexos internos devem ser cadastrados com `visibility=internal` para ficarem ocultos no portal.

## Limitacoes da V1

- Ainda nao ha autenticacao publica/token de acesso avancado.
- Se o usuario autenticado possuir e-mail, a lista filtra por `requester_email`.
- Sem usuario autenticado, a V1 permite simular a visao com `?email=usuario@empresa.com.br`; sem esse parametro, lista os chamados disponiveis.
- Nao ha inbound e-mail.
- Nao ha portal de abertura publica completo nesta fase.

## Proximos passos

- Autenticacao do solicitante ou link publico com token seguro por chamado.
- Abertura publica de chamado.
- Inbound e-mail.
- Regras finas de visibilidade para anexos e eventos publicos.
