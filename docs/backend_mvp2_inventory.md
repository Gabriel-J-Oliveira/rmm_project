# Backend MVP 2 - Configuracoes reais alimentando a Desk

## Objetivo

Substituir os mocks principais de Configuracoes do Desk por dados persistidos em PostgreSQL e usar esses dados no fluxo basico da Desk: Central, novo chamado rapido, detalhe, comentarios/templates e auditoria de alteracoes.

## Models usados

- `TicketCategory`: categoria operacional do chamado. Reaproveitada como `DeskCategory` para evitar duplicidade. Agora possui tipos permitidos, prioridade padrao, fila padrao, SLA padrao, icone e cor.
- `DeskQueue`: fila/time responsavel, com responsavel, membros, horario, capacidade e flags para chamados, RMM e GMUD.
- `DeskSLA`: regra de SLA por prioridade, com primeira resposta, resolucao, calendario e pausas.
- `DeskTemplate`: repositorio central de templates de abertura, resposta publica, comentario interno, resposta automatica, resolucao, escalacao, GMUD e checklist.
- `Ticket`: agora pode apontar para `DeskSLA` e possui `due_at` basico calculado pela resolucao do SLA.
- `TicketAuditEvent`: continua registrando criacao, comentarios e alteracoes de campo.

## Telas conectadas

- Configuracoes do Desk:
  - Categorias, Filas, SLAs e Templates renderizam linhas reais vindas do banco.
  - Drawers de criacao/edicao salvam via API JSON.
  - Ativar/desativar persiste no backend.
  - GMUD continua visual/mock nesta fase.
- Central de Atendimento:
  - Filtros usam os dados reais ja persistidos em chamados.
  - Novo chamado rapido usa categorias reais e defaults de prioridade/fila/SLA.
- Detalhe do Chamado:
  - Carrega categoria, fila, prioridade e SLA do ticket real quando disponiveis.
  - Alteracoes de status, prioridade, categoria, fila, SLA e responsavel registram auditoria.
- Composer:
  - Templates ativos para Composer/Resolver aparecem no menu Macro quando existem.
  - Variaveis basicas sao substituidas no servidor.

## Endpoints/views alterados

- `ticket_settings`: usa contexto real de `build_settings_context`.
- `ticket_settings_api`: salva, duplica e ativa/desativa categorias, filas, SLAs e templates.
- `ticket_api_create`: aplica categoria real, fila padrao e SLA padrao ao criar ticket.
- `ticket_api_update`: permite alterar `queue` e `sla`, alem de campos MVP1.

## Seed

Comando idempotente:

```bash
python manage.py seed_desk_mvp2
```

Cria:

- 9 categorias operacionais.
- 5 filas.
- 4 SLAs.
- 8 templates basicos.
- Backfill de SLA/fila em tickets existentes quando possivel.

## Ainda mockado nesta fase

- RMM real.
- GMUD real e workflow de aprovacao.
- Automacoes reais de envio.
- Relacionamentos/mesclagem persistidos.
- Anexos avancados e storage definitivo.
- SLA completo com calendario util e pausas reais.

## Proximos passos sugeridos

1. Normalizar `Ticket.queue` para FK em `DeskQueue` em uma fase propria.
2. Criar relacoes reais de chamados vinculados/mesclados.
3. Implementar automacoes que usem `DeskTemplate`.
4. Evoluir SLA real com calendario, pausas e eventos.
5. Persistir anexos avancados e evidencias principais.
