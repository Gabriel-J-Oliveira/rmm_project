param(
    [Parameter(Mandatory = $true)]
    [string]$Version,

    [string]$RemoteAlias = "nightowl-release",
    [string]$RemoteProjectPath = "/opt/nightowl",
    [string]$PublicBaseUrl = "https://nightowl.controlsul.com.br/downloads/nightowl-agent",

    [ValidateSet("development", "pilot", "stable")]
    [string]$Channel = "development",

    [ValidateRange(0, 100)]
    [int]$Rollout = 0,

    [bool]$Paused = $true,

    [switch]$SkipBuild,
    [switch]$SkipUpload,
    [switch]$SkipImport,
    [switch]$Force,
    [switch]$KeepLocalWork,
    [switch]$PruneOldReleases,
    [ValidateRange(1, 50)]
    [int]$KeepLastReleases = 5,
    [switch]$DryRun,
    [switch]$SelfTest
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$script:ReleaseRoot = Join-Path $script:RepoRoot "artifacts\nightowl-agent\releases"
$script:BuildScript = Join-Path $script:RepoRoot "scripts\Build-NightOwlAgentRelease.ps1"
$script:RequiredFiles = @(
    "NightOwl.Agent.Windows.zip",
    "Install-NightOwlAgentDotNet.ps1",
    "Uninstall-NightOwlAgentDotNet.ps1",
    "NightOwl.ico",
    "checksums.json",
    "version.json",
    "release-manifest.json"
)

$script:ExitCodes = @{
    validation_failed = 2
    build_failed = 10
    ssh_failed = 20
    upload_failed = 30
    checksum_failed = 40
    http_validation_failed = 50
    import_failed = 60
}

function Write-Step([string]$Message) {
    Write-Host ("[nightowl-release-publish] {0}" -f $Message)
}

function Fail([string]$Code, [string]$Message) {
    $exitCode = if ($script:ExitCodes.ContainsKey($Code)) { $script:ExitCodes[$Code] } else { 1 }
    $ex = New-Object System.Exception($Message)
    $ex.Data["ExitCode"] = $exitCode
    $ex.Data["Code"] = $Code
    throw $ex
}

function Assert-Version([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        Fail "validation_failed" "Version nao pode ser vazia."
    }
    if ($Value -notmatch '^\d+\.\d+\.\d+(\.\d+)?(-[0-9A-Za-z][0-9A-Za-z.-]*)?$') {
        Fail "validation_failed" "Version invalida: $Value. Use major.minor.patch[.build][-prerelease]."
    }
}

function Assert-SafeRemoteSegment([string]$Value) {
    if ($Value -notmatch '^[0-9A-Za-z][0-9A-Za-z._-]*$') {
        Fail "validation_failed" "Segmento de path remoto inseguro: $Value"
    }
}

function ConvertTo-BashSingleQuoted([string]$Value) {
    return "'" + $Value.Replace("'", "'\''") + "'"
}

function Invoke-Native([string]$FileName, [string[]]$Arguments, [string]$FailureCode) {
    Write-Step ("Executando: {0} {1}" -f $FileName, ($Arguments -join " "))
    if ($DryRun) {
        return @()
    }
    $stdoutFile = [System.IO.Path]::GetTempFileName()
    $stderrFile = [System.IO.Path]::GetTempFileName()
    try {
        & $FileName @Arguments > $stdoutFile 2> $stderrFile
        $nativeExitCode = $LASTEXITCODE
        $stdout = Get-Content -Path $stdoutFile -Raw -ErrorAction SilentlyContinue
        $stderr = Get-Content -Path $stderrFile -Raw -ErrorAction SilentlyContinue
        if ($nativeExitCode -ne 0) {
            $message = @(
                "Comando executado: $FileName $($Arguments -join ' ')",
                "Exit code: $nativeExitCode",
                "Pipeline error code: $FailureCode",
                "STDOUT:",
                ($stdout -replace '\s+$', ''),
                "STDERR:",
                ($stderr -replace '\s+$', '')
            ) -join "`n"
            Fail $FailureCode $message
        }
        return @($stdout -split "`r?`n" | Where-Object { $_ -ne "" })
    }
    finally {
        Remove-Item -LiteralPath $stdoutFile, $stderrFile -Force -ErrorAction SilentlyContinue
    }
}

function New-BuildReleaseArguments([string]$RequestedVersion, [string]$RequestedChannel, [string]$RequestedPublicBaseUrl, [bool]$AllowForce) {
    $arguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $script:BuildScript,
        "-Version", $RequestedVersion,
        "-Channel", $RequestedChannel,
        "-PublicBaseUrl", $RequestedPublicBaseUrl
    )
    if ($AllowForce) {
        $arguments += "-Force"
    }
    return ,$arguments
}

