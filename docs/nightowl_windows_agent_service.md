# NightOwl Windows Agent - modo servico

## Resumo

A versao `0.4.0` adiciona uma arquitetura preparada para o agente Windows rodar como servico residente, preservando o modo atual por tarefa agendada como fallback.

O modo legado continua em `C:\RMM`. O modo servico usa `C:\ProgramData\NightOwl`:

- `C:\ProgramData\NightOwl\Agent\RmmAgentService.ps1`
- `C:\ProgramData\NightOwl\Agent\RmmAgent.config.json`
- `C:\ProgramData\NightOwl\Agent\agent.state.json`
- `C:\ProgramData\NightOwl\Logs\`
- `C:\ProgramData\NightOwl\Packages\`
- `C:\ProgramData\NightOwl\Cache\`

## Ciclos do servico

O loop do servico possui ciclos independentes:

- `heartbeat`: envia heartbeat leve para `/api/agent/heartbeat/`;
- `jobs`: consulta jobs pendentes e executa apenas tipos seguros permitidos;
- `system_inventory`: coleta sistema operacional, fabricante, modelo, serial, idioma, timezone, usuario e uptime;
- `network_inventory`: coleta IPs, MACs, adaptadores, gateway e DNS;
- `hardware_inventory`: coleta CPU, memoria, BIOS, TPM e bateria quando disponivel;
- `disk`: coleta volumes, filesystem, total, livre e percentual usado;
- `security`: coleta Defender, antivirus via SecurityCenter2, firewall, BitLocker, ferramentas remotas e administradores locais;
- `software`: coleta softwares pelo registro do Windows, sem `Win32_Product`;
- `full_inventory`: consolida as coletas em um snapshot completo;
- `patches`: coleta reboot pendente, ultima verificacao de Windows Update e quantidade de updates pendentes quando seguro.

O heartbeat permanece leve. As coletas pesadas rodam em intervalos proprios no `RmmAgent.config.json`.

## Endpoints de coleta

O config possui `collectionEndpoints`:

- `systemInventoryUrl`
- `networkInventoryUrl`
- `hardwareInventoryUrl`
- `diskInventoryUrl`
- `securityInventoryUrl`
- `softwareInventoryUrl`
- `fullInventoryUrl`
- `patchStatusUrl`

Quando a URL estiver vazia, a coleta fica pronta localmente, registra log/estado e nao e enviada. Quando a URL existir, o agente envia o payload com `Authorization: Bearer <agent_token>`.

## Jobs tecnicos

Endpoints planejados:

- `GET /api/agent/jobs/pull/`
- `POST /api/agent/jobs/result/`

O agente deriva essas URLs do `serverUrl`/`heartbeatUrl` quando `jobsPullUrl` e `jobsResultUrl` estiverem vazios. Tambem e possivel configurar as URLs explicitamente.

Tipos permitidos inicialmente:

- `force_inventory`
- `collect_disks`
- `collect_security`
- `collect_software`
- `ping`
- `collect_logs`
- `windows_update_scan`

Controles:

- `jobs.enabled`
- `jobs.timeoutSeconds`
- `jobs.allowedTypes`
- `jobs.maxStdoutChars`
- `jobs.maxStderrChars`
- `jobs.executedJobHistoryLimit`
- `jobs.resultRetryLimit`

O agente registra:

- `job.received`
- `job.started`
- `job.completed`
- `job.failed`
- `job.result_sent`
- `job.result_send_failed`

Resultados nao enviados ficam em fila local no `agent.state.json` em `pendingJobResults`. Jobs executados ficam em `executedJobs` para evitar duplicidade.

Teste manual:

```powershell
powershell.exe -ExecutionPolicy Bypass -File "C:\ProgramData\NightOwl\Agent\RmmAgentService.ps1" -RunJobsOnce -DebugMode
```

## Estado e logs

Cada ciclo atualiza `agent.state.json` em:

- `cycles.<nome>.lastRunAt`
- `cycles.<nome>.status`
- `cycles.<nome>.metadata`
- `lastCollections.<nome>`

Falhas parciais de coleta nao derrubam o servico. O payload retorna `status = partial` e o JSONL registra `collection.partial_failure`.

## Instalacao

O servico se chama `NightOwlAgent`, com display name `NightOwl RMM Agent`, startup automatic delayed start e execucao como `LocalSystem`.

Para registrar corretamente um script PowerShell como servico Windows, use NSSM:

```powershell
powershell.exe -ExecutionPolicy Bypass -File "\\192.168.104.120\controlsul\Comum\_Agents\Install-RmmAgent.ps1" -ServerUrl "http://SERVIDOR:8000/api/agent/heartbeat/" -EnrollmentToken "enroll_xxxxx" -InstallAsService -NssmPath "\\192.168.104.120\controlsul\Comum\_Agents\nssm.exe" -RunOnce -RunCheck
```

Busca do `nssm.exe`:

1. `-NssmPath`;
2. `C:\ProgramData\NightOwl\Agent\nssm.exe`;
3. `<pacote>\nssm.exe`;
4. `<pacote>\tools\nssm.exe`;
5. PATH do Windows.

O instalador registra nos logs qual caminho foi usado e copia o binario para `C:\ProgramData\NightOwl\Agent\nssm.exe` quando ele vem do pacote/PATH.

Sem `-KeepScheduledTaskFallback`, a tarefa agendada `RMM-Agent-Heartbeat` e desabilitada depois que o servico e instalado, evitando heartbeats duplicados. Com `-KeepScheduledTaskFallback`, ela fica habilitada.

## Teste local

```powershell
powershell.exe -ExecutionPolicy Bypass -File "C:\ProgramData\NightOwl\Agent\RmmAgentService.ps1" -RunOnce -DebugMode
```

## Atualizacao

`Update-RmmAgent.ps1` continua preservando config, estado e logs. Quando `C:\ProgramData\NightOwl\Agent` existe, ele tambem atualiza os scripts do servico nesse caminho sem sobrescrever `RmmAgent.config.json` nem `agent.state.json`.

## Limites desta fase

- Nao executa instalacao real de software.
- Nao executa scripts remotos arbitrarios.
- Jobs nesta etapa sao restritos a coletas e verificacoes seguras.
- Nao substitui automaticamente o agente legado em ambientes que ainda dependem da tarefa agendada.
