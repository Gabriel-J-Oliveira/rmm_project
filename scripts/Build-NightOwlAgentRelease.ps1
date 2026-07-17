param(
    [Parameter(Mandatory = $true)]
    [string]$Version,

    [switch]$Force,
    [switch]$ValidateOnly,
    [switch]$Publish,

    [string]$ReleaseDir = "",
    [string]$PublishHost = "",
    [string]$PublishPath = "/opt/nightowl/downloads/agent/windows",
    [string]$PublicBaseUrl = "https://nightowl.controlsul.com.br/downloads/nightowl-agent",
    [string]$Runtime = "win-x64",
    [string]$MinimumUpdaterVersion = "0.1.0.7"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$releaseRoot = Join-Path $repoRoot "artifacts\nightowl-agent\releases"
$workRoot = Join-Path $repoRoot "artifacts\nightowl-agent\work"
$agentProject = Join-Path $repoRoot "NightOwl.Agent.Windows\NightOwl.Agent.Windows.csproj"
$trayProject = Join-Path $repoRoot "NightOwl.Agent.Tray\NightOwl.Agent.Tray.csproj"
$updaterProject = Join-Path $repoRoot "NightOwl.Agent.Updater\NightOwl.Agent.Updater.csproj"
$sharedProject = Join-Path $repoRoot "NightOwl.Agent.Shared\NightOwl.Agent.Shared.csproj"
$testProject = Join-Path $repoRoot "NightOwl.Agent.Shared.Tests\NightOwl.Agent.Shared.Tests.csproj"
$installScript = Join-Path $repoRoot "NightOwl.Agent.Windows\scripts\Install-NightOwlAgentDotNet.ps1"
$uninstallScript = Join-Path $repoRoot "NightOwl.Agent.Windows\scripts\Uninstall-NightOwlAgentDotNet.ps1"
$iconPath = Join-Path $repoRoot "assets\icons\NightOwl.ico"

function Write-Step([string]$Message) {
    Write-Host ("[nightowl-release] {0}" -f $Message)
}

function Resolve-FullPath([string]$Path) {
    $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path)
}

function Assert-Version([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        throw "Version nao pode ser vazia."
    }
    if ($Value -notmatch '^\d+\.\d+\.\d+(\.\d+)?$') {
        throw "Version invalida: $Value. Use formato numerico major.minor.patch ou major.minor.patch.build, ex: 0.1.0.8."
    }
}

function Convert-VersionParts([string]$Value) {
    $parts = @($Value.Split(".") | ForEach-Object { [int]$_ })
    while ($parts.Count -lt 4) {
        $parts += 0
    }
    return ,$parts[0..3]
}

function Compare-NightOwlVersion([string]$Left, [string]$Right) {
    $a = Convert-VersionParts $Left
    $b = Convert-VersionParts $Right
    for ($i = 0; $i -lt 4; $i++) {
        if ($a[$i] -lt $b[$i]) { return -1 }
        if ($a[$i] -gt $b[$i]) { return 1 }
    }
    return 0
}

function Get-CurrentProjectVersion {
    [xml]$project = Get-Content -Path $agentProject
    $value = [string]$project.Project.PropertyGroup.Version
    if ([string]::IsNullOrWhiteSpace($value)) { return "0.0.0" }
    return $value
}

function Get-GitCommit {
    try {
        $commit = (& git -C $repoRoot rev-parse HEAD 2>$null)
        if ($LASTEXITCODE -eq 0) { return [string]$commit.Trim() }
    }
    catch {}
    return ""
}

function Get-FileSha256([string]$Path) {
    return (Get-FileHash -Algorithm SHA256 -Path $Path).Hash.ToLowerInvariant()
}

function Invoke-Checked([string]$FileName, [string[]]$Arguments) {
    Write-Step ("Executando: {0} {1}" -f $FileName, ($Arguments -join " "))
    & $FileName @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Comando falhou com exit code ${LASTEXITCODE}: $FileName $($Arguments -join ' ')"
    }
}

