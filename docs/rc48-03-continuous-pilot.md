# RC48-03 — Preparação do piloto de telemetria continuous

Este documento prepara comparação e homologação. Não habilita `continuous`, altera endpoints, muda retenção ou autoriza publicação. O instalador continua gravando `telemetryMode=legacy`; qualquer agente com a instrumentação desta PR precisará de nova release assinada.

## Instrumentação disponível

| Medida | Origem | Unidade/semântica | Limitação |
|---|---|---|---|
| CPU do endpoint, RAM, discos e interfaces | Leitor nativo Windows, janela v2 de 60 s | Métricas por janela; CPU/RAM e rates de disco/rede com qualidade e leituras válidas | O primeiro delta e contadores inválidos permanecem desconhecidos; rates de interface representam o SO, não tráfego Internet faturável |
| CPU e memória do agente | `agent.cpu_percent`, `agent.working_set_bytes` | CPU normalizada pelos processadores lógicos; working set em bytes | CPU não é CPU total do endpoint |
| Duração e erros de coleta | `agent.collection_duration_ms`, `agent.telemetry_errors_count` e estados por grupo | Duração em ms; contadores e qualidade | `telemetry_errors_count` ausente continua desconhecido nos resumos JSON |
| Fila persistente v2 | `agent.telemetry_queue_pending_windows`, `agent.telemetry_queue_pending_bytes`, `agent.telemetry_queue_oldest_age_seconds` | Quantidade/tamanho de arquivos JSON candidatos e idade do mais antigo, amostrados pelo mesmo leitor e agregados por minuto | Snapshot em memória atualizado pelo ciclo já existente de escrita/trim/upload/ACK. Arquivos inválidos podem contar até serem lidos e movidos para quarentena; após remover o item mais antigo, a idade fica desconhecida até a próxima varredura normal, em vez de reportar zero falso |
| Perda/quarentena da fila v2 | `agent.telemetry_queue_lost_windows`, `agent.telemetry_queue_corrupt_windows` | Contadores do processo desde sua inicialização | Reinicializam com o processo; correlacionar com eventos JSONL e intervalos de uptime |
| Payload de telemetria enviado | `agent.transmitted_bytes_total` existente | Bytes serializados de payloads Live e v2 cujo envio foi aceito pelo cliente HTTP | Estimativa de payload da aplicação; não inclui cabeçalhos, TLS, retransmissões ou overhead de rede |
| Banco | `report_telemetry_storage --endpoint <UUID>` | Contagens exatas na janela pedida, amostras limitadas de qualidade/tamanho e tamanho global da tabela PostgreSQL | `--sample-limit` limita materialização da amostra, não o custo das agregações. O tamanho físico total não é atribuível ao endpoint |
| Jobs | `AgentJob`, ocorrências de automação e estado/ACK persistidos | Contagens e tempos por endpoint/job | Não misturar jobs do agente com estado do scheduler interno de análises |

As métricas de fila são adições sob o namespace existente `agent.*` em janelas v2. O backend aceita métricas desse namespace e as persiste em `metric_summaries`; não há migration, mudança de autenticação, ACK, retenção ou protocolo do agente. O caminho legacy não recebe nem consome essas métricas.

## Pré-condições antes de qualquer VM

1. Resolver a identidade do piloto por UUID e `machine_id`; hostname duplicado bloqueia o teste.
2. Registrar versão do agente/Tray, modo efetivo, uptime, heartbeat, capacidade, flags e estado dos jobs. Confirmar os valores carregados pelo serviço, não apenas defaults do código.
3. Em `legacy`, registrar contagem, tamanho, hash e horário do arquivo `ProgramData\NightOwl\State\telemetry-buffer.json`, se existir. Não apagar, renomear nem migrar o arquivo. `TelemetryBuffer` preserva até os limites configurados; a fila v2 não drena esse arquivo.
4. Registrar baseline legacy de pelo menos 24 horas sob carga representativa, sem executar simultaneamente os dois coletores.
5. Conferir espaço livre, baseline PostgreSQL, tráfego de rede, jobs/ACK e relógio UTC. Aprovar previamente limites de CPU/RAM, armazenamento e qualidade com o responsável operacional.
6. Confirmar backup/checkpoint e procedimento de restauração da configuração antes de uma futura mudança manual do modo.

## Critérios mensuráveis propostos (aprovação operacional pendente)

- Observar `continuous` por pelo menos 48 horas com serviço e backend disponíveis; comparar janelas de mesma carga com o baseline legacy, sem execução paralela.
- Em intervalos explicitamente classificados como online, exigir cobertura de janelas completas (`received_samples=6`) de pelo menos 95% das janelas esperadas. Separar intervalos offline/manutenção no relatório, sem convertê-los em sucesso.
- Após restaurar conectividade, verificar que a fila v2 diminui e zera em até 10 minutos, compatível com o ciclo de envio e backoff atual; se houver nova perda ou corrupção, reprovar e preservar logs/arquivos para diagnóstico.
- Exigir zero crescimento dos contadores de perda/quarentena durante o período estável e nenhuma permanência no limite de bytes/quantidade da fila.
- Comparar P50/P95 de `agent.cpu_percent`, `agent.working_set_bytes`, duração de coleta, payload enviado e tamanho PostgreSQL; os tetos numéricos precisam ser acordados antes do piloto com base na capacidade medida, não escolhidos após observar o resultado.
- Confirmar somente schema v2 novo no intervalo `continuous`; não misturar nem deduplicar legacy e v2 por heurística. O histórico anterior permanece identificado como legacy.
- Confirmar que jobs, ACKs, heartbeat e inventário mantêm seus resultados e cadências anteriores.

## Procedimento reversível futuro

1. Obter autorização específica para um endpoint já resolvido, janela e release assinada; manter instalação em `legacy` por padrão.
2. Capturar estado/hash/tamanho da fila v1 e configuração; confirmar que a fila não contém resultados pendentes não tratados. Não mudar o modo enquanto houver dúvida sobre os dados v1.
3. Parar o serviço do agente de forma controlada, preservar estado e aplicar apenas a configuração individual aprovada para `continuous`; iniciar o serviço e verificar heartbeat/capacidade/métricas. Nunca rodar os dois pipelines ao mesmo tempo.
4. Durante a observação, manter intacta a fila v1. Verificar a fila v2 e ACK. Se houver falha, parar o agente, preservar a fila v2 e logs, restaurar o arquivo de configuração anterior e reiniciar em `legacy`; não apagar nenhuma fila.
5. Após retorno a `legacy`, confirmar que a telemetria v1 volta a ser enviada e que dados v2 pendentes foram preservados para análise. Não reenfileirar ou converter janelas manualmente.

Essa reversão é operacional e não desfaz retenção já aplicada nem dados enviados. Não é autorizada por este PR.

## Itens que esta etapa não homologa

- Carga/custos em frota ou em PostgreSQL de produção.
- Integridade física dos identificadores de discos/interfaces em hardware real.
- Compatibilidade e drenagem de uma fila legacy real.
- Rollback do modo em endpoint.
- Publicação/assinatura ou instalação da nova versão Windows.
- Identidade do endpoint piloto, que precisa ser reconciliada fora desta alteração.
