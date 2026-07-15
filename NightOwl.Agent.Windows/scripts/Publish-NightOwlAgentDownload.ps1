param(
    [string]$ProjectPath = ".\NightOwl.Agent.Windows\NightOwl.Agent.Windows.csproj",
    [string]$TrayProjectPath = ".\NightOwl.Agent.Tray\NightOwl.Agent.Tray.csproj",
    [string]$UpdaterProjectPath = ".\NightOwl.Agent.Updater\NightOwl.Agent.Updater.csproj",
    [string]$PublishDir = ".\NightOwl.Agent.Windows\publish\win-x64",
    [string]$OutputDir = ".\NightOwl.Agent.Windows\publish\downloads\agent\windows",
    [string]$Version = "0.1.0.7",
    [string]$PublicBaseUrl = "https://nightowl.controlsul.com.br/downloads/nightowl-agent",
    [string]$Runtime = "win-x64",
    [switch]$SkipPublish
)

$ErrorActionPreference = "Stop"

function Resolve-FullPath([string]$Path) {
    $executionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path)
}

function Get-FileSha256([string]$Path) {
    return (Get-FileHash -Algorithm SHA256 -Path $Path).Hash.ToLowerInvariant()
}

$publishPath = Resolve-FullPath $PublishDir
$outputPath = Resolve-FullPath $OutputDir

