param(
    [string]$Version = "",

    [ValidateSet("development", "pilot", "stable")]
    [string]$Channel = "development",

    [string]$SigningKeyPath = "",
    [string]$SigningKeyId = "",
    [string]$TrustedPublicKeysPath = "",
    [string]$RemoteAlias = "",
    [string]$RemoteProjectPath = "",
    [string]$PublicBaseUrl = "",
    [string]$ConfigPath = "",
    [switch]$Ci,
    [switch]$SkipRemoteDjangoCommandCheck,
    [switch]$SelfTest
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$script:PublisherScript = Join-Path $script:RepoRoot "scripts\Publish-NightOwlAgentRelease.ps1"

function Write-Step([string]$Message) {
    Write-Host ("[nightowl-runner-test] {0}" -f $Message)
}

function Fail([string]$Code, [string]$Message) {
    $ex = New-Object System.Exception($Message)
    $ex.Data["Code"] = $Code
    throw $ex
}

function Get-FirstNonEmpty([string[]]$Values) {
    foreach ($value in $Values) {
        if (-not [string]::IsNullOrWhiteSpace($value)) { return $value }
    }
    return ""
}

function Get-DefaultPublisherConfigPath {
    if (-not [string]::IsNullOrWhiteSpace($env:NIGHTOWL_RELEASE_PUBLISHER_CONFIG)) { return $env:NIGHTOWL_RELEASE_PUBLISHER_CONFIG }
    if (-not [string]::IsNullOrWhiteSpace($env:USERPROFILE)) { return (Join-Path $env:USERPROFILE ".nightowl\release-publisher.json") }
    return ""
}

function Read-LocalConfig([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path -LiteralPath $Path)) { return $null }
    try { return Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json }
    catch { Fail "RUNNER_CONFIG_INVALID" "release-publisher.json invalido: $($_.Exception.Message)" }
}

function Resolve-RunnerValue([string]$ParameterValue, [string[]]$EnvironmentValues, $ConfigValue, [string]$DefaultValue = "") {
    if (-not [string]::IsNullOrWhiteSpace($ParameterValue)) { return $ParameterValue }
    $envValue = Get-FirstNonEmpty $EnvironmentValues
    if (-not [string]::IsNullOrWhiteSpace($envValue)) { return $envValue }
    if ($null -ne $ConfigValue -and -not [string]::IsNullOrWhiteSpace([string]$ConfigValue)) { return [string]$ConfigValue }
    return $DefaultValue
}

function Get-ConfigProperty($Config, [string]$Name) {
    if ($null -eq $Config) { return $null }
    $property = $Config.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}

function Invoke-NativeCaptured([string]$FileName, [string[]]$Arguments, [string]$FailureCode) {
    $stdoutFile = [System.IO.Path]::GetTempFileName()
    $stderrFile = [System.IO.Path]::GetTempFileName()
    try {
        & $FileName @Arguments > $stdoutFile 2> $stderrFile
        $nativeExitCode = $LASTEXITCODE
        $stdout = Get-Content -Raw -Path $stdoutFile -ErrorAction SilentlyContinue
        $stderr = Get-Content -Raw -Path $stderrFile -ErrorAction SilentlyContinue
        if ($nativeExitCode -ne 0) {
            Fail $FailureCode ("Comando falhou: {0} {1}`nExit code: {2}`nSTDOUT:`n{3}`nSTDERR:`n{4}" -f $FileName, ($Arguments -join " "), $nativeExitCode, ($stdout -replace '\s+$',''), ($stderr -replace '\s+$',''))
        }
        return @($stdout -split "`r?`n" | Where-Object { $_ -ne "" })
    }
    finally {
        Remove-Item -LiteralPath $stdoutFile, $stderrFile -Force -ErrorAction SilentlyContinue
    }
}

