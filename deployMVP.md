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

## M2 - Telemetria v1: LAB PASS

A revisao do primeiro commit foi `PASS_WITH_FIXES`. Os tres problemas HIGH
eram: falha de carga do buffer encerrando a telemetria, delta de rede calculado
sobre um total de interfaces mutavel e coleta sincrona ainda ativa apos timeout
sem shutdown limitado. O hardening e a review final passaram; Telemetry Core v1
foi publicada na RC41 e validada no laboratorio. Continua desabilitada por default.

- Release: `0.1.1.0-rc41`, source `a00e05a13efa86bb003df58891d1aac08566bdf4`.
- LAB: `CS-FISCAL-02`, endpoint `3c9db4f4-7dff-4051-8bbe-7791993eef5d`.
- Observacao overnight: 122 amostras, 122 `sample_id` distintos, zero
  duplicatas, zero intervalos acima de 7 minutos e zero erros de telemetria.
  Store-and-forward apos reboot/restart validado. Resultado:
  `OVERNIGHT_TELEMETRY_PASS`.

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

## M1 - Hardware Inventory 2.0: CODE_REVIEW_PASS

Esta implementacao adiciona `hardware.memory` (modulos e slots),
`hardware.physical_disks` (midia, barramento e associacao a letras quando
confiavel), detalhes de CPU e `hardware.battery`. Os campos existentes de
hardware e os volumes logicos em `disks` permanecem no contrato. A coleta
enriquecida ocorre no ciclo de inventario; o heartbeat nao recebe as consultas
adicionais de memoria fisica ou storage. Nenhum rollout foi iniciado nesta etapa.

A implementacao inicial foi concluida. A primeira review concluiu
`NEEDS_FIXES`; a correlacao de storage, o fallback WMI e o merge de bateria
foram endurecidos. A segunda review concluiu `NEEDS_FIXES` pelo timeout
compartilhado entre hardware basico e enriquecimento. O isolamento foi corrigido
e a review final concluiu `PASS`, com risco de codigo `LOW`. M1 ainda nao e
`LAB PASS`: falta validar em hardware Windows real.

O core roda em PowerShell independente (timeout de 12 s), o enriquecimento de
RAM/bateria em outro (15 s) e storage fisico em outro (18 s). Falha, JSON
invalido ou timeout do enriquecimento nao descarta fabricante, CPU, BIOS,
memoria total ou estado basico da bateria. O enriquecimento e fail-soft e a
associacao de storage e fail-closed. Sao tres subprocessos intencionais por
inventario; robustez e isolamento de falhas foram priorizados sobre
micro-otimizacao.

Letras de unidade e disco do sistema so sao atribuidos a um disco fisico com identificador unico
e sem evidencia de camada RAID, Storage Spaces ou virtual. Os campos
`cpu.socket`, `cpu.processor_id` e `cpu.max_clock_mhz` representam somente o
primeiro `Win32_Processor` (CPU0) neste MVP.

## Historico da base integrada da RC41

- Base: `origin/main` em `d6ab7a72974797cb284bb90acb76b74e9c996f55`.
- Correcao de `backup-manifest.json`: `a3418c14de61df73b24443ce76afdcfc6bc0d6e6`.
- Telemetry Core: `a7c1d7bfe8e01b893361d8539e72e16a46ac9829`.
- Roadmap do MVP: `513d70d57e335edddce54a55b2459ffa4df28d84`.
- Hardening da telemetria: `535c53fc695c4ad4d165606e728dbdc18415b968`.
- Validacao local integrada: builds e testes .NET, testes Django, grafo de
  migrations e regressao do backup manifest passaram.

Esta secao registra a integracao que precedeu o deploy do backend, a
publicacao da RC41 e o canario LAB descrito acima. O proximo gate de M1 e
validacao em Windows real antes de qualquer nova RC.
