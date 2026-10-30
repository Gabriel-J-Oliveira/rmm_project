# RC46 — Footprint do agente Windows

Data: 2026-10-30. Branch `feature/rc46-agent-cleanup-closure`.
Referência recebida: `6fe53cac4f064013fa6993b8c4c56041450d26b9`.
Medições desta rodada ficam em `artifacts/rc46-final-review/`.

## Método e classificação

Não houve instalação, start/stop de serviço, alteração de tarefa ou tráfego para
servidor corporativo. Foi encontrado localmente `NightOwlAgentDotNet` **Stopped**
em consulta somente leitura; sua origem/versão não foi investigada e seu estado
não foi alterado. Esse serviço não foi usado como endpoint de teste.

Comparação de pacote: checkout separado e detached no SHA recebido, sem alterações
de código produtivo, usando SDK 8.0.422/runtime 8.0.28, win-x64, Release,
self-contained, versão e filtro de arquivos iguais aos do pacote novo. Ambos
foram gerados com `New-NightOwlDevelopmentArtifact.ps1`; o baseline usa
`-SourceRoot artifacts/rc46-baseline-source`. Nenhuma branch de origem foi alterada.
Os ZIPs são artefatos unsigned para inspeção, não substitutos do builder oficial.
Os diretórios e revisões de origem diferem; metadados/debug paths/timestamps de
build podem contribuir para deltas pequenos. A comparação é de composição e
tamanho observado, não uma experiência de CPU nem uma promessa de bytes idênticos.

Medição de processo: `Windows.Tests.exe --footprint-observation` cria Worker real
e telemetria nativa, mas conserva inventário não devido e usa HttpMessageHandler
em memória. Não faz DNS/HTTP externo nem instala um serviço. Não é simulação dos
contadores nativos, mas o contexto operacional/HTTP é fixture. O processo inteiro
do harness inclui CLR, hosting, bibliotecas de teste e coletores.

## Medição local do Worker

| Métrica | Resultado | Classe e limite |
| --- | ---: | --- |
| Janela observada | 30,328 s | Local, curta, sem soak |
| Primeiro heartbeat capturado | 113,638 ms | Startup do Worker até handler local, sem rede/TLS |
| CPU média normalizada por CPUs lógicas | 0,245% | Processo do harness, não CPU de serviço instalado |
| Working set máximo amostrado | 63.684.608 B | Cerca de 60,73 MiB; amostragem por segundo |
| Threads máximas amostradas | 22 | Inclui CLR/ThreadPool/hosting/PDH, não 22 threads dedicadas do agente |
| Handles máximos amostrados | 444 | Processo completo |
| ReadTransferCount delta | 1.763.289 B | GetProcessIoCounters; não bytes físicos de disco |
| WriteTransferCount delta | 13.967 B | GetProcessIoCounters; inclui I/O do processo, não write amplification |
| Requests do handler | 19 | Contagem lógica, sem sockets/requisições reais na rede |
| Corpos outbound serializados | 1.927 B | Não inclui headers, TLS, retransmissões ou wire bytes |
| Arquivos na fixture | 5 / 11.737 B | Estado/saída local, não tamanho instalado |

Não existe medição pareada de CPU/RSS/IO do serviço instalado antes/depois.
Não é legítimo atribuir uma redução percentual da CPU da frota a esses números.

## Residentes, rotinas e recursos

| Componente | Responsabilidade e duração | Recursos/limites relevantes |
| --- | --- | --- |
| Windows | Um serviço SYSTEM | Heartbeat independente, inventário, jobs e telemetria no mesmo processo |
| Tray | Um processo por sessão interativa | Mutex Local da sessão; timer WinForms 30 s; tolerância monotônica 120 s |
| Updater | Sob demanda | Runner e lock global; quiesce 30 s, replace 30 s, health 180 s; rollback preservado |
| Uninstaller | Sob demanda | Autorizações, receipts/ACK; Tray 10 s, agente 15 s e binários 25 s |
| Diagnostics | Sob demanda | Limites de entrada/ZIP, sanitização e staging removido no finally |
| PowerShell/CIM do inventário | Processos temporários | Sem PowerShell no heartbeat; consultas segundo cadências e cancelamento existentes |
| PowerShell legado/NSSM/tarefa | Condicional, depende da frota | Não é residente exigido pelo pacote .NET; coexistência não foi automaticamente desabilitada |