function Assert-CommandAvailable([string]$Name) {
    if ($null -eq (Get-Command $Name -ErrorAction SilentlyContinue)) {
        Fail "RUNNER_PREREQUISITE_MISSING" "Comando obrigatorio nao encontrado no PATH: $Name"
    }
}

function Assert-Version([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        Fail "RUNNER_VERSION_MISSING" "Version e obrigatoria fora de -SelfTest."
    }
    if ($Value -notmatch '^\d+\.\d+\.\d+(\.\d+)?(-[0-9A-Za-z][0-9A-Za-z.-]*)?$') {
        Fail "RUNNER_VERSION_INVALID" "Version invalida: $Value"
    }
}

function Assert-WindowsPowerShellSupported {
    if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
        Fail "RUNNER_OS_UNSUPPORTED" "Este runner deve ser Windows."
    }
    if ($PSVersionTable.PSVersion.Major -lt 5) {
        Fail "RUNNER_POWERSHELL_UNSUPPORTED" "PowerShell 5.1 ou PowerShell 7+ e obrigatorio."
    }
}

function Invoke-PublisherValidateOnly([hashtable]$Resolved) {
    $arguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $script:PublisherScript,
        "-Version", $Version,
        "-Channel", $Channel,
        "-ValidateOnly",
        "-Ci"
    )
    foreach ($pair in @(
        @{ flag = "-SigningKeyPath"; value = $Resolved.SigningKeyPath },
        @{ flag = "-SigningKeyId"; value = $Resolved.SigningKeyId },
        @{ flag = "-TrustedPublicKeysPath"; value = $Resolved.TrustedPublicKeysPath },
        @{ flag = "-RemoteAlias"; value = $Resolved.RemoteAlias },
        @{ flag = "-RemoteProjectPath"; value = $Resolved.RemoteProjectPath },
        @{ flag = "-PublicBaseUrl"; value = $Resolved.PublicBaseUrl },
        @{ flag = "-ConfigPath"; value = $Resolved.ConfigPath }
    )) {
        if (-not [string]::IsNullOrWhiteSpace([string]$pair.value)) {
            $arguments += @($pair.flag, [string]$pair.value)
        }
    }
    Invoke-NativeCaptured "powershell.exe" $arguments "RUNNER_PUBLISHER_VALIDATE_FAILED" | Out-Null
}

function Test-RemoteDjangoCommands([hashtable]$Resolved) {
    if ($SkipRemoteDjangoCommandCheck) {
        Write-Step "SkipRemoteDjangoCommandCheck ativo; pulando verificacao remota dos management commands."
        return
    }
    $remoteProject = $Resolved.RemoteProjectPath.Replace("'", "'\''")
    $command = "set -euo pipefail; cd '$remoteProject'; source .venv/bin/activate; python manage.py help import_agent_release >/dev/null; python manage.py help verify_agent_release >/dev/null; python manage.py check --deploy --fail-level ERROR >/dev/null"
    Invoke-NativeCaptured "ssh.exe" @("-o", "BatchMode=yes", $Resolved.RemoteAlias, "bash", "-lc", $command) "RUNNER_DJANGO_CHECK_FAILED" | Out-Null
}

function Invoke-SelfTest {
    Assert-WindowsPowerShellSupported
    Assert-CommandAvailable "powershell.exe"
    Write-Step "SelfTest OK: validacoes estaticas do runner carregadas."
}

