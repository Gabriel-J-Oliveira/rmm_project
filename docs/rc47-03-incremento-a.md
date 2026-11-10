# RC47-03A — Última análise persistida

## Escopo

Esta branch adiciona persistência manual e controlada de uma análise de desempenho por endpoint a partir de uma janela v2 de 60 segundos já persistida. Não coleta, agenda, executa job, abre sessão ao vivo, modifica a retenção corrente nem altera o agente Windows. Não ativa processamento recorrente.

## Dados de origem e qualidade

- A fonte é exclusivamente `EndpointPerformanceSample` com `schema_version=2` e `resolution_seconds=60`, vinculada ao UUID do endpoint. O `machine_id` atual precisa estar presente e não pode divergir da identidade já registrada na análise.
- O payload salvo contém estatísticas agregadas: `count`, `sum`, `min`, `max`, `weight`, `weighted_sum` e, quando disponível, distribuição. A análise não procura nem presume seis linhas brutas de 10 segundos.
- A janela precisa ter timestamps timezone-aware coerentes com a linha, duração de 60 segundos, `expected_samples=6`, pelo menos 5 leituras recebidas (cobertura mínima de 83,3%) e ao menos uma métrica primária CPU ou memória. Cada métrica é revalidada.
- CPU, memória, disco e interfaces são projetados pela função existente de telemetria v2, com unidades e metadados já definidos. Métrica ausente continua desconhecida; não é convertida em zero. Disk/interface são opcionais e não se infere ausência física do dispositivo.
- A janela conserva contagem recebida/esperada, cobertura, contagem de amostras ausentes e status por grupo. O agregador do agente não conserva timestamps individuais das leituras faltantes; por isso `gap_intervals` é `null` e `gap_positions_available=false`.
- A contagem plana legada `telemetry_errors_count` continua sujeita à limitação descrita em `rc47-incremento-1-telemetry.md`; a análise usa o resumo v2 e conserva `null` quando a métrica não foi reportada.

## Persistência e idempotência

`PersistedTelemetryAnalysis` guarda por endpoint o último resultado válido, identidade, fonte/versão/resolução, janela, estado e motivo da tentativa, horários de tentativa/sucesso e estatísticas históricas compactas. Uma rejeição só atualiza o estado da tentativa: não substitui a última análise válida nem seus agregados.

`TelemetryAnalysisIncorporation` mantém um recibo pequeno com chave idempotente derivada de UUID do endpoint, `machine_id`, `sample_id`, schema/resolução e versão da política. O identificador público da análise é determinístico para esse recibo. O endpoint é bloqueado primeiro; em seguida, a linha de estado e a janela são bloqueadas. Esse ordenamento serializa workers e é compatível com o lock de endpoint usado pela compactação. Recibo, última análise e agregado são gravados na mesma transação; há também unicidade no banco por endpoint/chave e por analysis ID.

O histórico preserva por métrica contagem de leituras, soma, peso, soma ponderada, mínimo/máximo, primeira/última ocorrência, distribuição e contagens de métricas ausentes. Também mantém estatísticas separadas por janela `complete`/`partial` e cobertura/status de qualidade. Não calcula média de percentis: distribuições percentuais mergeáveis são mantidas quando disponíveis; agregações sem distribuição não prometem percentis.

## Operação

O comando `persist_latest_telemetry_analysis` é somente leitura por padrão e limita cada chamada a no máximo 100 endpoints (padrão 20). Pode-se restringir a um UUID com `--endpoint`.

```powershell
python manage.py persist_latest_telemetry_analysis --endpoint <UUID>
```

Para persistir em um ambiente controlado, é necessário configurar explicitamente `NIGHTOWL_PERSISTED_TELEMETRY_ANALYSIS_MANUAL_APPLY_ENABLED=true` nesse ambiente e chamar o comando com `--apply`:

```powershell
python manage.py persist_latest_telemetry_analysis --endpoint <UUID> --apply
```

A configuração padrão é `false`. Nenhum scheduler chama o comando e nenhum serviço automático foi habilitado. Offline, identidade ausente/trocada, endpoint inativo, ausência de janela nova ou qualidade insuficiente retornam estados explícitos; offline não gera retry automático. Linhas legacy, janelas compactadas de 5 min/1 h e janelas além dos 7 dias de detalhe nunca são tratadas como janela elegível.

## Agendamento futuro (Incremento B; não implementado)

- Reutilizar `process_job_automations`/scheduler existente somente após revisão de permissão, identidade, lock, health, quarentena e comportamento de indisponibilidade. A análise é trabalho de backend; não criar job do agente nem simular operador.
- Claim futuro deve usar estado persistido por endpoint, lock transacional/lease curta com proprietário e validade, e uma chave idempotente por UUID + `machine_id` + amostra + versão de política. Reivindicação expirada pode ser retomada; transação incompleta não incrementa histórico.
- Limitar concorrência e lote usando o limite atual do scheduler e seu health/lock; confirmar no Incremento B os valores operacionais. Não processar toda a frota num único ciclo.
- Quando endpoint estiver offline, adiar até janela dentro da retenção detalhada, com retry limitado e jitter determinístico por endpoint. O período de primeira análise para endpoint novo permanece uma decisão pendente; nenhuma janela 24/48 h foi assumida.
- A retenção detalhada de 7 dias significa que scheduler interrompido por mais de 7 dias não pode reconstruir janelas perdidas a partir dos rollups de 5 min/1 h. Deve marcar `no_eligible_data` e aguardar a próxima janela v2 elegível; não reter indefinidamente dados detalhados nem alterar a política de retenção atual.
- A frequência pretendida de 15 dias e sua dispersão determinística ainda não foram ativadas. Sessões de Monitoramento ao vivo continuam efêmeras e nunca são fonte da análise persistida.

## Capacidade e limitações

Com 200 endpoints e uma análise por endpoint a cada 15 dias, a ordem de grandeza é aproximadamente 4.867 recibos anuais. Os recibos guardam apenas UUIDs, chave e timestamps; a análise detalhada e o histórico ficam em uma linha por endpoint. O limite atual de 512 chaves de métrica históricas evita crescimento ilimitado por churn de discos/interfaces; um endpoint que exceda o limite tem a nova tentativa rejeitada sem alterar o resultado anterior. O tamanho real dos JSONs PostgreSQL não foi medido com payloads de endpoints Windows representativos; medir em homologação antes de habilitar recorrência.

A seleção e persistência não mexem em `EndpointPerformanceSample`, nos alertas ou nos rollups existentes. Os dados são sintéticos nos testes; avaliação com hardware real e consultas de tendência sobre dados persistidos permanece pendente. Nenhum migration foi aplicado fora do banco de testes.
