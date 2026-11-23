# RC48-01B — Projeções e consumidores de telemetria

## Escopo

Auditoria das projeções do payload continuous v2 e dos consumidores de resumo,
capacidade e monitoramento. Não altera o agente, protocolo, cadência, retenção,
alertas, schema do banco ou política de coleta. A retenção permanece conforme a
configuração existente; qualquer mudança depende de medição e decisão próprias.

## Caminho e compatibilidade

| Dado | Origem e persistência | Projeção/consumidor | Resultado |
|---|---|---|---|
| CPU e RAM | `ContinuousNativeReader` → agregação por janela de 60 s → `metric_summaries.metrics` | `project_continuous_metrics` mantém min/máx/média/percentis aproximados e leituras válidas; resumos de telemetria e capacidade consomem as mesmas janelas | Compatível; valores ausentes continuam desconhecidos |
| Discos | PDH `PhysicalDisk` por identidade hash; propriedades de associação são explicitamente não verificadas | Resumo v2 agrupa métricas por hash e liga metadados de `quality.disk_instances`; não afirma que instância PDH é disco físico | Compatível; associação física permanece não comprovada |
| Interfaces | `NetworkInterface` por ID hash; taxas derivadas de deltas e velocidade nominal | Resumo v2 agrupa por interface e mantém o aviso de que velocidade nominal não representa capacidade de internet | Compatível; a primeira leitura/delta inválido pode não produzir taxa |
| Hardware | Inventário Windows separado da telemetria contínua | Dashboard de capacidade lê `InventorySnapshot`; não tenta inferir hardware físico das métricas PDH | Fontes independentes preservadas |
| Legacy | Amostras de 300 s e contadores acumulados permanecem no contrato legado | Resumo legacy mantém campos e unidades anteriores; nenhuma taxa v2 é convertida em contador | Compatível |
| Alertas | Janelas v2 completas alimentam regras conservadoras de workload; alertas existentes usam seus próprios consumidores | CPU/RAM/disco/rede v2 preservam identidade por dispositivo e exigem janelas consecutivas conforme regra | Sem mudança de threshold ou geração de alerta |

## Defeitos confirmados e correções

1. **Dashboard de capacidade em histórico misto:** o endpoint era classificado como
   v2 pela existência de qualquer amostra v2 em toda a história. Quando o período
   primário não continha nenhuma janela v2, a tela recebia um resumo v2 vazio e
   descartava amostras legacy recentes. Agora cada período usa v2 se contém ao
   menos uma janela v2; se não contém nenhuma, usa o resumo legacy desse período.
   Quando há v2 no período, não se mistura legacy e v2 para preencher lacunas.
2. **Resumo da configuração da telemetria:** o cartão lia somente colunas planas
   de rede e disco. Essas colunas permanecem nulas para taxas v2 por dispositivo,
   corretamente, então o cartão mostrava “não informado” apesar de existirem
   métricas em `metric_summaries`. A API agora acrescenta `last_continuous_metrics`
   com a projeção v2 por dispositivo/interface; o frontend exibe médias da janela
   com seus nomes/unidades e mantém o caminho legacy sem alteração semântica.
   Metadados remotos continuam escapados na renderização.
   A referência estática do script foi versionada para invalidar caches antigos.

## Limitações conhecidas

- `network_received_bytes`/`network_sent_bytes` legacy são contadores; v2 armazena
  `received_bytes_per_sec`/`sent_bytes_per_sec`. São exibidos separadamente e não
  comparados como se fossem a mesma unidade.
- Uma janela v2 presente, mas sem determinada métrica, não é preenchida com dado
  legacy. Isso mantém visível a qualidade real, sem misturar fontes.
- Após compactação, o rollup conserva agregações, mas a qualidade reduzida pode
  não conservar metadados por dispositivo; hashes ainda identificam grupos, mas
  nomes podem ficar indisponíveis. Nenhuma mudança foi feita porque eventual
  custo de armazenamento precisa ser medido antes.
- Não foram consultados bancos, endpoints ou servidores de produção. Testes com
  dados sintéticos comprovam contratos de código, não disponibilidade operacional.

## Validação local

- Django/SQLite: 71 testes de configuração, analytics, API de resumo e capacidade
  aprovados. Uma rodada adicional de 67 testes de telemetria contínua,
  configuração e capacidade também passou.
- JavaScript: 5 testes unitários focados do cartão e cache estático — aprovados.
- Teste visual Playwright não executou nesta estação: o pacote `playwright` não
  está instalado neste worktree. O comando completo reportou `MODULE_NOT_FOUND`;
  não indica falha visual do código.
- `manage.py check`: passou durante a execução Django.
- PostgreSQL, Windows/.NET e gates remotos: pendentes de CI desta branch.
- Sem migrations geradas ou alterações no agente.
