param(
    [Parameter(Mandatory = $true)]
    [string]$ServerUrl,

    [string]$EnrollmentToken = "",
    [string]$AgentToken = "",
    [string]$InstallPath = "C:\ProgramData\NightOwl\AgentDotNet",
    [string]$ServiceName = "NightOwlAgentDotNet",
    [string]$DisplayName = "NightOwl RMM Agent .NET",
    [switch]$InstallAsService,
    [switch]$Force,
    [bool]$StartService = $true,
    [switch]$RunCheck,
    [bool]$KeepPowerShellAgent = $true,
    [switch]$DisablePowerShellAgent,
    [switch]$Debug
)

$ErrorActionPreference = "Stop"

function Write-Step($Status, $Message) {
    Write-Host ("[{0}] {1}" -f $Status, $Message)
}

function Write-InstallLog($EventType, $Message, $Metadata = @{}) {
    try {
        $logDir = "C:\ProgramData\NightOwl\Logs"
        New-Item -ItemType Directory -Force -Path $logDir | Out-Null
        $entry = [ordered]@{
            timestamp = (Get-Date).ToUniversalTime().ToString("o")
            event_type = $EventType
            message = $Message
            metadata = $Metadata
        }
        $entry | ConvertTo-Json -Depth 6 -Compress | Add-Content -Path (Join-Path $logDir "service-install.log") -Encoding UTF8
    }
    catch {}
}

function Assert-Elevated {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Execute este instalador em um PowerShell como Administrador."
    }
}

function Normalize-ServerUrl([string]$Url) {
    $value = ($Url.Trim()).TrimEnd("/")
    if ($value.EndsWith("/api/agent/heartbeat")) {
        return $value.Substring(0, $value.Length - "/api/agent/heartbeat".Length).TrimEnd("/")
    }
    return $value
}

function Join-AgentUrl([string]$Base, [string]$Path) {
    return ("{0}/{1}" -f $Base.TrimEnd("/"), $Path.TrimStart("/"))
}

function Read-JsonFile([string]$Path) {
    if (-not (Test-Path $Path)) { return $null }
    try {
        return Get-Content -Path $Path -Raw | ConvertFrom-Json
    }
    catch {
        return $null
    }
}

function Get-JsonProperty($Object, [string[]]$Names) {
    if ($null -eq $Object) { return "" }
    foreach ($name in $Names) {
        if ($Object.PSObject.Properties.Name -contains $name) {
            $value = $Object.$name
            if ($null -ne $value -and -not [string]::IsNullOrWhiteSpace([string]$value)) {
                return [string]$value
            }
        }
    }
    return ""
}

function Test-MachineId([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) { return $false }
    if ($Value -ieq $env:COMPUTERNAME) { return $false }
    if ($Value -ieq "HOSTNAME" -or $Value -ieq "MACHINE_ID") { return $false }
    return $true
}