function Assert-ArrayDoesNotContainFalseSwitch([string[]]$Arguments) {
    foreach ($argument in $Arguments) {
        if ($argument -match '^-[-A-Za-z0-9]+:(False|false|\$false)$') {
            Fail "validation_failed" "Switch falso serializado incorretamente: $argument"
        }
    }
}

function Invoke-SelfTest {
    $oldBuildScript = $script:BuildScript
    try {
        $script:BuildScript = "C:\Path With Spaces\Build-NightOwlAgentRelease.ps1"
        $withoutForce = New-BuildReleaseArguments -RequestedVersion "0.1.1.0-rc4" -RequestedChannel "development" -RequestedPublicBaseUrl "https://nightowl.controlsul.com.br/downloads/nightowl-agent?x=1&y=2" -AllowForce $false
        Assert-ArrayDoesNotContainFalseSwitch $withoutForce
        if ($withoutForce -contains "-Force") {
            Fail "validation_failed" "SelfTest falhou: Force false incluiu -Force."
        }
        if (@($withoutForce | Where-Object { $_ -eq "-Force" }).Count -ne 0) {
            Fail "validation_failed" "SelfTest falhou: Force false duplicou -Force."
        }
        if ($withoutForce[4] -ne "C:\Path With Spaces\Build-NightOwlAgentRelease.ps1") {
            Fail "validation_failed" "SelfTest falhou: path com espacos nao foi preservado."
        }

        $withForce = New-BuildReleaseArguments -RequestedVersion "0.1.1.0-rc4" -RequestedChannel "development" -RequestedPublicBaseUrl "https://nightowl.controlsul.com.br/downloads/nightowl-agent" -AllowForce $true
        Assert-ArrayDoesNotContainFalseSwitch $withForce
        if (@($withForce | Where-Object { $_ -eq "-Force" }).Count -ne 1) {
            Fail "validation_failed" "SelfTest falhou: Force true deve incluir -Force exatamente uma vez."
        }
        if ($withForce -contains "-Force:False" -or $withForce -contains "-Force:$false") {
            Fail "validation_failed" "SelfTest falhou: Force true/false gerou sintaxe :False."
        }
        Write-Step "SelfTest OK: argumentos do build montados sem switches falsos."
        Write-Step ("Force false: powershell.exe {0}" -f ($withoutForce -join " "))
        Write-Step ("Force true:  powershell.exe {0}" -f ($withForce -join " "))
    }
    finally {
        $script:BuildScript = $oldBuildScript
    }
}

function Invoke-Ssh([string]$Command, [string]$FailureCode = "ssh_failed") {
    $quoted = ConvertTo-BashSingleQuoted $Command
    return Invoke-Native "ssh.exe" @($RemoteAlias, "bash", "-lc", $quoted) $FailureCode
}

function Get-FileSha256([string]$Path) {
    return (Get-FileHash -Algorithm SHA256 -Path $Path).Hash.ToLowerInvariant()
}

function Read-JsonFile([string]$Path) {
    try {
        return Get-Content -Path $Path -Raw | ConvertFrom-Json
    }
    catch {
        Fail "validation_failed" "JSON invalido em $Path`: $($_.Exception.Message)"
    }
}

function Get-ChecksumEntry($Checksums, [string]$Name) {
    return @($Checksums.files | Where-Object { $_.name -eq $Name }) | Select-Object -First 1
}

function Assert-LocalRelease([string]$ReleaseDir) {
    if (-not (Test-Path $ReleaseDir)) {
        Fail "validation_failed" "Release local nao encontrada: $ReleaseDir"
    }
    foreach ($file in $script:RequiredFiles) {
        $path = Join-Path $ReleaseDir $file
        if (-not (Test-Path $path)) {
            Fail "validation_failed" "Artefato obrigatorio ausente: $path"
        }
    }

    $versionJson = Read-JsonFile (Join-Path $ReleaseDir "version.json")
    $checksumsJson = Read-JsonFile (Join-Path $ReleaseDir "checksums.json")
    $null = Read-JsonFile (Join-Path $ReleaseDir "release-manifest.json")

    if ([string]$versionJson.version -ne $Version) {
        Fail "validation_failed" "version.json declara $($versionJson.version), esperado $Version."
    }
    $zipPath = Join-Path $ReleaseDir "NightOwl.Agent.Windows.zip"
    $zipSha = Get-FileSha256 $zipPath
    $zipSize = (Get-Item $zipPath).Length
    if ([string]$versionJson.sha256 -ne $zipSha) {
        Fail "checksum_failed" "SHA256 local do ZIP diverge de version.json."
    }
    if ([long]$versionJson.size -ne $zipSize) {
        Fail "checksum_failed" "Tamanho local do ZIP diverge de version.json."
    }
    $zipEntry = Get-ChecksumEntry $checksumsJson "NightOwl.Agent.Windows.zip"
    if ($null -eq $zipEntry -or [string]$zipEntry.sha256 -ne $zipSha) {
        Fail "checksum_failed" "checksums.json sem SHA256 correto do ZIP."
    }
    return [ordered]@{
        VersionJson = $versionJson
        ChecksumsJson = $checksumsJson
        ZipSha = $zipSha
        ZipSize = $zipSize
    }
}