O inventário básico/rede permanece diário, hardware detalhado semanal, volumes
horário e ancilares conforme collectSeconds. Telemetria contínua permanece em
~10 s/leituras e janelas UTC de minuto; ring de 180, canal 16, lote até 24 janelas,
fila default 128 MiB/192 h. Jobs mantêm signal default 2 s, slot pesado único,
admissão/journal/resultados limitados. Não há novo serviço, loop ou timer.

O novo `tray-status.json` é uma projeção de campos públicos, sem token; só é
reescrito quando algum campo muda ou o arquivo precisa ser recuperado. Substitui
leituras de Config/State protegidos e do tail de log no caminho normal do Tray.
Fallback legado e tail limitado a 1 MiB/400 linhas continuam disponíveis.
Todos os JSONs lidos pelo Tray agora usam snapshot de tamanho, limitado a 1 MiB.

Contadores PDH deixam de abrir no construtor e abrem somente na primeira leitura
nativa solicitada. Perfil desabilitado/legado não paga esse bootstrap. Após uso,
o leitor do pipeline continua com vida de serviço para reutilização; a revisão
não introduziu destruição concorrente de handle enquanto uma leitura está ativa.
Modo desconhecido não coleta silenciosamente como legacy: registra diagnóstico
e falha somente na inicialização da telemetria, preservando os outros loops.

## Disco, arquivos e evidência

| Recurso | Limite/decisão |
| --- | --- |
| Logs técnicos | 10 MiB ativo + cinco rotações, 30 dias nas rotações; registro até 64 KiB |
| Tray por usuário | `%LOCALAPPDATA%\NightOwl\Logs\agent-tray-user.jsonl`, mesma política; nenhuma ACL de ProgramData ampliada |
| Status do Tray | Arquivo pequeno, fixo e substituído atomicamente; campos allowlisted |
| Resultados sem ACK | Sem expurgo por idade; admissão por registros/bytes do envelope; backpressure |
| Quarentena de resultados | Ocupa a mesma quota da fila; nomes únicos, sem overwrite; preservada para reconciliação |
| Recibos enviados | Até 1.000/14 dias, desde o ACK |
| Journals/outcomes | Limites V2 existentes; preservação de resultado exato e ownership |
| Temporários | Somente finally/ownership do fluxo responsável; removido cleanup duplicado de pacote no caminho de sucesso do installer |
| Backups/runner incerto | Evidência de recovery, sem expurgo genérico por idade |
| Cache/pacotes públicos antigos | Não varridos indiscriminadamente; dependem de estado/consumidores |

Fragmento de log interrompido é preservado numa linha separada; a próxima entrada
válida continua legível. Testado com falha de escrita parcial injetada. Disco
cheio físico e power cut exigem volume/VM descartável; não foram simulados
enchendo o disco de desenvolvimento.

Uninstall preserva logs/evidências, inclusive os arquivos por usuário. Não foi
adicionada varredura de perfis para purgar logs de outras sessões; a quota por
perfil continua limitada. Sem nova escrita, expiração de arquivos de log não é
um serviço automático de limpeza pós-uninstall.

## Dependências e distribuição

Baseline reconstruído: **512 arquivos**, **171.508.346 B** de payload; 494 DLLs,
seis EXEs, dez JSONs, um ICO e um TXT marcador de desenvolvimento. Há **221
satellite resource DLLs**. O relatório de comparação JSON registra o pacote final.

Duplicatas byte a byte observadas e justificadas:

- `mscordaccore.dll` e seu alias versionado `mscordaccore_amd64_amd64_8.0.2826.26413.dll`:
  nomes de distribuição do runtime para diagnóstico/DAC. Não removidos com base
  apenas no hash igual.
- Runtimeconfigs de Windows/Diagnostics/Uninstaller e de Tray/Updater: conteúdo
  igual por grupo, mas cada apphost busca seu próprio nome. Não removíveis sem
  mudar o contrato de execução.

O diretório publicado único já compartilha runtime/Shared/dependências entre os
cinco componentes; não são cinco runtimes completos no ZIP. Nenhum pacote NuGet
novo foi acrescentado. EventLog, SCM, WinForms, hosting e HTTP têm consumidores
reais; BCL/interop/reflection e runtime também não são mortos por ausência de uma
referência textual direta no código da aplicação.

