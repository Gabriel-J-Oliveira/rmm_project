Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$MetadataUrl = "__NIGHTOWL_DEPLOYMENT_METADATA_URL__"
$Stage = "start"
$TempRoot = Join-Path $env:TEMP "NightOwlDeployment"
$BootstrapLogPath = Join-Path $TempRoot "bootstrap.log"
$Token = $env:NIGHTOWL_DEPLOYMENT_TOKEN

function Protect-NightOwlLogValue([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return ""
    }
    if ($Value -match "(?i)deploy_|token|secret|password|private") {
        return "[REDACTED]"
    }
    return $Value
}

function Write-NightOwlBootstrapLog([string]$Status, [hashtable]$Fields) {
    try {
        New-Item -ItemType Directory -Force -Path $TempRoot | Out-Null
        $record = [ordered]@{
            timestamp = (Get-Date).ToUniversalTime().ToString("o")
            stage = $Stage
            status = $Status
        }
        foreach ($key in $Fields.Keys) {
            $record[$key] = Protect-NightOwlLogValue ([string]$Fields[$key])
        }
        $record | ConvertTo-Json -Depth 5 -Compress | Add-Content -Path $BootstrapLogPath -Encoding UTF8
    }
    catch {
        Write-Warning ("NightOwl bootstrap log unavailable: {0}" -f $_.Exception.Message)
    }
}

function Write-NightOwlResult([string]$Status, [hashtable]$Fields) {
    $parts = @("NightOwl installation $Status")
    foreach ($key in $Fields.Keys) {
        $value = [string]$Fields[$key]
        if ($value -match "(?i)token|secret|password|private") {
            $value = "[REDACTED]"
        }
        $parts += "$key=$value"
    }
    Write-Host ($parts -join "`n")
}

function Assert-Windows {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw "BOOTSTRAP_WINDOWS_REQUIRED"
    }
}

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "BOOTSTRAP_ADMIN_REQUIRED"
    }
}

function Assert-Https([string]$Url) {
    $uri = [Uri]$Url
    if ($uri.Scheme -ne "https") {
        throw "BOOTSTRAP_HTTPS_REQUIRED"
    }
}

function Get-Sha256([string]$Path) {
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
}

function Invoke-JsonGet([string]$Url, [string]$DeploymentToken) {
    return Invoke-RestMethod -Method Get -Uri $Url -Headers @{ "X-NightOwl-Deployment-Token" = $DeploymentToken } -UseBasicParsing
}

try {
    $Stage = "preflight"
    Assert-Windows
    Assert-Administrator
    if ([string]::IsNullOrWhiteSpace($Token)) {
        throw "BOOTSTRAP_TOKEN_REQUIRED"
    }
    Assert-Https $MetadataUrl

    $Stage = "metadata"
    $metadata = Invoke-JsonGet -Url $MetadataUrl -DeploymentToken $Token
    Assert-Https ([string]$metadata.server_url)
    Assert-Https ([string]$metadata.release.installer_url)
    Assert-Https ([string]$metadata.release.package_url)
    Assert-Https ([string]$metadata.trusted_public_keys.url)

    $Stage = "download"
    New-Item -ItemType Directory -Force -Path $TempRoot | Out-Null
    $trustPath = Join-Path $TempRoot "release-public-keys.json"
    $installerPath = Join-Path $TempRoot "Install-NightOwlAgentDotNet.ps1"
    Invoke-WebRequest -Uri ([string]$metadata.trusted_public_keys.url) -OutFile $trustPath -UseBasicParsing
    $actualTrustSha = Get-Sha256 $trustPath
    $expectedTrustSha = ([string]$metadata.trusted_public_keys.sha256).ToLowerInvariant()
    if ($actualTrustSha -ne $expectedTrustSha) {
        throw "BOOTSTRAP_TRUST_SHA_MISMATCH"
    }
    Invoke-WebRequest -Uri ([string]$metadata.release.installer_url) -OutFile $installerPath -UseBasicParsing

    $Stage = "install"
    $installArgs = @(
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        $installerPath,
        "-Install",
        "-InstallAsService",
        "-ServerUrl",
        ([string]$metadata.server_url),
        "-PackageUrl",
        ([string]$metadata.release.package_url),
        "-TrustedPublicKeysPath",
        $trustPath,
        "-ExpectedVersion",
        ([string]$metadata.release.version),
        "-ExpectedChannel",
        ([string]$metadata.release.channel),
        "-ExpectedPackageSha256",
        ([string]$metadata.release.sha256),
        "-ExpectedReleaseId",
        ([string]$metadata.release.id),
        "-EnrollmentToken",
        $Token,
        "-RunCheck",
        "-NonInteractive"
    )
    if (-not [string]::IsNullOrWhiteSpace([string]$metadata.release.git_commit)) {
        $installArgs += @("-ExpectedGitCommit", ([string]$metadata.release.git_commit))
    }
    & powershell.exe @installArgs
    $installerExitCode = $LASTEXITCODE
    if ($installerExitCode -ne 0) {
        throw "BOOTSTRAP_INSTALLER_FAILED:$installerExitCode"
    }

    $Stage = "completed"
    $machineId = ""
    $version = [string]$metadata.release.version
    $configPath = "C:\ProgramData\NightOwl\Config\agent.config.json"
    if (Test-Path $configPath) {
        try {
            $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
            $machineId = [string]$config.machineId
        } catch {
            $machineId = ""
        }
    }
    $serviceStatus = ""
    try {
        $serviceStatus = (Get-Service -Name "NightOwlAgentDotNet" -ErrorAction Stop).Status.ToString()
    } catch {
        $serviceStatus = "Unknown"
    }
    Write-NightOwlResult "completed" @{
        hostname = $env:COMPUTERNAME
        machine_id = $machineId
        version = $version
        service_status = $serviceStatus
        enrollment_status = "completed"
    }
    Write-NightOwlBootstrapLog "completed" @{
        hostname = $env:COMPUTERNAME
        machine_id = $machineId
        version = $version
        service_status = $serviceStatus
    }
    $global:NightOwlDeploymentBootstrapExitCode = 0
} catch {
    $message = [string]$_.Exception.Message
    $errorCode = ($message.Split(":")[0])
    Write-NightOwlResult "failed" @{
        stage = $Stage
        error_code = $errorCode
        error_message = $message
    }
    Write-NightOwlBootstrapLog "failed" @{
        error_code = $errorCode
        error_message = $message
    }
    Write-Error ("NightOwl installation failed: {0}" -f $message) -ErrorAction Continue
    $global:NightOwlDeploymentBootstrapExitCode = 1
} finally {
    Remove-Item Env:\NIGHTOWL_DEPLOYMENT_TOKEN -ErrorAction SilentlyContinue
}
