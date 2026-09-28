# NightOwl Asset Intelligence / OBZ - roadmap do MVP

| Etapa | Objetivo | Resultado |
| --- | --- | --- |
| M1 | Inventario de hardware enriquecido | Foto completa de cada equipamento |
| M2 | Telemetria de performance | Historico CPU/RAM/disco |
| M3 | Backend e retencao | Dados armazenados de forma eficiente |
| M4 | Agregacoes e qualidade | Medias, P95, cobertura e tendencias |
| M5 | Motor de diagnostico | Trocar / RAM / SSD / manter |
| M6 | Frontend OBZ | Visao consolidada e por endpoint |
| M7 | Piloto real | Validar em pequeno grupo |
| M8 | Deploy da frota | Coleta ampla |
| M9 | Janela de 7 dias | Dataset do OBZ |
| M10 | Fechamento do MVP | Relatorio final confiavel |

## Estado da telemetria v1

A revisao do primeiro commit foi `PASS_WITH_FIXES`. Os tres problemas HIGH
eram: falha de carga do buffer encerrando a telemetria, delta de rede calculado
sobre um total de interfaces mutavel e coleta sincrona ainda ativa apos timeout
sem shutdown limitado. O hardening e a review final passaram; Telemetry Core v1
esta pronta para a preparacao da RC41, ainda desabilitada por default.

- Opt-in: `telemetryEnabled=false` por default. Coleta local a cada 300 segundos
  (5 minutos); envio normal em lotes a cada 3600 segundos (aproximadamente 1 hora).
  Backlog confirmado e drenado em lotes de ate 24 com intervalo de 1 minuto;
  falha de envio volta ao intervalo normal para evitar carga acelerada.
- Spool no disco, sob `State`, com limite de 2304 amostras e 192 horas (8 dias).
  O arquivo e a fonte de verdade; apenas a contagem e o lote atual ficam
  residentes em memoria entre operacoes. Leitura/regravacao integral e
  transitoria, limitada a 32 MiB. Overflow descarta as amostras mais antigas e
  gera evento. ACK so remove amostras apos HTTP 2xx; `sample_id` permanece estavel
  em retry e a unicidade `(endpoint, sample_id)` impede duplicacao no backend.
- Durante o MVP, manter todas as amostras raw no servidor pela semana de
  observacao, sem purge automatico. Retencao e rollup serao definidos em M3/M4.
  A API aceita timestamps de ate 9 dias para cobrir o spool de 8 dias mais
  margem de transporte/relogio.
- Limite conhecido: em hosts com mais de 64 processadores logicos,
  `GetSystemTimes` reporta somente o grupo de processadores primario da thread.
  Validar no piloto; suporte completo a processor groups fica para depois.
- Throttling especifico no POST de telemetria fica como hardening anterior ao
  rollout amplo. O MVP mantem autenticacao e limites de corpo/lote atuais.

## Base integrada para futura RC41

- Base: `origin/main` em `d6ab7a72974797cb284bb90acb76b74e9c996f55`.
- Correcao de `backup-manifest.json`: `a3418c14de61df73b24443ce76afdcfc6bc0d6e6`.
- Telemetry Core: `a7c1d7bfe8e01b893361d8539e72e16a46ac9829`.
- Roadmap do MVP: `513d70d57e335edddce54a55b2459ffa4df28d84`.
- Hardening da telemetria: `535c53fc695c4ad4d165606e728dbdc18415b968`.
- Validacao local integrada: builds e testes .NET, testes Django, grafo de
  migrations e regressao do backup manifest passaram.

Proximo gate: deploy do backend e build/publicacao da RC41 mediante aprovacao
separada. Nenhuma RC41 foi criada, nenhum endpoint recebeu telemetria e nenhum
rollout ou campanha foi alterado nesta integracao.