function Test-SshNoPassword {
    if ($DryRun) {
        Write-Step "DryRun: pular teste SSH real para $RemoteAlias"
        return
    }
    $result = Invoke-Native "ssh.exe" @("-o", "BatchMode=yes", $RemoteAlias, "echo ok") "ssh_failed"
    if (($result | Out-String).Trim() -ne "ok") {
        Fail "ssh_failed" "SSH sem senha nao retornou ok para $RemoteAlias."
    }
}

function Copy-ReleaseToRemote([string]$ReleaseDir, [string]$RemoteTemp) {
    $files = @()
    foreach ($file in $script:RequiredFiles) {
        $files += (Join-Path $ReleaseDir $file)
    }
    $target = "${RemoteAlias}:$RemoteTemp/"
    Invoke-Native "scp.exe" (@("-q") + $files + @($target)) "upload_failed" | Out-Null
}

function Assert-RemoteRelease([string]$RemoteDir, [string]$ExpectedSha, [long]$ExpectedSize) {
    $required = ($script:RequiredFiles | ForEach-Object { ConvertTo-BashSingleQuoted $_ }) -join " "
    $command = @"
set -euo pipefail
cd $(ConvertTo-BashSingleQuoted $RemoteDir)
for f in $required; do test -s "`$f"; done
python3 -m json.tool version.json >/dev/null
python3 -m json.tool checksums.json >/dev/null
python3 -m json.tool release-manifest.json >/dev/null
actual=`$(sha256sum NightOwl.Agent.Windows.zip | awk '{print `$1}')
test "x`$actual" = "x$ExpectedSha"
size=`$(stat -c%s NightOwl.Agent.Windows.zip)
test "x`$size" = "x$ExpectedSize"
"@
    Invoke-Ssh $command "checksum_failed" | Out-Null
}

function Publish-RemoteAtomic([string]$RemoteTemp, [string]$RemoteTarget, [bool]$AllowReplace) {
    $backup = "$RemoteTarget.backup-$([guid]::NewGuid().ToString("N"))"
    $allowReplaceValue = if ($AllowReplace) { "1" } else { "0" }
    $command = @"
set -euo pipefail
target=$(ConvertTo-BashSingleQuoted $RemoteTarget)
tmp=$(ConvertTo-BashSingleQuoted $RemoteTemp)
backup=$(ConvertTo-BashSingleQuoted $backup)
allow_replace="$allowReplaceValue"
if [ -e "`$target" ] && [ "`$allow_replace" != "1" ]; then
  echo "release_exists"
  exit 17
fi
if [ -e "`$target" ]; then
  rm -rf "`$backup"
  mv "`$target" "`$backup"
fi
if mv "`$tmp" "`$target"; then
  find "`$target" -type d -exec chmod 755 {} \;
  find "`$target" -type f -exec chmod 644 {} \;
  chown -R www-data:www-data "`$target" 2>/dev/null || true
  rm -rf "`$backup"
else
  if [ -e "`$backup" ]; then mv "`$backup" "`$target"; fi
  exit 18
fi
"@
    Invoke-Ssh $command "upload_failed" | Out-Null
}

