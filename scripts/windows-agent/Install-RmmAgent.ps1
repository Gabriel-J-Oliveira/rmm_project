[CmdletBinding()]
param(
    [string]$SourcePath = "\\192.168.104.120\controlsul\Comum\_Agents",
    [string]$InstallPath = "C:\RMM",
    [string]$ServerUrl = "",
    [string]$AgentToken = "",
    [string]$EnrollmentToken = "",
    [string]$ManualValidationToken = "",
    [switch]$UseConsoleManualValidation,
    [string]$ManualValidationUiPath = "",
    [string]$LogoPath = "",
    [int]$IntervalMinutes = 15,
    [switch]$RunOnce,
    [switch]$RunCheck,
    [switch]$ForceConfig,
    [switch]$InstallAsService,
    [switch]$KeepScheduledTaskFallback,
    [string]$ProgramDataPath = "C:\ProgramData\NightOwl",
    [string]$NssmPath = "",
    [string]$WinswPath = "",
    [switch]$DebugMode
)

$ErrorActionPreference = "Stop"
$TaskName = "RMM-Agent-Heartbeat"
$LogDirectory = Join-Path $InstallPath "logs"
$InstallLog = Join-Path $LogDirectory "install.log"
$ConfigPath = Join-Path $InstallPath "RmmAgent.config.ps1"
$StatePath = Join-Path $InstallPath "agent.state.json"
$script:EnrollmentState = @{}
$script:InstallAgentMode = "scheduled_task"
if ($InstallAsService) {
    $script:InstallAgentMode = "service"
}

function Write-InstallLog {
    param([string]$Message, [string]$Level = "INFO")
    if (-not (Test-Path $LogDirectory)) {
        New-Item -Path $LogDirectory -ItemType Directory -Force | Out-Null
    }
    $timestamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    Add-Content -Path $InstallLog -Value "[$timestamp] [$Level] $Message"
    Write-Host "[$Level] $Message"
}

function Read-VersionFromPath {
    param([string]$Path)
    $versionPath = Join-Path $Path "VERSION"
    if (Test-Path $versionPath) {
        return (Get-Content -Path $versionPath -Raw).Trim()
    }
    $manifestPath = Join-Path $Path "manifest.json"
    if (Test-Path $manifestPath) {
        try { return (Get-Content -Path $manifestPath -Raw | ConvertFrom-Json).version } catch { return "" }
    }
    return ""
}

function Get-EnrollmentUrl {
    param([string]$HeartbeatUrl)
    if ([string]::IsNullOrWhiteSpace($HeartbeatUrl)) {
        throw "ServerUrl is required for enrollment."
    }
    $trimmed = $HeartbeatUrl.Trim()
    if ($trimmed -match "/api/agent/heartbeat/?$") {
        return ($trimmed -replace "/api/agent/heartbeat/?$", "/api/agent/enroll/")
    }
    return ($trimmed.TrimEnd("/") + "/api/agent/enroll/")
}

function Get-SafeTokenPrefix {
    param([string]$Token)
    if ([string]::IsNullOrWhiteSpace($Token)) { return "" }
    if ($Token.Length -le 18) { return $Token }
    return $Token.Substring(0, 18)
}

function Get-LocalComputerDomain {
    try {
        $computerSystem = Get-CimInstance -ClassName Win32_ComputerSystem -ErrorAction Stop
        if ($computerSystem.PartOfDomain) {
            return [string]$computerSystem.Domain
        }
    }
    catch {
        Write-InstallLog "Could not read computer domain: $($_.Exception.Message)" "WARN"
    }
    return ""
}

function Get-LocalSerialNumber {
    try {
        $bios = Get-CimInstance -ClassName Win32_BIOS -ErrorAction Stop
        return [string]$bios.SerialNumber
    }
    catch {
        Write-InstallLog "Could not read serial number: $($_.Exception.Message)" "WARN"
        return ""
    }
}