if (-not $SkipPublish) {
    if (Test-Path $publishPath) {
        Remove-Item -Path $publishPath -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $publishPath | Out-Null
    dotnet publish $ProjectPath -c Release -r $Runtime --self-contained true -o $publishPath
    dotnet publish $TrayProjectPath -c Release -r $Runtime --self-contained true -o $publishPath
    dotnet publish $UpdaterProjectPath -c Release -r $Runtime --self-contained true -o $publishPath
}

if (-not (Test-Path (Join-Path $publishPath "NightOwl.Agent.Windows.exe"))) {
    throw "Publish invalido: NightOwl.Agent.Windows.exe nao encontrado em $publishPath"
}
if (-not (Test-Path (Join-Path $publishPath "NightOwl.Agent.Tray.exe"))) {
    throw "Publish invalido: NightOwl.Agent.Tray.exe nao encontrado em $publishPath"
}
if (-not (Test-Path (Join-Path $publishPath "NightOwl.Agent.Updater.exe"))) {
    throw "Publish invalido: NightOwl.Agent.Updater.exe nao encontrado em $publishPath"
}
$iconSource = Resolve-FullPath ".\assets\icons\NightOwl.ico"
$publishIconDir = Join-Path $publishPath "assets\icons"
New-Item -ItemType Directory -Force -Path $publishIconDir | Out-Null
Copy-Item -Path $iconSource -Destination (Join-Path $publishIconDir "NightOwl.ico") -Force
Copy-Item -Path $iconSource -Destination (Join-Path $publishPath "NightOwl.ico") -Force

$localVersion = [ordered]@{
    version = $Version
    installedAt = ""
    channel = "stable"
    packageSha256 = ""
    updatedBy = "installer"
}
$localVersion | ConvertTo-Json -Depth 5 | Set-Content -Path (Join-Path $publishPath "agent.version.json") -Encoding UTF8

New-Item -ItemType Directory -Force -Path $outputPath | Out-Null

$zipPath = Join-Path $outputPath "NightOwl.Agent.Windows.zip"
if (Test-Path $zipPath) {
    Remove-Item -Path $zipPath -Force
}

$packageTemp = Join-Path $env:TEMP ("NightOwlAgentZip-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $packageTemp | Out-Null
try {
    Get-ChildItem -Path $publishPath -Force | Where-Object {
        $_.Name -notin @("NightOwl.Agent.Windows.zip", "version.json", "checksums.json")
    } | ForEach-Object {
        Copy-Item -Path $_.FullName -Destination $packageTemp -Recurse -Force
    }
    Compress-Archive -Path (Join-Path $packageTemp "*") -DestinationPath $zipPath -Force
}
finally {
    if (Test-Path $packageTemp) {
        Remove-Item -Path $packageTemp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

$installScript = Join-Path $publishPath "Install-NightOwlAgentDotNet.ps1"
$uninstallScript = Join-Path $publishPath "Uninstall-NightOwlAgentDotNet.ps1"
if (-not (Test-Path $installScript)) {
    $installScript = Resolve-FullPath ".\NightOwl.Agent.Windows\scripts\Install-NightOwlAgentDotNet.ps1"
}
if (-not (Test-Path $uninstallScript)) {
    $uninstallScript = Resolve-FullPath ".\NightOwl.Agent.Windows\scripts\Uninstall-NightOwlAgentDotNet.ps1"
}

Copy-Item -Path $installScript -Destination (Join-Path $outputPath "Install-NightOwlAgentDotNet.ps1") -Force
Copy-Item -Path $uninstallScript -Destination (Join-Path $outputPath "Uninstall-NightOwlAgentDotNet.ps1") -Force
Copy-Item -Path (Join-Path $publishPath "NightOwl.ico") -Destination (Join-Path $outputPath "NightOwl.ico") -Force

$zipSha = Get-FileSha256 $zipPath
$installSha = Get-FileSha256 (Join-Path $outputPath "Install-NightOwlAgentDotNet.ps1")
$uninstallSha = Get-FileSha256 (Join-Path $outputPath "Uninstall-NightOwlAgentDotNet.ps1")
$iconSha = Get-FileSha256 (Join-Path $outputPath "NightOwl.ico")
$zipSize = (Get-Item $zipPath).Length
$installSize = (Get-Item (Join-Path $outputPath "Install-NightOwlAgentDotNet.ps1")).Length
$uninstallSize = (Get-Item (Join-Path $outputPath "Uninstall-NightOwlAgentDotNet.ps1")).Length
$iconSize = (Get-Item (Join-Path $outputPath "NightOwl.ico")).Length
$publishedAt = (Get-Date).ToUniversalTime().ToString("o")

$versionManifest = [ordered]@{
    product = "NightOwl Agent Windows"
    agent = "NightOwl.Agent.Windows"
    channel = "stable"
    version = $Version
    publishedAt = $publishedAt
    minimumSupportedVersion = "0.1.0"
    packageUrl = ("{0}/NightOwl.Agent.Windows.zip" -f $PublicBaseUrl.TrimEnd("/"))
    checksumUrl = ("{0}/checksums.json" -f $PublicBaseUrl.TrimEnd("/"))
    installerUrl = ("{0}/Install-NightOwlAgentDotNet.ps1" -f $PublicBaseUrl.TrimEnd("/"))
    notes = "Atualizacao do NightOwl Agent Windows."
    requiresRestart = $true
    force = $false
    platform = "windows-x64"
    package = "NightOwl.Agent.Windows.zip"
    published_at = $publishedAt
    sha256 = $zipSha
}
$versionManifest | ConvertTo-Json -Depth 5 | Set-Content -Path (Join-Path $outputPath "version.json") -Encoding UTF8

$checksumManifest = [ordered]@{
    "NightOwl.Agent.Windows.zip" = $zipSha
    "Install-NightOwlAgentDotNet.ps1" = $installSha
    "Uninstall-NightOwlAgentDotNet.ps1" = $uninstallSha
    "NightOwl.ico" = $iconSha
    files = @(
        [ordered]@{ name = "NightOwl.Agent.Windows.zip"; sha256 = $zipSha; size = $zipSize },
        [ordered]@{ name = "Install-NightOwlAgentDotNet.ps1"; sha256 = $installSha; size = $installSize },
        [ordered]@{ name = "Uninstall-NightOwlAgentDotNet.ps1"; sha256 = $uninstallSha; size = $uninstallSize },
        [ordered]@{ name = "NightOwl.ico"; sha256 = $iconSha; size = $iconSize }
    )
}
$checksumManifest | ConvertTo-Json -Depth 6 | Set-Content -Path (Join-Path $outputPath "checksums.json") -Encoding UTF8

Write-Host "Pacote NightOwl pronto em: $outputPath"
Write-Host "Copie o conteudo para: /opt/nightowl/downloads/agent/windows/"