try {
    if ($SelfTest) {
        Invoke-SelfTest
        exit 0
    }

    Assert-WindowsPowerShellSupported
    Assert-Version $Version
    foreach ($command in @("dotnet", "git", "ssh.exe", "scp.exe", "powershell.exe")) {
        Assert-CommandAvailable $command
    }
    if (-not (Test-Path -LiteralPath $script:PublisherScript)) {
        Fail "RUNNER_PUBLISHER_MISSING" "Publisher nao encontrado: $script:PublisherScript"
    }

    $effectiveConfigPath = if ([string]::IsNullOrWhiteSpace($ConfigPath)) { Get-DefaultPublisherConfigPath } else { $ConfigPath }
    $config = Read-LocalConfig $effectiveConfigPath
    $resolved = @{
        SigningKeyPath = Resolve-RunnerValue $SigningKeyPath @($env:NIGHTOWL_RELEASE_SIGNING_KEY_PATH, $env:NIGHTOWL_RELEASE_SIGNING_KEY) (Get-ConfigProperty $config "signing_key_path")
        SigningKeyId = Resolve-RunnerValue $SigningKeyId @($env:NIGHTOWL_RELEASE_SIGNING_KEY_ID) (Get-ConfigProperty $config "signing_key_id")
        TrustedPublicKeysPath = Resolve-RunnerValue $TrustedPublicKeysPath @($env:NIGHTOWL_RELEASE_PUBLIC_KEYS_PATH, $env:NIGHTOWL_RELEASE_TRUSTED_KEYS_JSON) (Get-ConfigProperty $config "trusted_public_keys_path")
        RemoteAlias = Resolve-RunnerValue $RemoteAlias @($env:NIGHTOWL_RELEASE_SSH_TARGET, $env:NIGHTOWL_RELEASE_REMOTE_ALIAS) (Get-ConfigProperty $config "remote_alias") "nightowl-release"
        RemoteProjectPath = Resolve-RunnerValue $RemoteProjectPath @($env:NIGHTOWL_RELEASE_DJANGO_ROOT, $env:NIGHTOWL_RELEASE_REMOTE_ROOT, $env:NIGHTOWL_RELEASE_REMOTE_PROJECT_PATH) (Get-ConfigProperty $config "remote_project_path") "/opt/nightowl"
        PublicBaseUrl = Resolve-RunnerValue $PublicBaseUrl @($env:NIGHTOWL_RELEASE_PUBLIC_BASE_URL) (Get-ConfigProperty $config "public_base_url") "https://nightowl.controlsul.com.br/downloads/nightowl-agent"
        ConfigPath = $effectiveConfigPath
    }

    foreach ($required in @("SigningKeyPath", "SigningKeyId", "TrustedPublicKeysPath", "RemoteAlias", "RemoteProjectPath", "PublicBaseUrl")) {
        if ([string]::IsNullOrWhiteSpace([string]$resolved[$required])) {
            Fail "RUNNER_CONFIG_MISSING" "Configuracao obrigatoria ausente: $required"
        }
    }
    if (-not (Test-Path -LiteralPath $resolved.SigningKeyPath)) { Fail "RUNNER_SIGNING_KEY_MISSING" "Chave privada nao encontrada no caminho configurado." }
    if (-not (Test-Path -LiteralPath $resolved.TrustedPublicKeysPath)) { Fail "RUNNER_PUBLIC_KEYS_MISSING" "Bundle publico nao encontrado no caminho configurado." }

    $sdkList = Invoke-NativeCaptured "dotnet" @("--list-sdks") "RUNNER_DOTNET_FAILED"
    if (-not ($sdkList | Where-Object { $_ -match '^8\.' })) {
        Fail "RUNNER_DOTNET_SDK_MISSING" "dotnet SDK 8.x nao encontrado."
    }
    Invoke-NativeCaptured "git" @("--version") "RUNNER_GIT_FAILED" | Out-Null
    Invoke-NativeCaptured "ssh.exe" @("-V") "RUNNER_SSH_FAILED" | Out-Null

    Invoke-PublisherValidateOnly $resolved
    Test-RemoteDjangoCommands $resolved

    Write-Step "RUNNER VALIDADO. Nenhuma release foi publicada."
    exit 0
}
catch {
    $code = if ($_.Exception.Data.Contains("Code")) { [string]$_.Exception.Data["Code"] } else { "RUNNER_UNEXPECTED_ERROR" }
    Write-Error ("Falha [{0}]: {1}" -f $code, $_.Exception.Message)
    exit 1
}
