# Fluxo real de Filas e SLAs

## Models usados

- `DeskQueue`: configura as filas reais do Desk, incluindo responsável, status, recebimento de chamados/RMM/GMUD e capacidade.
- `DeskSLA`: configura prazos reais por prioridade, primeira resposta, resolução, calendário simples e pausa ao aguardar solicitante.
- `Ticket`: armazena `queue`, `sla` e `due_at`. O vencimento básico usa `due_at = created_at + sla.resolution_minutes`.
- `TicketCategory`: liga categoria a fila padrão e SLA padrão.
- `TicketAuditEvent`: registra criação do chamado e mudanças operacionais de fila/SLA.

## Telas conectadas

- Configurações > Filas: lista, cria, edita e ativa/desativa `DeskQueue`.
- Configurações > SLAs: lista, cria, edita e ativa/desativa `DeskSLA`.
- Novo chamado rápido: carrega categorias reais; ao selecionar categoria, sugere prioridade, fila e SLA padrão.
- Central: usa filas reais no filtro avançado, exibe SLA/vencimento e mantém tickets com fila real.
- Detalhe: mostra fila, SLA e vencimento; permite alterar fila e SLA sem sair da tela.

## O que ficou real

- Persistência de filas e SLAs no banco.
- Seed idempotente com filas `N1 - Atendimento`, `N2 - Infraestrutura`, `Segurança`, `Sistemas` e `CAB / Mudanças`.
- Seed idempotente com SLAs `Baixa`, `Normal`, `Alta` e `Crítica`.
- Categoria `VPN` com ícone `bi-lock`, cor `blue`, prioridade `Alta`, fila `N2 - Infraestrutura`, SLA `Alta` e tipo `Incidente`.
- Ticket criado recebe fila, SLA e `due_at`.
- Alteração de categoria, fila ou SLA recalcula o vencimento quando aplicável.
- Auditoria registra criação/edição/toggle de filas e SLAs, criação de ticket e mudanças de fila/SLA no detalhe.

## O que ainda é mockado ou simplificado

- Calendário comercial avançado ainda não é calculado; o vencimento usa soma direta de minutos.
- GMUD continua visual/preview nesta etapa.
- Filas ainda são armazenadas no `Ticket.queue` por nome, reaproveitando o model existente sem criar FK nova.
- Métricas avançadas de SLA, pausa real e automações permanecem para fases futuras.