function Test-PublicUrls($LocalRelease) {
    $releaseBase = "{0}/releases/{1}" -f $PublicBaseUrl.TrimEnd("/"), $Version
    foreach ($url in @(
        "$releaseBase/release-manifest.json",
        "$releaseBase/NightOwl.Agent.Windows.zip"
    )) {
        Write-Step "Validando URL publica: $url"
        if ($DryRun) { continue }
        try {
            $response = Invoke-WebRequest -Uri $url -Method Head -UseBasicParsing -TimeoutSec 30
            if ([int]$response.StatusCode -ne 200) {
                Fail "http_validation_failed" "URL nao retornou 200: $url ($($response.StatusCode))"
            }
        }
        catch {
            Fail "http_validation_failed" "Falha ao validar URL $url`: $($_.Exception.Message)"
        }
    }

    if (-not $DryRun) {
        $versionUrl = "$releaseBase/version.json"
        $publicVersion = Invoke-WebRequest -Uri $versionUrl -UseBasicParsing -TimeoutSec 30
        if ([int]$publicVersion.StatusCode -ne 200) {
            Fail "http_validation_failed" "version.json publico nao retornou 200."
        }
        $manifest = $publicVersion.Content | ConvertFrom-Json
        if ([string]$manifest.version -ne $Version) {
            Fail "http_validation_failed" "version.json publico declarou $($manifest.version), esperado $Version."
        }
        if ([string]$manifest.sha256 -ne [string]$LocalRelease.ZipSha) {
            Fail "http_validation_failed" "version.json publico com SHA256 divergente."
        }
        if ([string]$manifest.packageUrl -ne "$releaseBase/NightOwl.Agent.Windows.zip") {
            Fail "http_validation_failed" "version.json publico com packageUrl inesperada: $($manifest.packageUrl)"
        }
    }
}

function Import-ReleaseInDjango($LocalRelease) {
    $versionJsonUrl = "{0}/releases/{1}/version.json" -f $PublicBaseUrl.TrimEnd("/"), $Version
    $forceFlag = if ($Force) { " --force" } else { "" }
    $pyPaused = if ($Paused) { "True" } else { "False" }
    $command = @"
set -euo pipefail
cd $(ConvertTo-BashSingleQuoted $RemoteProjectPath)
source .venv/bin/activate
python manage.py import_agent_release --agent-version $(ConvertTo-BashSingleQuoted $Version) --channel $(ConvertTo-BashSingleQuoted $Channel) --version-json $(ConvertTo-BashSingleQuoted $versionJsonUrl)$forceFlag
python manage.py shell -c $(ConvertTo-BashSingleQuoted "from agents.models import AgentRelease; r=AgentRelease.objects.get(version='$Version'); assert r.channel == '$Channel'; assert r.rollout_percentage == $Rollout; assert r.rollout_paused == $pyPaused; assert r.package_url == '$($LocalRelease.VersionJson.packageUrl)'; assert r.sha256 == '$($LocalRelease.ZipSha)'; print('release_import_verified')")
"@
    Invoke-Ssh $command "import_failed" | Out-Null
}

function Invoke-PruneOldReleases {
    $releaseRootRemote = "$RemoteProjectPath/downloads/agent/windows/releases"
    $keep = [Math]::Max(1, $KeepLastReleases)
    $dry = if ($DryRun) { "1" } else { "0" }
    $command = @"
set -euo pipefail
cd $(ConvertTo-BashSingleQuoted $RemoteProjectPath)
source .venv/bin/activate
python - <<'PY'
import json
from pathlib import Path
from agents.models import AgentMachine, AgentJob, AgentRelease
release_root = Path("$releaseRootRemote")
current = "$Version"
keep = int("$keep")
protected = {current}
protected.update(v for v in AgentMachine.objects.exclude(agent_version='').values_list('agent_version', flat=True))
protected.update(v for v in AgentMachine.objects.exclude(pinned_agent_version='').values_list('pinned_agent_version', flat=True))
protected.update(AgentRelease.objects.filter(channel='stable', status__in=['available', 'active']).values_list('version', flat=True))
for job in AgentJob.objects.filter(status__in=['queued','pending','dispatched','running'], job_type='update_agent'):
    payload = job.payload or {}
    if payload.get('target_version'):
        protected.add(str(payload['target_version']))
    if job.agent_release_id:
        protected.add(job.agent_release.version)
dirs = [p.name for p in release_root.iterdir() if p.is_dir() and not p.name.startswith('.')]
known = {r.version: r for r in AgentRelease.objects.filter(version__in=dirs)}
ordered = sorted(dirs, key=lambda v: (known.get(v).released_at or known.get(v).created_at if known.get(v) else None, v), reverse=True)
keep_set = set(ordered[:keep])
delete = [v for v in ordered if v not in protected and v not in keep_set]
print(json.dumps({'protected': sorted(protected), 'kept': sorted(keep_set), 'delete': delete}))
PY
"@
    $planText = (Invoke-Ssh $command "import_failed" | Out-String).Trim()
    Write-Step "Plano de limpeza: $planText"
    if ($DryRun) {
        return
    }
    $plan = $planText | ConvertFrom-Json
    foreach ($versionToDelete in @($plan.delete)) {
        Assert-SafeRemoteSegment $versionToDelete
        Invoke-Ssh "rm -rf $(ConvertTo-BashSingleQuoted "$releaseRootRemote/$versionToDelete")" "upload_failed" | Out-Null
        Write-Step "Release antiga removida: $versionToDelete"
    }
}

