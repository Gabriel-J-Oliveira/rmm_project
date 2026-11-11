# RC47-01 — Inventário de Hardware

## Escopo

A nova página `/hardware-inventory/` utiliza projeções de `AgentMachine`, do último `InventorySnapshot` e de alertas ativos. As rotas antigas `/capacity/` e `/api/capacity/...` permanecem para compatibilidade. A nova interface não calcula P95, pressão, cobertura de amostras, carga de processos nem séries de desempenho. Não houve alteração em coletor, modelo, migration, scheduler ou pipeline de alertas.

## Matriz de consumidores

| Produtor/serviço | Consumidores encontrados | Tratamento nesta mudança |
|---|---|---|
| `InventorySnapshot` e inventário recebido | Detalhes de endpoint, APIs de inventário, contexto de chamados e a nova página | Reutilizado, com campos permitidos; JSON bruto não é devolvido |
| `EndpointPerformanceSample` / `telemetry_analytics` | `agents/telemetry_views.py`, análise persistida/contínua e APIs antigas de Capacidade | Preservados; ausentes das novas consultas de Inventário de Hardware |
| `telemetry_diagnostics` | APIs de telemetria, testes e APIs antigas de Capacidade | Preservado integralmente |
| `telemetry_continuous` | Consolidação de janelas e criação de alertas `continuous_telemetry` | Preservado; alertas continuam visíveis na nova tela como registros ativos |
| Alertas derivados do inventário | `agents/alerting.py` e central de alertas | Regras não alteradas; consulta de leitura reaproveitada |
| `capacity.js` e `capacity_series` | Página/API legada e testes de compatibilidade | Arquivos e rotas antigas mantidos; a nova página carrega `hardware_inventory.js` |

Nenhum consumidor de `build_endpoint_telemetry_summary`, `build_resource_diagnostics` ou `build_capacity_assessment` foi removido. A rotina de alertas exclui `source='continuous_telemetry'` da reconciliação de alertas não temporários, evitando que a avaliação legada encerre alertas contínuos.

## Campos apresentados

Quando reportados no snapshot: hostname/FQDN, estado, usuário reportado, fabricante/modelo, SO/build, placa-mãe/BIOS (projeção segura), CPU/núcleos/frequência, RAM/módulos/slots, discos físicos, volumes, bateria, uptime, alertas e proveniência. O instante de coleta da seção de hardware é priorizado quando o payload o informa; recebimento é exibido separadamente. Slots inconsistentes são sinalizados e omitidos pelos validadores existentes.

Idade de equipamento, garantia e oportunidade comercial de upgrade não são inferidas: não há fonte persistida confiável identificada para idade/garantia. Critérios de capacidade instalada (por exemplo, RAM observada abaixo de um limiar) são comparações informativas, não recomendação automática nesta versão.

Valores ausentes permanecem “Não informado”/sem evidência. Não há preenchimento com zeros ou dados demonstrativos.

## Identidade e reconciliação

`AgentMachine` não possui constraint única para `(hostname, domain)`; há índices não únicos. O registro de enrollment atual resolve por `machine_id` e por hostname/domínio normalizados, rejeita mais de um resultado (`identity_ambiguous`) e rejeita chaves que apontem a endpoints diferentes (`identity_keys_conflict`). A identidade runtime também valida `machine_id`, hostname, domínio, FQDN e alterações de hardware; serial é evidência auxiliar, não chave de associação.

Ainda assim, duplicatas podem existir por registros históricos anteriores ao hardening, criação manual, domínios/valores legados divergentes ou fluxos concorrentes antigos. O lock de identidade é advisory no PostgreSQL e não existe constraint única de hostname/domínio; endpoints duplicados não devem ser fundidos automaticamente. A mudança não consultou banco de produção e não presume que UUIDs de `CS-CVEL-0281` identifiquem o mesmo equipamento.

Reconciliação segura exige evidência independente e concordante: objeto/SID do computador no AD e domínio, `machine_id` autenticado atual, token/credencial associado, hostname/FQDN e, como evidência auxiliar, serial de hardware significativo. Divergências, serial genérico ou máquinas substituídas exigem revisão administrativa. Não excluir, fundir, desativar ou alterar identidades nesta implementação.
