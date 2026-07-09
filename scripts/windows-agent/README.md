# Night Owl Windows Agent

Agente PowerShell do Night Owl RMM. Ele coleta inventario basico da maquina Windows e envia heartbeat para o backend Django.

O agente nao executa comandos remotos, nao coleta conteudo de arquivos e nao faz DLP.

## Arquitetura

- Fonte oficial do pacote: `\\192.168.104.120\controlsul\Comum\_Agents`
- Execucao local em cada endpoint: `C:\RMM\RmmAgent.ps1`
- Config local por endpoint: `C:\RMM\RmmAgent.config.ps1`
- Logs e estado locais: `C:\RMM\logs` e `C:\RMM\agent.state.json`
- A tarefa agendada sempre executa o script local, nunca diretamente do share.
- Estrutura preparada para servico: `C:\ProgramData\NightOwl\Agent`
- Config do servico: `C:\ProgramData\NightOwl\Agent\RmmAgent.config.json`
- Estado do servico: `C:\ProgramData\NightOwl\Agent\agent.state.json`
- Logs do servico: `C:\ProgramData\NightOwl\Logs`
- Cache e pacotes futuros: `C:\ProgramData\NightOwl\Cache` e `C:\ProgramData\NightOwl\Packages`

O share central deve conter os scripts oficiais, `VERSION` e `manifest.json`. O token de cada endpoint nao deve ficar no share central.

## Permissoes do share

Se instalacao/update/check forem executados como `SYSTEM`, o compartilhamento precisa permitir leitura para as contas de computador ou para um grupo adequado, por exemplo Domain Computers, conforme a politica do dominio.

## Instalar a partir do share

Abra PowerShell como Administrador:

```powershell
powershell.exe -ExecutionPolicy Bypass -File "\\192.168.104.120\controlsul\Comum\_Agents\Install-RmmAgent.ps1" -ServerUrl "http://SERVIDOR:8000/api/agent/heartbeat/" -AgentToken "TOKEN" -RunOnce -RunCheck
```

### Instalar usando enrollment token

Fluxo preferencial para cadastro inicial:

```powershell
powershell.exe -ExecutionPolicy Bypass -File "\\192.168.104.120\controlsul\Comum\_Agents\Install-RmmAgent.ps1" -ServerUrl "http://192.168.104.X:8000/api/agent/heartbeat/" -EnrollmentToken "enroll_xxxxx" -RunOnce -RunCheck
```

Neste modo:

- o enrollment token serve apenas para cadastro inicial;
- o servidor valida o token e gera um `agent_token` unico para o endpoint;
- o `agent_token` fica salvo apenas em `C:\RMM\RmmAgent.config.ps1`;
- o enrollment token nao fica salvo no config local;
- o enrollment token pode expirar e ter limite de uso.

No backend, crie o token com:

```powershell
python manage.py create_enrollment_token --name "Piloto TI" --expires-hours 168 --max-uses 20 --allowed-domain control.local
```

### Validacao manual fora do dominio

Se o enrollment token tiver `allowed_domain` e a maquina estiver em `WORKGROUP`, sem dominio ou em dominio diferente, o servidor exigira um token manual de curta duracao.

Crie o token manual no backend:

```powershell
python manage.py create_manual_validation_token --name "Instalacao fora do dominio" --expires-minutes 5
```

Instale informando o token manual desde o inicio:

```powershell
powershell.exe -ExecutionPolicy Bypass -File "\\192.168.104.120\controlsul\Comum\_Agents\Install-RmmAgent.ps1" -ServerUrl "http://192.168.104.X:8000/api/agent/heartbeat/" -EnrollmentToken "enroll_xxxxx" -ManualValidationToken "manual_XXXXXXXXXX" -RunOnce -RunCheck
```

Ou execute apenas com `-EnrollmentToken`; se o servidor exigir validacao manual, o instalador abrira a UI local do Night Owl.

Na Fase Agent 5.3, quando a maquina nao estiver no dominio permitido, o instalador abre por padrao uma UI local Windows, nao-web, para informar o token manual. A UI comunica diretamente com `/api/agent/enroll/` e retorna o `agent_token` ao instalador por um arquivo temporario, que e apagado logo apos a leitura.

Exemplo com UI automatica:

```powershell
powershell.exe -ExecutionPolicy Bypass -File "\\192.168.104.120\controlsul\Comum\_Agents\Install-RmmAgent.ps1" -ServerUrl "http://192.168.104.X:8000/api/agent/heartbeat/" -EnrollmentToken "enroll_xxxxx" -RunOnce -RunCheck
```

Forcar fallback por console:

```powershell
powershell.exe -ExecutionPolicy Bypass -File "\\192.168.104.120\controlsul\Comum\_Agents\Install-RmmAgent.ps1" -ServerUrl "http://192.168.104.X:8000/api/agent/heartbeat/" -EnrollmentToken "enroll_xxxxx" -UseConsoleManualValidation
```

Abrir a UI manualmente para teste:

```powershell
powershell.exe -ExecutionPolicy Bypass -File ".\NightOwlManualValidation.ps1" -ServerUrl "http://192.168.104.X:8000/api/agent/heartbeat/" -EnrollmentToken "enroll_xxxxx" -Hostname "PC-FORA-AD" -Domain "WORKGROUP" -ResultPath "$env:TEMP\nightowl-enroll-result.json"
```

Logo da UI:

- pacote: `assets\nightowl-logo.png`
- instalado: `C:\RMM\assets\nightowl-logo.png`
- parametro opcional: `-LogoPath`

O token manual:

- e single-use;
- expira por padrao em 5 minutos;
- nao fica salvo em `RmmAgent.config.ps1`;
- nao fica salvo em `agent.state.json`;
- nao deve ser escrito em logs.

Roadmap futuro: evoluir a UI local nao-web para incluir selecao de servidor/pacote, diagnostico visual antes da instalacao e uma experiencia ainda mais guiada para tecnicos de campo.

Parametros principais:

- `-SourcePath`: origem do pacote. Padrao: `\\192.168.104.120\controlsul\Comum\_Agents`
- `-InstallPath`: destino local. Padrao: `C:\RMM`
- `-ServerUrl`: obrigatorio se o config local nao existir
- `-AgentToken`: obrigatorio se o config local nao existir
- `-EnrollmentToken`: alternativa preferencial para cadastro inicial sem token individual pre-gerado
- `-ManualValidationToken`: token manual de 5 minutos para instalacao fora do dominio autorizado
- `-UseConsoleManualValidation`: usa console em vez da UI local para validacao manual
- `-ManualValidationUiPath`: caminho alternativo para `NightOwlManualValidation.ps1`
- `-LogoPath`: caminho alternativo para a logo da UI local
- `-IntervalMinutes`: intervalo da tarefa. Padrao: `15`
- `-RunOnce`: executa o agente uma vez apos instalar
- `-RunCheck`: executa diagnostico ao final
- `-ForceConfig`: sobrescreve o config local. Use com cuidado.
- `-InstallAsService`: prepara `C:\ProgramData\NightOwl` e instala o servico `NightOwlAgent`
- `-KeepScheduledTaskFallback`: mantem a tarefa agendada habilitada junto com o servico
- `-ProgramDataPath`: base do agente como servico. Padrao: `C:\ProgramData\NightOwl`
- `-NssmPath`: caminho para `nssm.exe`, quando nao estiver no PATH ou no pacote
- `-WinswPath`: reservado para suporte futuro ao WinSW

Isso cria ou atualiza:

- `C:\RMM`
- `C:\RMM\RmmAgent.config.ps1`, somente se ainda nao existir
- tarefa agendada `RMM-Agent-Heartbeat`
- `C:\RMM\agent.state.json`

## Modo servico Windows

A versao `0.4.0` prepara o agente para execucao residente como servico Windows, sem remover o modo legado por tarefa agendada.

O servico usa:

- nome do servico: `NightOwlAgent`
- display name: `NightOwl RMM Agent`
- conta: `LocalSystem`
- startup: automatic delayed start
- script principal: `C:\ProgramData\NightOwl\Agent\RmmAgentService.ps1`
- config: `C:\ProgramData\NightOwl\Agent\RmmAgent.config.json`

O `RmmAgentService.ps1` executa um loop continuo com ciclos separados para:

- heartbeat leve;
- pull de jobs, preparado para fase futura;
- inventario de sistema;
- inventario de rede;
- inventario de hardware;
- disco;
- seguranca;
- software;
- inventario completo;
- patches.

O heartbeat leve continua rapido e nao executa coletas pesadas. As coletas de sistema, rede, hardware, disco, seguranca, software, inventario completo e patches rodam em intervalos proprios configuraveis no `RmmAgent.config.json`.