try {
    if ($SelfTest) {
        Invoke-SelfTest
        exit 0
    }

    Assert-Version $Version
    Assert-SafeRemoteSegment $Version
    if (-not (Test-Path $script:BuildScript)) {
        Fail "validation_failed" "Build script nao encontrado: $script:BuildScript"
    }
    if ($Rollout -ne 0 -or -not $Paused) {
        Fail "validation_failed" "O comando import_agent_release atual importa sempre pausado e rollout 0. Use Rollout=0 e Paused=true."
    }

    Write-Step "Validando SSH sem senha para $RemoteAlias"
    Test-SshNoPassword

    if (-not $SkipBuild) {
        $buildArguments = New-BuildReleaseArguments -RequestedVersion $Version -RequestedChannel $Channel -RequestedPublicBaseUrl $PublicBaseUrl -AllowForce ([bool]$Force)
        Assert-ArrayDoesNotContainFalseSwitch $buildArguments
        Invoke-Native "powershell.exe" $buildArguments "build_failed" | Out-Null
    }
    else {
        Write-Step "SkipBuild ativo; usando artefatos locais existentes."
    }

    $releaseDir = Join-Path $script:ReleaseRoot $Version
    $localRelease = Assert-LocalRelease $releaseDir
    $releaseRootRemote = "$RemoteProjectPath/downloads/agent/windows/releases"
    $uploadId = [guid]::NewGuid().ToString("N")
    $remoteTemp = "$releaseRootRemote/.upload-$Version-$uploadId"
    $remoteTarget = "$releaseRootRemote/$Version"

    if (-not $SkipUpload) {
        Invoke-Ssh "mkdir -p $(ConvertTo-BashSingleQuoted $releaseRootRemote); rm -rf $(ConvertTo-BashSingleQuoted $remoteTemp); mkdir -p $(ConvertTo-BashSingleQuoted $remoteTemp)" "upload_failed" | Out-Null
        try {
            Copy-ReleaseToRemote -ReleaseDir $releaseDir -RemoteTemp $remoteTemp
            Assert-RemoteRelease -RemoteDir $remoteTemp -ExpectedSha $localRelease.ZipSha -ExpectedSize $localRelease.ZipSize
            Publish-RemoteAtomic -RemoteTemp $remoteTemp -RemoteTarget $remoteTarget -AllowReplace ([bool]$Force)
            Assert-RemoteRelease -RemoteDir $remoteTarget -ExpectedSha $localRelease.ZipSha -ExpectedSize $localRelease.ZipSize
        }
        catch {
            try { Invoke-Ssh "rm -rf $(ConvertTo-BashSingleQuoted $remoteTemp)" "upload_failed" | Out-Null } catch {}
            throw
        }
    }
    else {
        Write-Step "SkipUpload ativo; pulando envio e publicacao remota."
    }

    if (-not $SkipUpload) {
        Test-PublicUrls $localRelease
    }

    if (-not $SkipImport) {
        Import-ReleaseInDjango $localRelease
    }
    else {
        Write-Step "SkipImport ativo; release nao importada no Django."
    }

    if ($PruneOldReleases) {
        Invoke-PruneOldReleases
    }

    if (-not $KeepLocalWork) {
        Write-Step "Artefatos locais preservados em $releaseDir"
    }

    Write-Host ""
    Write-Host "Release publicada com sucesso:"
    Write-Host "  Versao:      $Version"
    Write-Host "  Canal:       $Channel"
    Write-Host "  Rollout:     0%"
    Write-Host "  Pausada:     true"
    Write-Host "  SHA256 ZIP:  $($localRelease.ZipSha)"
    Write-Host "  URL:         $($PublicBaseUrl.TrimEnd('/'))/releases/$Version/version.json"
    exit 0
}
catch {
    $exit = 1
    $code = "unexpected"
    if ($_.Exception.Data.Contains("ExitCode")) {
        $exit = [int]$_.Exception.Data["ExitCode"]
    }
    if ($_.Exception.Data.Contains("Code")) {
        $code = [string]$_.Exception.Data["Code"]
    }
    Write-Error ("Falha [{0}]: {1}" -f $code, $_.Exception.Message)
    exit $exit
}