function Copy-ReleasePayload([string]$PublishDir, [string]$PackageDir) {
    $forbiddenNames = @(
        "agent.config.json",
        "agent.identity.json",
        "agent.state.json",
        "agent-dotnet.state.json",
        "update-state.json",
        "version.json",
        "checksums.json",
        "release-manifest.json",
        "NightOwl.Agent.Windows.zip"
    )
    $forbiddenExtensions = @(".pdb", ".log", ".tmp", ".ps1")

    New-Item -ItemType Directory -Force -Path $PackageDir | Out-Null
    Get-ChildItem -Path $PublishDir -Force | ForEach-Object {
        Copy-PayloadItem -Source $_.FullName -Destination (Join-Path $PackageDir $_.Name) -ForbiddenNames $forbiddenNames -ForbiddenExtensions $forbiddenExtensions
    }
}

function Copy-PayloadItem([string]$Source, [string]$Destination, [string[]]$ForbiddenNames, [string[]]$ForbiddenExtensions) {
    $item = Get-Item -LiteralPath $Source -Force
    if ($item.Name -in $ForbiddenNames) { return }
    if ($item.Name -like "*.preserved-*") { return }
    if ($item.Name -in @("bin", "obj", ".git", ".vs", "artifacts", "downloads", "publish", "releases")) { return }
    if (-not $item.PSIsContainer -and $ForbiddenExtensions -contains $item.Extension.ToLowerInvariant()) { return }
    if ($item.PSIsContainer) {
        New-Item -ItemType Directory -Force -Path $Destination | Out-Null
        Get-ChildItem -LiteralPath $item.FullName -Force | ForEach-Object {
            Copy-PayloadItem -Source $_.FullName -Destination (Join-Path $Destination $_.Name) -ForbiddenNames $ForbiddenNames -ForbiddenExtensions $ForbiddenExtensions
        }
    }
    else {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destination) | Out-Null
        Copy-Item -LiteralPath $item.FullName -Destination $Destination -Force
    }
}

function New-AgentVersionFile([string]$Path, [string]$BuildId, [string]$BuiltAt, [string]$Commit) {
    $agentVersion = [ordered]@{
        product = "NightOwl Agent Windows"
        version = $Version
        build_id = $BuildId
        built_at = $BuiltAt
        git_commit = $Commit
        minimum_updater_version = $MinimumUpdaterVersion
        package = "NightOwl.Agent.Windows.zip"
        installedAt = ""
        channel = "stable"
        packageSha256 = ""
        updatedBy = "installer"
    }
    $agentVersion | ConvertTo-Json -Depth 6 | Set-Content -Path $Path -Encoding UTF8
}

function Compress-ReleaseZip([string]$PackageDir, [string]$ZipPath) {
    if (Test-Path $ZipPath) { Remove-Item -Path $ZipPath -Force }
    Compress-Archive -Path (Join-Path $PackageDir "*") -DestinationPath $ZipPath -Force
}

function Read-ZipEntryText([System.IO.Compression.ZipArchive]$Zip, [string]$EntryName) {
    $entry = $Zip.GetEntry($EntryName)
    if ($null -eq $entry) { throw "Entrada ausente no ZIP: $EntryName" }
    $reader = New-Object System.IO.StreamReader($entry.Open())
    try { return $reader.ReadToEnd() }
    finally { $reader.Dispose() }
}