function Invoke-AgentEnrollment {
    param(
        [string]$HeartbeatUrl,
        [string]$Token,
        [string]$ManualToken = ""
    )

    $enrollUrl = Get-EnrollmentUrl -HeartbeatUrl $HeartbeatUrl
    $agentVersion = Read-VersionFromPath -Path $InstallPath
    if ([string]::IsNullOrWhiteSpace($agentVersion)) {
        $agentVersion = "0.1.0"
    }

    $payload = [ordered]@{
        enrollment_token = $Token
        hostname = $env:COMPUTERNAME
        domain = Get-LocalComputerDomain
        serial_number = Get-LocalSerialNumber
        agent_version = $agentVersion
        agent_mode = $script:InstallAgentMode
        install_path = $InstallPath
        task_name = $TaskName
    }
    if (-not [string]::IsNullOrWhiteSpace($ManualToken)) {
        $payload["manual_validation_token"] = $ManualToken
    }
    $json = $payload | ConvertTo-Json -Depth 6
    $bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($json)

    Write-InstallLog "Requesting agent enrollment at $enrollUrl"
    try {
        return Invoke-RestMethod -Uri $enrollUrl -Method Post -Body $bodyBytes -ContentType "application/json; charset=utf-8"
    }
    catch {
        $detail = $_.Exception.Message
        if ($_.Exception.Response) {
            try {
                $stream = $_.Exception.Response.GetResponseStream()
                if ($stream) {
                    $reader = New-Object System.IO.StreamReader($stream)
                    $body = $reader.ReadToEnd()
                    if (-not [string]::IsNullOrWhiteSpace($body)) {
                        $detail = $body
                    }
                }
            }
            catch {
                $detail = $_.Exception.Message
            }
        }
        $errorCode = ""
        try {
            $parsed = $detail | ConvertFrom-Json
            $errorCode = [string]$parsed.error
        }
        catch {
            $errorCode = ""
        }
        $exception = New-Object System.Exception("Enrollment failed: $detail")
        $exception.Data["error"] = $errorCode
        throw $exception
    }
}

function Request-ManualValidationToken {
    Write-Host ""
    Write-Host "Instalacao requer validacao manual"
    Write-Host "Esta maquina nao esta no dominio autorizado para enrollment automatico."
    Write-Host "Acesse o portal/admin do Night Owl, gere um token de validacao manual e informe abaixo."
    Write-Host "O token expira em 5 minutos."
    Write-Host ""
    return Read-Host "Informe o token de validacao manual"
}

function Invoke-ManualValidationUi {
    param([string]$HeartbeatUrl, [string]$Token)

    $uiPath = $ManualValidationUiPath
    if ([string]::IsNullOrWhiteSpace($uiPath)) {
        $uiPath = Join-Path $InstallPath "NightOwlManualValidation.ps1"
    }
    if (-not (Test-Path $uiPath)) {
        throw "NightOwlManualValidation.ps1 nao encontrado."
    }

    $agentVersion = Read-VersionFromPath -Path $InstallPath
    if ([string]::IsNullOrWhiteSpace($agentVersion)) {
        $agentVersion = "0.1.0"
    }

    $resolvedLogoPath = $LogoPath
    if ([string]::IsNullOrWhiteSpace($resolvedLogoPath)) {
        $candidateLogo = Join-Path $InstallPath "assets\nightowl-logo.png"
        if (Test-Path $candidateLogo) {
            $resolvedLogoPath = $candidateLogo
        }
    }

    $resultPath = Join-Path $env:TEMP ("nightowl-enroll-result-{0}.json" -f ([guid]::NewGuid().ToString("N")))
    try {
        $arguments = @(
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", $uiPath,
            "-ServerUrl", $HeartbeatUrl,
            "-EnrollmentToken", $Token,
            "-Hostname", $env:COMPUTERNAME,
            "-Domain", (Get-LocalComputerDomain),
            "-SerialNumber", (Get-LocalSerialNumber),
            "-AgentVersion", $agentVersion,
            "-AgentMode", "scheduled_task",
            "-InstallPath", $InstallPath,
            "-TaskName", $TaskName,
            "-ResultPath", $resultPath
        )
        if (-not [string]::IsNullOrWhiteSpace($resolvedLogoPath)) {
            $arguments += @("-LogoPath", $resolvedLogoPath)
        }

        Write-InstallLog "Opening Night Owl manual validation UI."
        $process = Start-Process -FilePath "powershell.exe" -ArgumentList $arguments -Wait -PassThru
        if ($process.ExitCode -eq 1) {
            throw "Validacao manual cancelada pelo usuario."
        }
        if ($process.ExitCode -ne 0) {
            throw "Interface de validacao manual falhou."
        }
        if (-not (Test-Path $resultPath)) {
            throw "Interface de validacao manual nao retornou resultado."
        }

        return Get-Content -Path $resultPath -Raw | ConvertFrom-Json
    }
    finally {
        if (Test-Path $resultPath) {
            Remove-Item -Path $resultPath -Force -ErrorAction SilentlyContinue
        }
    }
}

