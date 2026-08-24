Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$MetadataUrl = "__NIGHTOWL_DEPLOYMENT_METADATA_URL__"
$Stage = "start"
$TempRoot = Join-Path $env:TEMP "NightOwlDeployment"
$Token = $env:NIGHTOWL_DEPLOYMENT_TOKEN

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
        "-EnrollmentToken",
        $Token,
        "-RunCheck",
        "-NonInteractive"
    )
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
    exit 0
} catch {
    $message = [string]$_.Exception.Message
    Write-NightOwlResult "failed" @{
        stage = $Stage
        error_code = ($message.Split(":")[0])
        error_message = $message
    }
    exit 1
} finally {
    Remove-Item Env:\NIGHTOWL_DEPLOYMENT_TOKEN -ErrorAction SilentlyContinue
}