function Test-ForbiddenZipEntry([string]$Name) {
    $normalized = $Name.Replace("\", "/").ToLowerInvariant()
    if ($normalized -match '(^|/)(bin|obj|logs?|config|identity|state|diagnostics|artifacts|downloads|publish|releases)(/|$)') { return $true }
    if ($normalized -match 'agent\.config\.json$') { return $true }
    if ($normalized -match 'agent\.identity\.json$') { return $true }
    if ($normalized -match 'agent(\.|-)state\.json$') { return $true }
    if ($normalized -match 'agent-dotnet\.state\.json$') { return $true }
    if ($normalized -match 'update-state\.json$') { return $true }
    if ($normalized -match '\.preserved-') { return $true }
    if ($normalized -match '\.log$|\.tmp$|\.pdb$') { return $true }
    if ($normalized -match 'token|machine_id') { return $true }
    return $false
}

function Validate-Release([string]$Path) {
    if (-not (Test-Path $Path)) { throw "ReleaseDir nao encontrado: $Path" }
    $zipPath = Join-Path $Path "NightOwl.Agent.Windows.zip"
    $versionPath = Join-Path $Path "version.json"
    $checksumsPath = Join-Path $Path "checksums.json"
    $manifestPath = Join-Path $Path "release-manifest.json"
    foreach ($required in @($zipPath, $versionPath, $checksumsPath, $manifestPath)) {
        if (-not (Test-Path $required)) { throw "Artefato obrigatorio ausente: $required" }
    }

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::OpenRead($zipPath)
    try {
        $entryNames = @($zip.Entries | ForEach-Object { $_.FullName.Replace("\", "/") })
        foreach ($requiredEntry in @(
            "NightOwl.Agent.Windows.exe",
            "NightOwl.Agent.Tray.exe",
            "NightOwl.Agent.Updater.exe",
            "NightOwl.Agent.Shared.dll",
            "assets/icons/NightOwl.ico",
            "agent.version.json"
        )) {
            if ($entryNames -notcontains $requiredEntry) {
                throw "ZIP sem arquivo obrigatorio: $requiredEntry"
            }
        }
        foreach ($entryName in $entryNames) {
            if (Test-ForbiddenZipEntry $entryName) {
                throw "ZIP contem arquivo proibido: $entryName"
            }
        }
        $agentVersionJson = Read-ZipEntryText -Zip $zip -EntryName "agent.version.json"
        $agentVersion = $agentVersionJson | ConvertFrom-Json
        if ([string]$agentVersion.version -ne $Version) {
            throw "agent.version.json inconsistente. Esperado $Version, obtido $($agentVersion.version)"
        }
    }
    finally {
        $zip.Dispose()
    }

    $zipSha = Get-FileSha256 $zipPath
    $versionManifest = Get-Content -Raw -Path $versionPath | ConvertFrom-Json
    if ([string]$versionManifest.version -ne $Version) { throw "version.json com versao inconsistente." }
    if ([string]$versionManifest.sha256 -ne $zipSha) { throw "version.json com SHA256 inconsistente." }
    if ([long]$versionManifest.size -ne (Get-Item $zipPath).Length) { throw "version.json com tamanho inconsistente." }

    $checksums = Get-Content -Raw -Path $checksumsPath | ConvertFrom-Json
    $zipEntry = @($checksums.files | Where-Object { $_.name -eq "NightOwl.Agent.Windows.zip" }) | Select-Object -First 1
    if ($null -eq $zipEntry -or [string]$zipEntry.sha256 -ne $zipSha) {
        throw "checksums.json sem SHA256 correto do ZIP."
    }

    Write-Step "Release validada: $Path"
}

function New-Checksums([string]$ReleasePath) {
    $files = @(
        "NightOwl.Agent.Windows.zip",
        "Install-NightOwlAgentDotNet.ps1",
        "Uninstall-NightOwlAgentDotNet.ps1",
        "NightOwl.ico",
        "version.json",
        "release-manifest.json"
    )
    $map = [ordered]@{}
    $items = @()
    foreach ($file in $files) {
        $path = Join-Path $ReleasePath $file
        if (-not (Test-Path $path)) { continue }
        $sha = Get-FileSha256 $path
        $size = (Get-Item $path).Length
        $map[$file] = $sha
        $items += [ordered]@{ name = $file; sha256 = $sha; size = $size }
    }
    $map["files"] = $items
    $map | ConvertTo-Json -Depth 8 | Set-Content -Path (Join-Path $ReleasePath "checksums.json") -Encoding UTF8
}

function Publish-LocalAtomic([string]$Source, [string]$DestinationRoot) {
    $destination = Resolve-FullPath $DestinationRoot
    $temp = Join-Path $destination (".nightowl-release-{0}-{1}" -f $Version, ([guid]::NewGuid().ToString("N")))
    $releaseStore = Join-Path $destination "releases\$Version"
    if ((Test-Path $releaseStore) -and -not $Force) {
        throw "Release ja publicada em $releaseStore. Use -Force apenas em desenvolvimento."
    }
    New-Item -ItemType Directory -Force -Path $temp | Out-Null
    try {
        Copy-Item -Path (Join-Path $Source "*") -Destination $temp -Recurse -Force
        Validate-Release $temp
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $releaseStore) | Out-Null
        if (Test-Path $releaseStore) { Remove-Item -Path $releaseStore -Recurse -Force }
        Move-Item -Path $temp -Destination $releaseStore
        foreach ($file in @("Install-NightOwlAgentDotNet.ps1", "Uninstall-NightOwlAgentDotNet.ps1", "NightOwl.ico", "NightOwl.Agent.Windows.zip", "checksums.json", "release-manifest.json")) {
            Copy-Item -Path (Join-Path $releaseStore $file) -Destination (Join-Path $destination $file) -Force
        }
        Copy-Item -Path (Join-Path $releaseStore "version.json") -Destination (Join-Path $destination "version.json") -Force
    }
    catch {
        if (Test-Path $temp) { Remove-Item -Path $temp -Recurse -Force -ErrorAction SilentlyContinue }
        throw
    }
}

function Publish-RemoteAtomic([string]$Source, [string]$HostName, [string]$DestinationRoot, [string]$ExpectedZipSha) {
    $remoteTemp = "$DestinationRoot/.nightowl-release-$Version-$([guid]::NewGuid().ToString('N'))"
    $remoteRelease = "$DestinationRoot/releases/$Version"
    Invoke-Checked "ssh" @($HostName, "mkdir -p '$remoteTemp' '$DestinationRoot/releases'")
    try {
        Invoke-Checked "scp" @("-r", (Join-Path $Source "*"), "${HostName}:$remoteTemp/")
        Invoke-Checked "ssh" @($HostName, "test -s '$remoteTemp/NightOwl.Agent.Windows.zip' && test -s '$remoteTemp/checksums.json' && test -s '$remoteTemp/version.json' && test -s '$remoteTemp/release-manifest.json'")
        Invoke-Checked "ssh" @($HostName, "actual=`$(sha256sum '$remoteTemp/NightOwl.Agent.Windows.zip' | awk '{print `$1}'); test `"x`$actual`" = `"x$ExpectedZipSha`"")
        if (-not $Force) {
            Invoke-Checked "ssh" @($HostName, "if [ -e '$remoteRelease' ]; then echo 'remote release exists: $remoteRelease' >&2; exit 17; fi")
        }
        Invoke-Checked "ssh" @($HostName, "rm -rf '$remoteRelease' && mv '$remoteTemp' '$remoteRelease'")
        Invoke-Checked "ssh" @($HostName, "cp '$remoteRelease/Install-NightOwlAgentDotNet.ps1' '$DestinationRoot/Install-NightOwlAgentDotNet.ps1' && cp '$remoteRelease/Uninstall-NightOwlAgentDotNet.ps1' '$DestinationRoot/Uninstall-NightOwlAgentDotNet.ps1' && cp '$remoteRelease/NightOwl.ico' '$DestinationRoot/NightOwl.ico' && cp '$remoteRelease/NightOwl.Agent.Windows.zip' '$DestinationRoot/NightOwl.Agent.Windows.zip' && cp '$remoteRelease/checksums.json' '$DestinationRoot/checksums.json' && cp '$remoteRelease/release-manifest.json' '$DestinationRoot/release-manifest.json' && cp '$remoteRelease/version.json' '$DestinationRoot/version.json'")
    }
    catch {
        & ssh $HostName "rm -rf '$remoteTemp'" 2>$null
        throw
    }
}

Assert-Version $Version

if ([string]::IsNullOrWhiteSpace($ReleaseDir)) {
    $ReleaseDir = Join-Path $releaseRoot $Version
}
$ReleaseDir = Resolve-FullPath $ReleaseDir

if ($ValidateOnly) {
    Validate-Release $ReleaseDir
    return
}

$currentVersion = Get-CurrentProjectVersion
if ((Compare-NightOwlVersion $Version $currentVersion) -lt 0 -and -not $Force) {
    throw "Downgrade bloqueado. Projeto atual: $currentVersion, solicitado: $Version. Use -Force apenas em desenvolvimento."
}
if ((Test-Path $ReleaseDir) -and -not $Force) {
    throw "Release ja existe: $ReleaseDir. Use -Force apenas em desenvolvimento."
}

New-Item -ItemType Directory -Force -Path $releaseRoot | Out-Null
New-Item -ItemType Directory -Force -Path $workRoot | Out-Null
if (Test-Path $ReleaseDir) { Remove-Item -Path $ReleaseDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null

$buildId = [guid]::NewGuid().ToString("N")
$builtAt = (Get-Date).ToUniversalTime().ToString("o")
$commit = Get-GitCommit
$workDir = Join-Path $workRoot "$Version-$buildId"
$publishDir = Join-Path $workDir "publish"
$packageDir = Join-Path $workDir "package"
New-Item -ItemType Directory -Force -Path $publishDir,$packageDir | Out-Null
$releaseReady = $false

$assemblyVersion = ($Version.Split(".") + @("0", "0", "0", "0"))[0..3] -join "."
$msbuildVersionArgs = @(
    "-p:Version=$Version",
    "-p:AssemblyVersion=$assemblyVersion",
    "-p:FileVersion=$assemblyVersion",
    "-p:InformationalVersion=$Version+$buildId"
)
$projects = @($sharedProject, $agentProject, $trayProject, $updaterProject, $testProject)

try {
    foreach ($project in @($sharedProject, $testProject)) {
        Invoke-Checked "dotnet" @("restore", $project)
    }

    foreach ($project in @($agentProject, $trayProject, $updaterProject)) {
        Invoke-Checked "dotnet" @("restore", $project, "-r", $Runtime)
    }

    foreach ($project in @($sharedProject, $agentProject, $trayProject, $updaterProject, $testProject)) {
        Invoke-Checked "dotnet" (@("build", $project, "-c", "Release", "--no-restore") + $msbuildVersionArgs)
    }

    Invoke-Checked "dotnet" (@("test", $testProject, "-c", "Release", "--no-restore", "--no-build") + $msbuildVersionArgs)
    Invoke-Checked "dotnet" @("run", "--project", $testProject, "-c", "Release", "--no-restore")

    foreach ($project in @($agentProject, $trayProject, $updaterProject)) {
        Invoke-Checked "dotnet" (@("publish", $project, "-c", "Release", "-r", $Runtime, "--self-contained", "true", "-o", $publishDir, "--no-restore") + $msbuildVersionArgs)
    }

    Copy-ReleasePayload -PublishDir $publishDir -PackageDir $packageDir
    $packageIconDir = Join-Path $packageDir "assets\icons"
    New-Item -ItemType Directory -Force -Path $packageIconDir | Out-Null
    Copy-Item -Path $iconPath -Destination (Join-Path $packageIconDir "NightOwl.ico") -Force
    New-AgentVersionFile -Path (Join-Path $packageDir "agent.version.json") -BuildId $buildId -BuiltAt $builtAt -Commit $commit

    $zipPath = Join-Path $ReleaseDir "NightOwl.Agent.Windows.zip"
    Compress-ReleaseZip -PackageDir $packageDir -ZipPath $zipPath
    $zipSha = Get-FileSha256 $zipPath
    $zipSize = (Get-Item $zipPath).Length

    Copy-Item -Path $installScript -Destination (Join-Path $ReleaseDir "Install-NightOwlAgentDotNet.ps1") -Force
    Copy-Item -Path $uninstallScript -Destination (Join-Path $ReleaseDir "Uninstall-NightOwlAgentDotNet.ps1") -Force
    Copy-Item -Path $iconPath -Destination (Join-Path $ReleaseDir "NightOwl.ico") -Force

    $zipSha = Get-FileSha256 $zipPath
    $zipSize = (Get-Item $zipPath).Length
    $versionManifest = [ordered]@{
        product = "NightOwl Agent Windows"
        agent = "NightOwl.Agent.Windows"
        channel = "stable"
        version = $Version
        publishedAt = $builtAt
        published_at = $builtAt
        minimumSupportedVersion = "0.1.0"
        minimum_updater_version = $MinimumUpdaterVersion
        packageUrl = ("{0}/NightOwl.Agent.Windows.zip" -f $PublicBaseUrl.TrimEnd("/"))
        checksumUrl = ("{0}/checksums.json" -f $PublicBaseUrl.TrimEnd("/"))
        installerUrl = ("{0}/Install-NightOwlAgentDotNet.ps1" -f $PublicBaseUrl.TrimEnd("/"))
        notes = "Release $Version do NightOwl Agent Windows."
        requiresRestart = $true
        force = $false
        platform = "windows-x64"
        package = "NightOwl.Agent.Windows.zip"
        sha256 = $zipSha
        size = $zipSize
        build_id = $buildId
        git_commit = $commit
    }
    $versionManifest | ConvertTo-Json -Depth 8 | Set-Content -Path (Join-Path $ReleaseDir "version.json") -Encoding UTF8

    $releaseManifest = [ordered]@{
        product = "NightOwl Agent Windows"
        version = $Version
        build_id = $buildId
        built_at = $builtAt
        git_commit = $commit
        runtime = $Runtime
        public_base_url = $PublicBaseUrl
        package = [ordered]@{
            name = "NightOwl.Agent.Windows.zip"
            sha256 = $zipSha
            size = $zipSize
        }
        required_zip_entries = @(
            "NightOwl.Agent.Windows.exe",
            "NightOwl.Agent.Tray.exe",
            "NightOwl.Agent.Updater.exe",
            "NightOwl.Agent.Shared.dll",
            "assets/icons/NightOwl.ico",
            "agent.version.json"
        )
        forbidden_patterns = @("agent.config.json", "agent.identity.json", "agent.state.json", "agent-dotnet.state.json", "update-state.json", "*.preserved-*", "*.log", "*.tmp", "*.pdb", "*.ps1", "bin/", "obj/", "publish/", "downloads/", "artifacts/", "releases/")
    }
    $releaseManifest | ConvertTo-Json -Depth 8 | Set-Content -Path (Join-Path $ReleaseDir "release-manifest.json") -Encoding UTF8

    New-Checksums $ReleaseDir
    Validate-Release $ReleaseDir
    $releaseReady = $true

    if ($Publish) {
        if ([string]::IsNullOrWhiteSpace($PublishHost)) {
            Publish-LocalAtomic -Source $ReleaseDir -DestinationRoot $PublishPath
        }
        else {
            Publish-RemoteAtomic -Source $ReleaseDir -HostName $PublishHost -DestinationRoot $PublishPath -ExpectedZipSha $zipSha
        }
        Write-Step "Publicacao concluida."
    }

    Write-Step "Release pronta em: $ReleaseDir"
}
catch {
    if (-not $releaseReady -and (Test-Path $ReleaseDir)) {
        Remove-Item -Path $ReleaseDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    Write-Error $_
    throw
}
finally {
    if (Test-Path $workDir) {
        Remove-Item -Path $workDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}