function Resolve-MachineId([string]$ConfigPath, [string]$StatePath) {
    $existingConfig = Read-JsonFile $ConfigPath
    $configMachineId = Get-JsonProperty $existingConfig @("machineId", "machine_id", "MachineId")
    if (Test-MachineId $configMachineId) {
        return @{ Value = $configMachineId; Source = "config" }
    }

    $candidates = @(
        @{ Path = $StatePath; Source = "dotnet_state" },
        @{ Path = "C:\ProgramData\NightOwl\Agent\agent.state.json"; Source = "powershell_state" },
        @{ Path = "C:\RMM\agent.state.json"; Source = "legacy_rmm_state" }
    )
    foreach ($candidate in $candidates) {
        $state = Read-JsonFile $candidate.Path
        $stateMachineId = Get-JsonProperty $state @("machine_id", "machineId", "MachineId", "agent_id", "agentId")
        if (Test-MachineId $stateMachineId) {
            return @{ Value = $stateMachineId; Source = $candidate.Source }
        }
    }

    try {
        $machineGuid = (Get-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Cryptography" -Name MachineGuid -ErrorAction Stop).MachineGuid
        if (Test-MachineId $machineGuid) {
            return @{ Value = $machineGuid; Source = "machine_guid" }
        }
    }
    catch {}

    return @{ Value = ([guid]::NewGuid().ToString()); Source = "generated" }
}

function Get-ComputerInfoLite {
    $cs = Get-CimInstance Win32_ComputerSystem -ErrorAction SilentlyContinue
    $bios = Get-CimInstance Win32_BIOS -ErrorAction SilentlyContinue
    return @{
        Hostname = $env:COMPUTERNAME.ToUpperInvariant()
        Domain = if ($cs.PartOfDomain) { ([string]$cs.Domain).ToLowerInvariant() } else { "" }
        SerialNumber = if ($bios.SerialNumber) { [string]$bios.SerialNumber } else { "" }
    }
}

function Invoke-Enrollment($BaseUrl, $Token, $MachineId, $InstallPath) {
    $info = Get-ComputerInfoLite
    $body = @{
        enrollment_token = $Token
        machine_id = $MachineId
        hostname = $info.Hostname
        domain = $info.Domain
        serial_number = $info.SerialNumber
        agent_version = "0.1.0"
        agent_mode = "dotnet-service"
        install_path = $InstallPath
        task_name = "NightOwlAgentDotNet"
    } | ConvertTo-Json -Depth 5
    $url = Join-AgentUrl $BaseUrl "/api/agent/enroll/"
    return Invoke-RestMethod -Method Post -Uri $url -Body $body -ContentType "application/json" -TimeoutSec 30
}

function Save-AgentConfig($Path, $Config) {
    $Config | ConvertTo-Json -Depth 8 | Set-Content -Path $Path -Encoding UTF8
}

function Write-StateMachineId($Path, $MachineId) {
    $state = Read-JsonFile $Path
    if ($null -eq $state) {
        $state = [pscustomobject]@{}
    }
    if ($state.PSObject.Properties.Name -notcontains "machine_id") {
        $state | Add-Member -NotePropertyName "machine_id" -NotePropertyValue $MachineId
    }
    else {
        $state.machine_id = $MachineId
    }
    $state | ConvertTo-Json -Depth 8 | Set-Content -Path $Path -Encoding UTF8
}

function Stop-ServiceIfExists([string]$Name) {
    $service = Get-Service -Name $Name -ErrorAction SilentlyContinue
    if ($service -and $service.Status -ne "Stopped") {
        Stop-Service -Name $Name -Force -ErrorAction SilentlyContinue
        $service.WaitForStatus("Stopped", "00:00:20")
    }
}

function Install-OrUpdateService([string]$Name, [string]$Display, [string]$ExePath) {
    Write-InstallLog "service.install.started" "Instalando ou atualizando servico .NET." @{
        service_name = $Name
        executable = $ExePath
    }
    $existing = Get-Service -Name $Name -ErrorAction SilentlyContinue
    try {
        if ($existing) {
            Stop-ServiceIfExists $Name
            sc.exe config $Name binPath= "`"$ExePath`"" start= delayed-auto obj= LocalSystem | Out-Null
            sc.exe description $Name "NightOwl RMM Windows agent implemented as a .NET Worker Service." | Out-Null
        }
        else {
            New-Service -Name $Name -DisplayName $Display -BinaryPathName "`"$ExePath`"" -StartupType Automatic -Description "NightOwl RMM Windows agent implemented as a .NET Worker Service." | Out-Null
            sc.exe config $Name start= delayed-auto | Out-Null
        }
        sc.exe failure $Name reset= 86400 actions= restart/60000/restart/120000/restart/300000 | Out-Null
        Write-InstallLog "service.install.completed" "Servico .NET configurado." @{
            service_name = $Name
            executable = $ExePath
            startup = "delayed-auto"
            account = "LocalSystem"
        }
    }
    catch {
        Write-InstallLog "service.install.failed" "Falha ao configurar servico .NET." @{
            service_name = $Name
            error = $_.Exception.Message
        }
        throw
    }
}

function Test-RecentHeartbeat([string]$LogPath) {
    if (-not (Test-Path $LogPath)) { return $false }
    $since = (Get-Date).ToUniversalTime().AddMinutes(-10)
    $lines = Get-Content -Path $LogPath -Tail 300 -ErrorAction SilentlyContinue
    foreach ($line in $lines) {
        try {
            $record = $line | ConvertFrom-Json
            if ($record.event_type -eq "heartbeat.sent" -and ([datetime]$record.timestamp) -ge $since) {
                return $true
            }
        }
        catch {}
    }
    return $false
}

Assert-Elevated

$serverBase = Normalize-ServerUrl $ServerUrl
if ([string]::IsNullOrWhiteSpace($serverBase)) {
    throw "ServerUrl e obrigatorio."
}
if ([string]::IsNullOrWhiteSpace($EnrollmentToken) -and [string]::IsNullOrWhiteSpace($AgentToken)) {
    throw "Informe -EnrollmentToken ou -AgentToken."
}

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$sourcePath = $scriptRoot
$configPath = Join-Path $InstallPath "agent.config.json"
$statePath = "C:\ProgramData\NightOwl\AgentDotNet\agent-dotnet.state.json"
$logPath = "C:\ProgramData\NightOwl\Logs\agent-dotnet.jsonl"

$directories = @(
    "C:\ProgramData\NightOwl",
    $InstallPath,
    "C:\ProgramData\NightOwl\Logs",
    "C:\ProgramData\NightOwl\Jobs",
    "C:\ProgramData\NightOwl\Packages",
    "C:\ProgramData\NightOwl\Cache"
)
foreach ($dir in $directories) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
}

$identity = Resolve-MachineId -ConfigPath $configPath -StatePath $statePath
$machineId = $identity.Value
$identitySource = $identity.Source