Se os endpoints dedicados de coleta ainda nao estiverem configurados, o agente coleta localmente, grava estado/log e deixa o payload pronto, sem enviar dados pesados dentro do heartbeat.

Funcoes principais:

- `Get-NightOwlSystemInventory`
- `Get-NightOwlNetworkInventory`
- `Get-NightOwlDiskInventory`
- `Get-NightOwlHardwareInventory`
- `Get-NightOwlSoftwareInventory`
- `Get-NightOwlSecurityInventory`
- `Get-NightOwlPatchStatus`

O inventario de software usa registro do Windows (`HKLM:\...\Uninstall` e `WOW6432Node`) e nao usa `Win32_Product`.

### Jobs tecnicos seguros

O servico tambem suporta pull inicial de jobs tecnicos:

- pull: `/api/agent/jobs/pull/`
- resultado: `/api/agent/jobs/result/`
- intervalo padrao: `45` segundos
- teste manual: `-RunJobsOnce`

Tipos permitidos nesta fase:

- `force_inventory`
- `collect_disks`
- `collect_security`
- `collect_software`
- `ping`
- `collect_logs`
- `windows_update_scan`

O agente nao executa scripts arbitrarios, nao instala software e nao roda comandos remotos livres nesta fase. A allowlist fica em `RmmAgent.config.json` em `jobs.allowedTypes`.

Cada resultado possui:

- `status`: `completed`, `failed` ou `expired`
- `started_at`
- `finished_at`
- `duration_seconds`
- `exit_code`
- `stdout` resumido
- `stderr` resumido
- `result` em JSON
- `error_message`

Se o resultado nao puder ser enviado ao backend, ele fica em `pendingJobResults` no `agent.state.json` para retry posterior. Jobs ja executados ficam em `executedJobs`, evitando duplicidade.

### Instalar como servico

O registro do servico usa NSSM. A busca por `nssm.exe` acontece nesta ordem:

1. caminho passado em `-NssmPath`;
2. `C:\ProgramData\NightOwl\Agent\nssm.exe`;
3. raiz do pacote, por exemplo `\\192.168.104.120\controlsul\Comum\_Agents\nssm.exe`;
4. `tools\nssm.exe` dentro do pacote;
5. PATH do Windows.

Quando encontrado no pacote ou PATH, o instalador copia o binario para `C:\ProgramData\NightOwl\Agent\nssm.exe` e usa essa copia local.

```powershell
powershell.exe -ExecutionPolicy Bypass -File "\\192.168.104.120\controlsul\Comum\_Agents\Install-RmmAgent.ps1" -ServerUrl "http://SERVIDOR:8000/api/agent/heartbeat/" -EnrollmentToken "enroll_xxxxx" -InstallAsService -NssmPath "\\192.168.104.120\controlsul\Comum\_Agents\nssm.exe" -RunOnce -RunCheck
```

Por compatibilidade, o instalador ainda cria a tarefa agendada. Quando `-InstallAsService` e usado sem `-KeepScheduledTaskFallback`, a tarefa e desabilitada ao final para evitar dois heartbeats concorrentes. Para manter a tarefa ativa como fallback:

```powershell
powershell.exe -ExecutionPolicy Bypass -File "\\192.168.104.120\controlsul\Comum\_Agents\Install-RmmAgent.ps1" -ServerUrl "http://SERVIDOR:8000/api/agent/heartbeat/" -EnrollmentToken "enroll_xxxxx" -InstallAsService -KeepScheduledTaskFallback -NssmPath "\\192.168.104.120\controlsul\Comum\_Agents\nssm.exe"
```

### Testar uma iteracao do servico

```powershell
powershell.exe -ExecutionPolicy Bypass -File "C:\ProgramData\NightOwl\Agent\RmmAgentService.ps1" -RunOnce -DebugMode
```

Testar somente pull/execucao de jobs:

```powershell
powershell.exe -ExecutionPolicy Bypass -File "C:\ProgramData\NightOwl\Agent\RmmAgentService.ps1" -RunJobsOnce -DebugMode
```

### Instalar/remover somente o servico

```powershell
powershell.exe -ExecutionPolicy Bypass -File C:\RMM\Install-RmmAgentService.ps1 -SourcePath C:\RMM -NssmPath C:\RMM\nssm.exe -RunOnce
powershell.exe -ExecutionPolicy Bypass -File C:\ProgramData\NightOwl\Agent\Uninstall-RmmAgentService.ps1
```

