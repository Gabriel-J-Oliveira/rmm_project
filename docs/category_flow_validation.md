# Night Owl Desk - validacao do fluxo de categorias

## Objetivo

Validar que uma categoria criada ou editada em Configuracoes passa a afetar a abertura, exibicao e edicao dos chamados.

## Fonte dos dados

- Model principal: `tickets.TicketCategory`
- Fila padrao: `tickets.DeskQueue`
- SLA padrao: `tickets.DeskSLA`
- Ticket criado: `tickets.Ticket`
- Auditoria do chamado: `tickets.TicketAuditEvent`
- Auditoria global, quando disponivel: `agents.AuditEvent`

## Campos persistidos da categoria

Os seguintes campos sao salvos no banco e devem permanecer apos F5:

- nome
- descricao
- icone
- cor
- status ativo/inativo
- prioridade padrao
- fila padrao
- SLA padrao
- tipos permitidos
- subcategorias

Icones e cores passam por fallback seguro. Icone invalido vira `bi-folder`; cor invalida vira `gray`.

## Fluxo esperado

1. Criar ou editar categoria em `/tickets/settings/` ou `/tickets/categories/`.
2. Selecionar a categoria no Novo chamado rapido.
3. A UI sugere prioridade, fila e SLA padrao.
4. Ao criar o chamado, o backend salva:
   - categoria real
   - fila padrao
   - prioridade
   - SLA e `due_at`
5. A Central exibe categoria real com icone/cor normalizados.
6. O Detalhe exibe categoria, fila, prioridade e SLA reais.
7. Ao alterar categoria no Detalhe, o backend aplica os defaults da nova categoria e registra auditoria.

## Categoria de aceite: VPN

Rodar:

```powershell
python manage.py seed_desk_mvp2
```

Validar a categoria:

- Nome: `VPN`
- Icone: `bi-lock`
- Cor: `blue`
- Prioridade padrao: `Alta`
- Fila padrao: `N2 - Infraestrutura`
- SLA padrao: `Alta`
- Tipo permitido: `Incidente`

## Teste manual

1. Abra `/tickets/settings/` e confirme que `VPN` aparece na lista.
2. Edite `VPN`, salve e pressione F5; os campos devem permanecer.
3. Abra `/tickets/`.
4. Clique em Novo chamado.
5. Selecione `VPN`.
6. Confirme a sugestao de prioridade/fila/SLA.
7. Crie o chamado.
8. Confirme que o chamado aparece na Central com categoria `VPN`.
9. Abra o Detalhe.
10. Confirme categoria, fila, prioridade e SLA.
11. Altere a categoria no Detalhe e valide evento de auditoria do chamado.

## Limitacoes atuais

- Tipos permitidos e subcategorias ainda nao bloqueiam regras de abertura; eles sao persistidos e exibidos para a proxima fase de validacao operacional.
- A fila do ticket ainda e salva como texto, mantendo compatibilidade com a fase atual do Desk.
