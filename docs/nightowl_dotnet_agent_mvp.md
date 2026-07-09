# NightOwl .NET Windows Agent MVP

## Objetivo

O `NightOwl.Agent.Windows` e o agente Windows oficial em .NET. Ele roda em paralelo ao agente PowerShell legado e usa o backend Django atual por APIs REST.

## Servico Windows

- Nome: `NightOwlAgentDotNet`
- Display name: `NightOwl RMM Agent .NET`
- Conta: `LocalSystem`
- Startup: automatic delayed start
- Pasta de instalacao: `C:\ProgramData\NightOwl\AgentDotNet`
- Logs JSONL: `C:\ProgramData\NightOwl\Logs\agent-dotnet.jsonl`
- Estado local: `C:\ProgramData\NightOwl\AgentDotNet\agent-dotnet.state.json`
- Jobs: `C:\ProgramData\NightOwl\Jobs`
- Packages futuros: `C:\ProgramData\NightOwl\Packages`
- Cache: `C:\ProgramData\NightOwl\Cache`

O agente PowerShell legado pode permanecer instalado. O instalador .NET nao remove o legado por padrao.

## APIs usadas

- `POST /api/agent/heartbeat/`
- `POST /api/agent/collect/`
- `GET /api/agent/jobs/pull/`
- `POST /api/agent/jobs/result/`

Todas usam `Authorization: Bearer <agent_token>`.

## Identidade estavel

O agente nunca deve usar hostname como `machine_id`, exceto como fallback temporario extremo. A resolucao segue esta prioridade:

1. `machineId` no `agent.config.json`
2. Estado .NET: `C:\ProgramData\NightOwl\AgentDotNet\agent-dotnet.state.json`
3. Estado PowerShell novo: `C:\ProgramData\NightOwl\Agent\agent.state.json`
4. Estado legado: `C:\RMM\agent.state.json`
5. `HKLM\SOFTWARE\Microsoft\Cryptography\MachineGuid`
6. UUID gerado

O `machine_id` resolvido e salvo no estado .NET e logado como `machine_id.resolved`.

No Django, endpoints sao correlacionados por:

1. `machine_id`
2. `hostname + domain`
3. `serial_number`

Conflitos de identidade geram evento `endpoint.identity_conflict` em vez de sobrescrever silenciosamente o cadastro.

## Configuracao

O instalador gera `agent.config.json` com:

- `agentToken`
- `machineId`
- `serverBaseUrl`
- `heartbeatUrl`
- `collectUrl`
- `jobsPullUrl`
- `jobsResultUrl`
- paths locais
- intervalos
- allowlist de jobs

Se o token estiver ausente ou for placeholder, o agente registra `config.invalid_missing_token` e evita loop agressivo contra a API.

## Instalacao oficial

Publique o agente e copie o conteudo publicado para o share, HTTPS futuro ou caminho local de instalacao.

Exemplo de publish:

```powershell
dotnet publish .\NightOwl.Agent.Windows\NightOwl.Agent.Windows.csproj -c Release -r win-x64 --self-contained true -o .\NightOwl.Agent.Windows\publish\win-x64
```

O publish inclui:

- `NightOwl.Agent.Windows.exe`
- `agent.config.json`
- `Install-NightOwlAgentDotNet.ps1`
- `Uninstall-NightOwlAgentDotNet.ps1`

Exemplo com enrollment token:

```powershell
powershell.exe -ExecutionPolicy Bypass -File "\\192.168.104.120\controlsul\Comum\_Agents\Install-NightOwlAgentDotNet.ps1" -ServerUrl "http://nightowl.control.local" -EnrollmentToken "enroll_xxx" -InstallAsService -RunCheck
```

Exemplo com agent token direto:

```powershell
powershell.exe -ExecutionPolicy Bypass -File "C:\NightOwlAgents\Install-NightOwlAgentDotNet.ps1" -ServerUrl "http://192.168.106.51:8010" -AgentToken "rmm_live_xxx" -InstallAsService -RunCheck
```

Notas:

- Execute como Administrador.
- `127.0.0.1` deve ser usado apenas quando o backend esta na mesma maquina.
- Caminho local funciona apenas na propria maquina.
- Para endpoints de rede, use UNC ou HTTPS futuro.
- O instalador nao imprime token no resumo.

## Parametros do instalador

- `-ServerUrl`: obrigatorio, aceita base URL ou `/api/agent/heartbeat/`
- `-EnrollmentToken`: token de enrollment, trocado por agent token no backend
- `-AgentToken`: token bearer direto do endpoint
- `-InstallPath`: padrao `C:\ProgramData\NightOwl\AgentDotNet`
- `-InstallAsService`: instala/atualiza o servico
- `-StartService`: padrao `true`
- `-RunCheck`: imprime diagnostico final
- `-KeepPowerShellAgent`: padrao `true`
- `-DisablePowerShellAgent`: desabilita a tarefa legada `RMM-Agent-Heartbeat`
- `-Force`: reservado para reinstalacao/update
- `-Debug`: reservado para logs verbosos

## Desinstalacao

```powershell
powershell.exe -ExecutionPolicy Bypass -File "C:\ProgramData\NightOwl\AgentDotNet\Uninstall-NightOwlAgentDotNet.ps1"
```

Por padrao, remove apenas o servico e preserva dados. Para remover a pasta `AgentDotNet`:

```powershell
powershell.exe -ExecutionPolicy Bypass -File "C:\ProgramData\NightOwl\AgentDotNet\Uninstall-NightOwlAgentDotNet.ps1" -RemoveData
```

O uninstall nao remove logs gerais, packages, cache nem o agente PowerShell legado.

## Jobs permitidos no MVP

- `ping`
- `collect_logs`
- `collect_disks`
- `collect_software`
- `collect_security`
- `windows_update_scan`
- `force_inventory`

Ficam fora desta fase:

- deploy real de software
- execucao arbitraria de scripts
- reboot remoto
- instalacao de patches
- auto-update do agente

## Logs principais

- `service.starting`
- `service.stopping`
- `config.loaded`
- `config.normalized`
- `machine_id.resolved`
- `config.invalid_missing_token`
- `heartbeat.sent`
- `heartbeat.failed`
- `collection.*`
- `job.pull.*`
- `job.received`
- `job.started`
- `job.completed`
- `job.failed`
- `job.result.sent`
- `job.result.failed`

O instalador/desinstalador registra eventos em:

`C:\ProgramData\NightOwl\Logs\service-install.log`

## Backend Django

O endpoint agregado `/api/agent/collect/` aceita payload com secoes no topo:

- `system`
- `hardware`
- `network`
- `disks`
- `software`
- `security`
- `patches`

O backend aceita payload parcial, preserva dados anteriores quando uma secao nao vem no payload e cria eventos para alimentar Endpoint Detail, Eventos e Jobs.