## Atualizar manualmente

```powershell
powershell.exe -ExecutionPolicy Bypass -File C:\RMM\Update-RmmAgent.ps1
```

Com diagnostico ao final:

```powershell
powershell.exe -ExecutionPolicy Bypass -File C:\RMM\Update-RmmAgent.ps1 -RunCheck
```

O update copia os arquivos oficiais do share para `C:\RMM`, preservando:

- `RmmAgent.config.ps1`
- `logs\`
- `agent.state.json`, exceto campos de diagnostico de update
- `C:\ProgramData\NightOwl\Agent\RmmAgent.config.json`, quando o servico existir
- `C:\ProgramData\NightOwl\Logs\`
- `C:\ProgramData\NightOwl\Agent\agent.state.json`

## Checar instalacao

```powershell
powershell.exe -ExecutionPolicy Bypass -File C:\RMM\Check-RmmAgent.ps1
```

Para rodar tambem um heartbeat manual durante a checagem:

```powershell
powershell.exe -ExecutionPolicy Bypass -File C:\RMM\Check-RmmAgent.ps1 -RunAgentTest
```

Exit codes:

- `0`: essencial OK
- `1`: warnings
- `2`: erro critico

## Rodar manualmente

```powershell
powershell.exe -ExecutionPolicy Bypass -File C:\RMM\RmmAgent.ps1
```

## Desinstalar

Por padrao, remove a tarefa e arquivos do agente, preservando config e logs:

```powershell
powershell.exe -ExecutionPolicy Bypass -File C:\RMM\Uninstall-RmmAgent.ps1 -KeepLogs -KeepConfig
```

Remover tudo:

```powershell
powershell.exe -ExecutionPolicy Bypass -File C:\RMM\Uninstall-RmmAgent.ps1 -RemoveAll
```

## Deploy controlado por CSV

Esta fase prepara deploy em lote controlado, sem AD/GPO. A implantacao via AD/GPO fica para fase futura, preferencialmente depois de enrollment token e/ou agente como servico.

Crie uma lista simples:

```text
PC-001
PC-002
```

Gere o CSV de deploy no backend:

```powershell
python manage.py prepare_agent_deploy --input computers.txt --output deploy_agents.csv --domain control.local --server-url "http://192.168.104.X:8000/api/agent/heartbeat/"
```

Execute deploy `CopyOnly`:

```powershell
powershell.exe -ExecutionPolicy Bypass -File "\\192.168.104.120\controlsul\Comum\_Agents\Deploy-RmmAgent.ps1" -DeployCsv ".\deploy_agents.csv" -Mode CopyOnly
```

Depois ative a tarefa localmente ou por ferramenta administrativa:

```powershell
powershell.exe -ExecutionPolicy Bypass -File C:\RMM\Install-RmmAgent.ps1 -RunOnce -RunCheck
```

Importante: `deploy_agents.csv` contem tokens individuais de agentes. Guarde em local restrito e exclua quando nao for mais necessario.

O CSV com tokens individuais continua existindo como modo tecnico. Para novas instalacoes controladas, prefira enrollment token quando possivel.

## Logs

```powershell
Get-Content C:\RMM\logs\agent.log -Tail 50
Get-Content C:\RMM\logs\update.log -Tail 50
```

## Arquivos esperados no pacote central

```text
\\192.168.104.120\controlsul\Comum\_Agents\
  RmmAgent.ps1
  RmmAgent.config.example.ps1
  RmmAgent.config.json.example
  RmmAgentService.ps1
  Install-RmmAgentService.ps1
  Uninstall-RmmAgentService.ps1
  Install-RmmAgent.ps1
  Update-RmmAgent.ps1
  Check-RmmAgent.ps1
  Uninstall-RmmAgent.ps1
  NightOwlManualValidation.ps1
  assets\nightowl-logo.png
  VERSION
  manifest.json
```

## Roadmap futuro

Esta fase ainda usa scripts PowerShell locais em `C:\RMM`. No futuro, o agente deve evoluir para:

- servico residente;
- possivel icone/system tray com identidade Night Owl;
- UX/local UI propria do agente;
- modelo de pacote com assinatura/hash;
- revisao e remocao do que for legado/transitorio quando o servico oficial existir.