function Test-ReadableFile {
    param([string]$Path)
    try {
        Get-Content -Path $Path -TotalCount 1 -ErrorAction Stop | Out-Null
        return $true
    }
    catch {
        Write-InstallLog "File is not readable: $Path :: $($_.Exception.Message)" "ERROR"
        return $false
    }
}

function Copy-AgentPackageFile {
    param([string]$FileName)
    $source = Join-Path $SourcePath $FileName
    $destination = Join-Path $InstallPath $FileName
    if (-not (Test-Path $source)) {
        Write-InstallLog "Skipping missing package file: $source" "WARN"
        return
    }
    if (-not (Test-ReadableFile -Path $source)) {
        throw "Package file is not readable: $source"
    }

    $sourceFull = [System.IO.Path]::GetFullPath($source)
    $destinationFull = [System.IO.Path]::GetFullPath($destination)
    if ([string]::Equals($sourceFull, $destinationFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        Write-InstallLog "Skipping same source/destination: $destinationFull"
        return
    }

    $destinationDirectory = Split-Path -Path $destinationFull -Parent
    if ($destinationDirectory -and -not (Test-Path $destinationDirectory)) {
        New-Item -Path $destinationDirectory -ItemType Directory -Force | Out-Null
    }
    Copy-Item -Path $sourceFull -Destination $destinationFull -Force
    Write-InstallLog "Copied $FileName"
}

function Copy-AgentAssets {
    $sourceAssets = Join-Path $SourcePath "assets"
    $destinationAssets = Join-Path $InstallPath "assets"
    if (-not (Test-Path $sourceAssets)) {
        Write-InstallLog "Skipping missing assets directory: $sourceAssets" "WARN"
        return
    }
    if (-not (Test-Path $destinationAssets)) {
        New-Item -Path $destinationAssets -ItemType Directory -Force | Out-Null
    }
    Copy-Item -Path (Join-Path $sourceAssets "*") -Destination $destinationAssets -Recurse -Force
    Write-InstallLog "Copied assets directory"
}

function Read-AgentState {
    if (-not (Test-Path $StatePath)) {
        return @{}
    }
    try {
        $object = Get-Content -Path $StatePath -Raw | ConvertFrom-Json
        $state = @{}
        foreach ($property in $object.PSObject.Properties) {
            $state[$property.Name] = $property.Value
        }
        return $state
    }
    catch {
        return @{}
    }
}

function Write-AgentState {
    $state = Read-AgentState
    $state["update_source"] = $SourcePath
    $state["package_manifest_path"] = Join-Path $SourcePath "manifest.json"
    $state["local_version"] = Read-VersionFromPath -Path $InstallPath
    $state["last_update_status"] = "installed"
    $state["last_update_error"] = ""
    if (-not $state.ContainsKey("last_update_check_at")) { $state["last_update_check_at"] = $null }
    if (-not $state.ContainsKey("last_run_at")) { $state["last_run_at"] = $null }
    if (-not $state.ContainsKey("last_success_at")) { $state["last_success_at"] = $null }
    if (-not $state.ContainsKey("last_error_at")) { $state["last_error_at"] = $null }
    if (-not $state.ContainsKey("last_error")) { $state["last_error"] = $null }
    if (-not $state.ContainsKey("last_status")) { $state["last_status"] = $null }
    if (-not $state.ContainsKey("last_machine_id")) { $state["last_machine_id"] = $null }
    if (-not $state.ContainsKey("last_snapshot_id")) { $state["last_snapshot_id"] = $null }
    if ($script:EnrollmentState.Count -gt 0) {
        foreach ($key in $script:EnrollmentState.Keys) {
            $state[$key] = $script:EnrollmentState[$key]
        }
    }
    $state | ConvertTo-Json -Depth 6 | Set-Content -Path $StatePath -Encoding UTF8
}

try {
    if (-not (Test-Path $SourcePath)) {
        throw "SourcePath not accessible: $SourcePath"
    }
    if (-not (Test-Path $InstallPath)) {
        New-Item -Path $InstallPath -ItemType Directory -Force | Out-Null
    }
    if (-not (Test-Path $LogDirectory)) {
        New-Item -Path $LogDirectory -ItemType Directory -Force | Out-Null
    }

    $files = @(
        "RmmAgent.ps1",
        "Install-RmmAgent.ps1",
        "Update-RmmAgent.ps1",
        "Uninstall-RmmAgent.ps1",
        "Check-RmmAgent.ps1",
        "NightOwlManualValidation.ps1",
        "assets\nightowl-logo.png",
        "RmmAgent.config.example.ps1",
        "RmmAgent.config.json.example",
        "RmmAgentService.ps1",
        "Install-RmmAgentService.ps1",
        "Uninstall-RmmAgentService.ps1",
        "VERSION",
        "manifest.json",
        "README.md"
    )
    foreach ($file in $files) {
        Copy-AgentPackageFile -FileName $file
    }
    Copy-AgentAssets

    if ((Test-Path $ConfigPath) -and -not $ForceConfig) {
        Write-InstallLog "Preserving existing config: $ConfigPath"
    }
    else {
        if ([string]::IsNullOrWhiteSpace($ServerUrl)) {
            throw "ServerUrl is required when config does not exist or ForceConfig is used."
        }

        $tokenForConfig = $AgentToken
        if (-not [string]::IsNullOrWhiteSpace($AgentToken)) {
            Write-InstallLog "Using direct agent token mode."
            $script:EnrollmentState["install_mode"] = "direct_token"
            $script:EnrollmentState["server_url"] = $ServerUrl
        }
        elseif (-not [string]::IsNullOrWhiteSpace($EnrollmentToken)) {
            Write-InstallLog "Using enrollment token mode."
            try {
                $enrollmentResponse = Invoke-AgentEnrollment -HeartbeatUrl $ServerUrl -Token $EnrollmentToken -ManualToken $ManualValidationToken
            }
            catch {
                $errorCode = [string]$_.Exception.Data["error"]
                if ($errorCode -eq "manual_validation_required") {
                    if ($UseConsoleManualValidation) {
                        $promptedManualToken = Request-ManualValidationToken
                        if ([string]::IsNullOrWhiteSpace($promptedManualToken)) {
                            throw "Token de validacao manual nao informado."
                        }
                        try {
                            $enrollmentResponse = Invoke-AgentEnrollment -HeartbeatUrl $ServerUrl -Token $EnrollmentToken -ManualToken $promptedManualToken
                            $ManualValidationToken = $promptedManualToken
                        }
                        catch {
                            throw "Token de validacao manual invalido ou expirado. Gere um novo token e tente novamente."
                        }
                    }
                    else {
                        try {
                            $enrollmentResponse = Invoke-ManualValidationUi -HeartbeatUrl $ServerUrl -Token $EnrollmentToken
                            $ManualValidationToken = "__validated_by_ui__"
                        }
                        catch {
                            throw "$($_.Exception.Message) Para usar o fallback por console, execute novamente com -UseConsoleManualValidation."
                        }
                    }
                }
                else {
                    throw
                }
            }
            if (-not $enrollmentResponse.agent_token) {
                throw "Enrollment response did not include agent_token."
            }
            $tokenForConfig = [string]$enrollmentResponse.agent_token
            if ($enrollmentResponse.heartbeat_url) {
                $ServerUrl = [string]$enrollmentResponse.heartbeat_url
            }
            $script:EnrollmentState["enrollment_used"] = $true
            $script:EnrollmentState["enrolled_at"] = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
            $script:EnrollmentState["enrollment_token_prefix"] = Get-SafeTokenPrefix -Token $EnrollmentToken
            $script:EnrollmentState["machine_id"] = [string]$enrollmentResponse.machine_id
            $script:EnrollmentState["server_url"] = $ServerUrl
            $script:EnrollmentState["install_mode"] = "enrollment"
            $script:EnrollmentState["manual_validation_used"] = -not [string]::IsNullOrWhiteSpace($ManualValidationToken)
            if ($script:EnrollmentState["manual_validation_used"]) {
                $script:EnrollmentState["enrollment_domain_validation"] = "manual"
            }
            else {
                $script:EnrollmentState["enrollment_domain_validation"] = "domain"
            }
        }
        else {
            throw "AgentToken or EnrollmentToken is required when config does not exist or ForceConfig is used."
        }
        @"
`$RmmServerUrl = "$ServerUrl"
`$AgentToken = "$tokenForConfig"
"@ | Set-Content -Path $ConfigPath -Encoding UTF8
        Write-InstallLog "Created/updated config: $ConfigPath"
    }

    Write-AgentState

    $agentScript = Join-Path $InstallPath "RmmAgent.ps1"
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$agentScript`""
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) -RepetitionDuration (New-TimeSpan -Days 3650)
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew
    $task = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description "Night Owl RMM agent heartbeat every $IntervalMinutes minutes."
    Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null
    Write-InstallLog "Scheduled task created/updated: $TaskName -> $agentScript"

    if ($RunOnce -and -not $InstallAsService) {
        powershell.exe -NoProfile -ExecutionPolicy Bypass -File $agentScript
        Write-InstallLog "RunOnce completed with exit code $LASTEXITCODE"
    }

    if ($RunCheck -and -not $InstallAsService) {
        $checkScript = Join-Path $InstallPath "Check-RmmAgent.ps1"
        powershell.exe -NoProfile -ExecutionPolicy Bypass -File $checkScript -SourcePath $SourcePath -InstallPath $InstallPath
        Write-InstallLog "RunCheck completed with exit code $LASTEXITCODE"
    }

    if ($InstallAsService) {
        $serviceInstallScript = Join-Path $InstallPath "Install-RmmAgentService.ps1"
        if (-not (Test-Path $serviceInstallScript)) {
            throw "InstallAsService requested but Install-RmmAgentService.ps1 is missing."
        }
        $serviceArgs = @(
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", $serviceInstallScript,
            "-SourcePath", $InstallPath,
            "-BasePath", $ProgramDataPath,
            "-LegacyInstallPath", $InstallPath
        )
        if (-not [string]::IsNullOrWhiteSpace($ServerUrl)) {
            $serviceArgs += @("-ServerUrl", $ServerUrl)
        }
        if (-not [string]::IsNullOrWhiteSpace($NssmPath)) {
            $serviceArgs += @("-NssmPath", $NssmPath)
        }
        if (-not [string]::IsNullOrWhiteSpace($WinswPath)) {
            $serviceArgs += @("-WinswPath", $WinswPath)
        }
        if ($RunOnce) {
            $serviceArgs += "-RunOnce"
        }
        if ($DebugMode) {
            $serviceArgs += "-DebugMode"
        }
        if ($KeepScheduledTaskFallback) {
            $serviceArgs += "-KeepScheduledTaskFallback"
        }
        & powershell.exe @serviceArgs
        if ($LASTEXITCODE -ne 0) {
            throw "Service installation failed with exit code $LASTEXITCODE."
        }
        Write-InstallLog "Service mode installed: NightOwlAgent"

        if (-not $KeepScheduledTaskFallback) {
            try {
                Disable-ScheduledTask -TaskName $TaskName | Out-Null
                Write-InstallLog "Scheduled task disabled because service mode is active. Re-enable it or install with -KeepScheduledTaskFallback if needed."
            }
            catch {
                Write-InstallLog "Could not disable scheduled task fallback: $($_.Exception.Message)" "WARN"
            }
        }
    }

    if ($RunCheck -and $InstallAsService) {
        $checkScript = Join-Path $InstallPath "Check-RmmAgent.ps1"
        powershell.exe -NoProfile -ExecutionPolicy Bypass -File $checkScript -SourcePath $SourcePath -InstallPath $InstallPath -ProgramDataPath $ProgramDataPath
        Write-InstallLog "RunCheck completed with exit code $LASTEXITCODE"
    }

    Write-InstallLog "Installation complete."
}
catch {
    Write-InstallLog "Installation failed: $($_.Exception.Message)" "ERROR"
    exit 1
}
