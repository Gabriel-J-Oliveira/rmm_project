# Kanban tecnico MVP

## Objetivo

A visao Kanban da Central Tecnica organiza chamados por status operacional, priorizando leitura rapida, triagem visual e mudancas de status pelo workflow real.

## Colunas

As colunas seguem os status do workflow atual:

- Novo.
- Em atendimento.
- Aguardando usuario.
- Aguardando terceiro.
- Resolvidos.

Encerrados e cancelados nao aparecem por padrao. Resolvidos so aparecem quando o filtro atual inclui esse status ou quando existirem itens filtrados nessa coluna.

## Cards

Cada card mostra:

- numero e titulo;
- solicitante e setor;
- prioridade;
- responsavel;
- SLA resumido;
- origem;
- categoria;
- ultima atualizacao.

Badges adicionais indicam criticidade, SLA vencido/vencendo, sem responsavel, RMM, VIP e reaberto.

## Filtros

A barra rapida mantem:

- Fila geral;
- Atribuidos a mim;
- Sem responsavel;
- Criticos;
- SLA vencendo;
- Reabertos;
- RMM.

O drawer "Mais filtros" continua concentrando status, prioridade, responsavel, fila, categoria, origem, setor, SLA, RMM e caracteristicas.

## Preview

O preview lateral continua disponivel no Kanban, mas pode ser recolhido. Ao recolher, o board ocupa toda a largura util. Clicar em um card seleciona o chamado e carrega o preview.

## Drag-and-drop

O drag-and-drop usa a API real da Central, que chama `ticket_workflow.py` por meio de `transition_ticket()` ou `assign_ticket()`.

Transicoes sensiveis exigem modal:

- Aguardar usuario: mensagem publica ao solicitante.
- Resolver: solucao aplicada.
- Reabrir resolvido: motivo da reabertura.
- Encerrar/cancelar: motivo.

Novo para Em atendimento atribui o chamado ao tecnico atual quando ainda estiver sem responsavel.

Transicoes invalidas sao bloqueadas e o card permanece na coluna original.

## Limitacoes

- Nao ha status persistido `reopened`; o card "Reaberto" usa auditoria `ticket_reopened`.
- O Kanban ainda nao possui persistencia de ordem manual dentro da coluna.
- Em mobile, o board usa scroll horizontal e o preview pode ficar abaixo conforme o layout responsivo.

## Validacao manual

1. Abrir `/tickets/?view=kanban`.
2. Alternar entre Lista e Kanban.
3. Aplicar filtros rapidos e Mais filtros.
4. Selecionar card e validar preview.
5. Recolher e reabrir preview.
6. Arrastar Novo para Em atendimento.
7. Arrastar Em atendimento para Aguardando usuario.
8. Arrastar Em atendimento para Resolvido.
9. Arrastar Resolvido para Em atendimento.
10. Tentar uma transicao invalida e confirmar erro amigavel.
11. Conferir auditoria e EmailOutbox nos eventos sensiveis.