PDB, scripts arbitrários, config/identidade/estado, logs e temporários ficam fora
dos ZIPs de desenvolvimento comparados. O pacote oficial mantém sua allowlist de
um único installer PowerShell e gates de trust/hash/assinatura. O script legado
e NSSM não são copiados para esse payload .NET.

Não aplicados: remoção manual de DLLs do runtime, trimming, AOT, single-file ou
filtro de idiomas sem matriz de compatibilidade. O próprio SDK local rejeita
`PublishTrimmed` com `UseWindowsForms` em
`Microsoft.NET.RuntimeIdentifierInference.targets`, erro
`TrimmingWindowsFormsIsNotSupported`. Eliminar recursos de outros idiomas também
retiraria diagnósticos localizados sem inventário da frota. São decisões
intencionais de compatibilidade, não oportunidades esquecidas.

## Resultado quantitativo do pacote

| Artefato com o mesmo SDK/filtro | Arquivos | Payload (B) | ZIP (B) |
| --- | ---: | ---: | ---: |
| Baseline recebido 6fe53ca, reconstruído | 512 | 171.508.346 | 72.010.551 |
| Entrega local anterior, antes desta revisão | 512 | 171.512.444 | 72.012.389 |
| Código final 7b8ce4f | 512 | 171.518.586 | 72.014.906 |

Variação contra baseline: **+10.240 B de payload (+0,00597%)** e **+4.355 B de ZIP**.
Contra entrega anterior: +6.144 B de payload / +2.517 B de ZIP. Não houve redução
do pacote; houve pequeno crescimento para corrigir configuração, recovery e a
fronteira de dados do Tray. Não foram removidas DLLs necessárias para produzir
uma porcentagem artificial de economia.

Inventário PE final: 476 arquivos managed (145.708.272 B), 24 native PE
(25.547.656 B), dez JSONs (191.020 B), ICO (71.445 B) e marcador TXT (193 B).
Os seis EXEs são os cinco componentes e `createdump.exe`, ferramenta do runtime
preservada para diagnóstico. Satellites: 221 arquivos / 16.263.184 B.

Artefato final:
`artifacts/rc46-development/20261030T153655Z-475a48100f684ecc8ab393df89ddc6d9/`.
ZIP SHA-256:
`427a0ce47d878806b827e74dcc9c6174fc806c8840478b6000f68228d68acc6c`.

Verificação independente em Python confirmou cada tamanho/hash dos 512 arquivos,
os source inputs e a exclusão de PS1/PDB/log/tmp/lock. Manifesto detalhado e
comparação ficam em `artifacts/rc46-final-review/package-comparison.json`.
O ZIP não é assinatura, Pilot homologado nem instalação. Não foi distribuído.

## Indisponível e estimativas

Complemento de ambiente: foi preparado PostgreSQL portátil 16.15 a partir da
[distribuição Windows indicada pelo PostgreSQL](https://www.postgresql.org/download/windows/)
e dos [binários ZIP da EDB](https://www.enterprisedb.com/download-postgresql-binaries).
Os executáveis ficaram em `.local-tools/`, sem serviço Windows, usando cluster
novo autenticado SCRAM e bind exclusivo 127.0.0.1. As 717 regressões passaram em
143,662 s com zero skips, incluindo os 29 gates PostgreSQL e os browsers. As
bases Django foram removidas e o servidor próprio foi encerrado. Essa ferramenta
de testes não integra o ZIP/RMM nem o footprint do agente medido acima.

Na preparação do cluster, um helper ficou aguardando pipes herdados do processo
background. Somente esse helper de propriedade da tarefa foi encerrado; os
comandos subsequentes usaram a instância isolada já iniciada. Nenhuma instância
PostgreSQL preexistente foi parada ou reconfigurada. A URL/senha sintética não
foi impressa nem adicionada ao Git.

CPU/RSS/startup sustentados em serviço instalado, tráfego/socket real, tamanho
instalado e comportamento em várias sessões/Explorer/SCM permanecem sem medição.
O limite de 60 MiB de logs é por caminho; em múltiplos perfis, cada Tray possui
seu diretório e quota. Evidências/rollback não recebem uma quota global destrutiva.
Contagem de loops, cadências e limites acima é inventário de código, não contagem
medida de processos de uma frota. Não extrapolar essa observação local para
centenas de endpoints.