if (-not [string]::IsNullOrWhiteSpace($EnrollmentToken)) {
    Write-Step "OK" "Executando enrollment no servidor NightOwl"
    $enrollResponse = Invoke-Enrollment -BaseUrl $serverBase -Token $EnrollmentToken -MachineId $machineId -InstallPath $InstallPath
    if ($enrollResponse.agent_token) {
        $AgentToken = [string]$enrollResponse.agent_token
    }
    if ($enrollResponse.machine_id -and -not (Test-MachineId $machineId)) {
        $machineId = [string]$enrollResponse.machine_id
        $identitySource = "enrollment"
    }
}

$exclude = @("*.pdb")
Copy-Item -Path (Join-Path $sourcePath "*") -Destination $InstallPath -Recurse -Force -Exclude $exclude
$exePath = Join-Path $InstallPath "NightOwl.Agent.Windows.exe"
if (-not (Test-Path $exePath)) {
    throw "Executavel do agente nao encontrado apos copia: $exePath"
}
Write-Step "OK" "Arquivos copiados"

$existingConfig = Read-JsonFile $configPath
if ($null -eq $existingConfig) {
    $existingConfig = [pscustomobject]@{}
}

$config = [ordered]@{
    agentToken = $AgentToken
    machineId = $machineId
    agentVersion = if ($existingConfig.agentVersion) { $existingConfig.agentVersion } else { "0.1.0" }
    serverBaseUrl = $serverBase
    heartbeatUrl = Join-AgentUrl $serverBase "/api/agent/heartbeat/"
    collectUrl = Join-AgentUrl $serverBase "/api/agent/collect/"
    jobsPullUrl = Join-AgentUrl $serverBase "/api/agent/jobs/pull/"
    jobsResultUrl = Join-AgentUrl $serverBase "/api/agent/jobs/result/"
    intervals = [ordered]@{
        heartbeatSeconds = 300
        collectSeconds = 3600
        jobsSeconds = 10
    }
    logPath = $logPath
    statePath = $statePath
    installPath = $InstallPath
    packagesPath = "C:\ProgramData\NightOwl\Packages"
    cachePath = "C:\ProgramData\NightOwl\Cache"
    jobsPath = "C:\ProgramData\NightOwl\Jobs"
    allowedJobTypes = @("ping", "collect_logs", "collect_disks", "collect_software", "collect_security", "windows_update_scan", "force_inventory")
}
Save-AgentConfig -Path $configPath -Config $config
Write-StateMachineId -Path $statePath -MachineId $machineId
Write-Step "OK" "Configuracao atualizada"
Write-Step "OK" ("Machine ID: {0} ({1})" -f $machineId, $identitySource)

if ($DisablePowerShellAgent) {
    $legacyTask = Get-ScheduledTask -TaskName "RMM-Agent-Heartbeat" -ErrorAction SilentlyContinue
    if ($legacyTask) {
        Disable-ScheduledTask -TaskName "RMM-Agent-Heartbeat" | Out-Null
        Write-Step "OK" "Tarefa PowerShell legada desabilitada"
    }
}
elseif ($KeepPowerShellAgent) {
    Write-Step "OK" "Agente PowerShell legado preservado"
}

if ($InstallAsService) {
    Install-OrUpdateService -Name $ServiceName -Display $DisplayName -ExePath $exePath
    Write-Step "OK" "Servico instalado"
    if ($StartService) {
        Start-Service -Name $ServiceName
        Start-Sleep -Seconds 3
        Write-Step "OK" "Servico iniciado"
    }
}

if ($RunCheck) {
    $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($service) {
        Write-Step "OK" ("Service status: {0}" -f $service.Status)
        $serviceInfo = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'" -ErrorAction SilentlyContinue
        if ($serviceInfo) {
            Write-Step "OK" ("Service startup type: {0}" -f $serviceInfo.StartMode)
            Write-Step "OK" ("Service account: {0}" -f $serviceInfo.StartName)
            Write-Step "OK" ("Service executable: {0}" -f $serviceInfo.PathName)
        }
    }
    else {
        Write-Step "WARN" "Servico nao encontrado"
    }
    if (Test-Path $configPath) { Write-Step "OK" "Config existe" } else { Write-Step "FAIL" "Config ausente" }
    if ([string]::IsNullOrWhiteSpace($AgentToken) -or $AgentToken -eq "TOKEN") { Write-Step "FAIL" "Token invalido/placeholder" } else { Write-Step "OK" "Token configurado" }
    if (Test-Path $logPath) { Write-Step "OK" "Log existe" } else { New-Item -ItemType File -Force -Path $logPath | Out-Null; Write-Step "OK" "Log criado" }
    try {
        Invoke-WebRequest -Method Head -Uri (Join-AgentUrl $serverBase "/api/agent/heartbeat/") -TimeoutSec 10 | Out-Null
        Write-Step "OK" "Endpoint heartbeat acessivel"
    }
    catch {
        Write-Step "WARN" "Heartbeat nao validado via HEAD; o endpoint pode aceitar apenas POST"
    }
    if (Test-RecentHeartbeat $logPath) {
        Write-Step "OK" "Heartbeat recente encontrado no log"
    }
    else {
        Write-Step "WARN" "Heartbeat recente ainda nao encontrado; aguarde o ciclo do servico"
    }
}

Write-Step "OK" "Instalacao concluida"
