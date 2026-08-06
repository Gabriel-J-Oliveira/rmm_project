param(
    [long]$BundleVersion = 0,
    [string]$BundleDir = "",
    [string]$RemoteAlias = $env:NIGHTOWL_RELEASE_SSH_TARGET,
    [string]$RemoteProjectPath = $env:NIGHTOWL_RELEASE_REMOTE_ROOT,
    [string]$PublicBaseUrl = $env:NIGHTOWL_RELEASE_PUBLIC_BASE_URL,
    [string]$DjangoRoot = $env:NIGHTOWL_RELEASE_DJANGO_ROOT,
    [switch]$DryRun,
    [switch]$SkipBuild,
    [switch]$SkipImport,
    [switch]$Force,
    [switch]$SelfTest
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($RemoteAlias)) { $RemoteAlias = "nightowl-release" }
if ([string]::IsNullOrWhiteSpace($RemoteProjectPath)) { $RemoteProjectPath = "/opt/nightowl" }
if ([string]::IsNullOrWhiteSpace($DjangoRoot)) { $DjangoRoot = $RemoteProjectPath }
if ([string]::IsNullOrWhiteSpace($PublicBaseUrl)) { $PublicBaseUrl = "https://nightowl.controlsul.com.br/downloads/nightowl-agent" }

function Write-Step([string]$Message) { Write-Host "[nightowl-trust-publish] $Message" }
function Fail([string]$Code, [string]$Message) { throw "$Code`: $Message" }

function Invoke-SelfTest {
    $build = Join-Path $PSScriptRoot "Build-NightOwlReleaseTrustBundle.ps1"
    $test = Join-Path $PSScriptRoot "Test-NightOwlReleaseTrustBundle.ps1"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $build -SelfTest
    if ($LASTEXITCODE -ne 0) { Fail "TRUST_SELFTEST_FAILED" "Build self-test falhou." }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $test -SelfTest
    if ($LASTEXITCODE -ne 0) { Fail "TRUST_SELFTEST_FAILED" "Test self-test falhou." }
    Write-Step "SelfTest OK."
}

function Invoke-Native([string]$FileName, [string[]]$Arguments, [switch]$Sensitive) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $FileName
    foreach ($arg in $Arguments) { [void]$psi.ArgumentList.Add($arg) }
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $process = [System.Diagnostics.Process]::Start($psi)
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) {
        $shownArgs = if ($Sensitive) { "<redacted>" } else { ($Arguments -join " ") }
        Fail "TRUST_NATIVE_COMMAND_FAILED" "$FileName $shownArgs falhou com exit_code=$($process.ExitCode). STDOUT=$stdout STDERR=$stderr"
    }
    return $stdout
}

function Assert-BundleImmutableRemote([long]$Version, [string]$LocalBundleSha, [string]$LocalSigSha) {
    $remoteDir = "$RemoteProjectPath/downloads/agent/windows/trust/bundles/$Version"
    $cmd = @"
set -e
if [ -d '$remoteDir' ]; then
  cd '$remoteDir'
  b=`$(sha256sum release-public-keys.json | awk '{print `$1}')
  s=`$(sha256sum release-public-keys.sig | awk '{print `$1}')
  if [ "`$b" = "$LocalBundleSha" ] && [ "`$s" = "$LocalSigSha" ]; then echo identical; exit 0; fi
  echo divergent
  exit 42
fi
echo missing
"@
    $out = Invoke-Native "ssh.exe" @($RemoteAlias, $cmd)
    if ($out -match "divergent") { Fail "TRUST_BUNDLE_IMMUTABILITY_VIOLATION" "bundle_version $Version ja existe com conteudo diferente." }
    return ($out -match "identical")
}

if ($SelfTest) {
    Invoke-SelfTest
    exit 0
}
if ($BundleVersion -le 0) { Fail "TRUST_BUNDLE_VERSION_REQUIRED" "informe -BundleVersion." }
if ([string]::IsNullOrWhiteSpace($BundleDir)) {
    $BundleDir = Join-Path $RepoRoot ("artifacts\nightowl-agent\trust\bundles\{0}" -f $BundleVersion)
}
if (-not $SkipBuild) {
    $buildScript = Join-Path $PSScriptRoot "Build-NightOwlReleaseTrustBundle.ps1"
    $args = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $buildScript, "-BundleVersion", [string]$BundleVersion, "-OutputDir", $BundleDir, "-PublicBaseUrl", $PublicBaseUrl)
    if ($Force) { $args += "-Force" }
    & powershell.exe @args
    if ($LASTEXITCODE -ne 0) { Fail "TRUST_BUNDLE_BUILD_FAILED" "build do trust bundle falhou." }
}

$testScript = Join-Path $PSScriptRoot "Test-NightOwlReleaseTrustBundle.ps1"
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $testScript -BundleDir $BundleDir
if ($LASTEXITCODE -ne 0) { Fail "TRUST_BUNDLE_VALIDATION_FAILED" "validacao local falhou." }

$meta = Get-Content -Raw -Path (Join-Path $BundleDir "release-public-keys.meta.json") | ConvertFrom-Json
if ($DryRun) {
    Write-Step "DRY RUN: nenhum SSH/SCP/import Django sera executado."
    Write-Step "Bundle v$BundleVersion pronto localmente em $BundleDir"
    Write-Step "DRY RUN CONCLUIDO. Nenhum arquivo foi enviado e nenhum trust bundle foi alterado."
    exit 0
}

$sshOk = Invoke-Native "ssh.exe" @($RemoteAlias, "echo ok")
if ($sshOk.Trim() -ne "ok") { Fail "TRUST_SSH_FAILED" "SSH sem senha nao retornou ok." }

$localBundleSha = [string]$meta.bundle_sha256
$localSigSha = [string]$meta.signature_sha256
if (Assert-BundleImmutableRemote -Version $BundleVersion -LocalBundleSha $localBundleSha -LocalSigSha $localSigSha) {
    Write-Step "Bundle remoto identico ja existe; upload no-op."
}
else {
    $guid = [guid]::NewGuid().ToString("N")
    $remoteBase = "$RemoteProjectPath/downloads/agent/windows/trust"
    $remoteTmp = "$remoteBase/.upload-$BundleVersion-$guid"
    $remoteFinal = "$remoteBase/bundles/$BundleVersion"
    try {
        Invoke-Native "ssh.exe" @($RemoteAlias, "mkdir -p '$remoteTmp' '$remoteBase/bundles'")
        Invoke-Native "scp.exe" @((Join-Path $BundleDir "release-public-keys.json"), (Join-Path $BundleDir "release-public-keys.sig"), (Join-Path $BundleDir "release-public-keys.meta.json"), "$RemoteAlias`:$remoteTmp/")
        $validate = "cd '$remoteTmp' && test -s release-public-keys.json && test -s release-public-keys.sig && test -s release-public-keys.meta.json && [ `$(sha256sum release-public-keys.json | awk '{print `$1}') = '$localBundleSha' ] && [ `$(sha256sum release-public-keys.sig | awk '{print `$1}') = '$localSigSha' ] && python3 -m json.tool release-public-keys.json >/dev/null && python3 -m json.tool release-public-keys.meta.json >/dev/null && chmod 755 '$remoteTmp' && chmod 644 '$remoteTmp'/*"
        Invoke-Native "ssh.exe" @($RemoteAlias, $validate)
        Invoke-Native "ssh.exe" @($RemoteAlias, "mv '$remoteTmp' '$remoteFinal' && cp '$remoteFinal'/release-public-keys.* '$remoteBase'/")
    }
    catch {
        Invoke-Native "ssh.exe" @($RemoteAlias, "rm -rf '$remoteTmp'") | Out-Null
        throw
    }
}

if (-not $SkipImport) {
    $metadataUrl = "$($PublicBaseUrl.TrimEnd('/'))/trust/bundles/$BundleVersion/release-public-keys.meta.json"
    $djangoCmd = "cd '$DjangoRoot' && . .venv/bin/activate && python manage.py import_agent_trust_bundle --metadata-url '$metadataUrl' --status published"
    Invoke-Native "ssh.exe" @($RemoteAlias, $djangoCmd)
}

Write-Step "Trust bundle publicado."
Write-Step "Bundle version: $BundleVersion"
Write-Step "Bundle SHA-256: $localBundleSha"
Write-Step "Metadata URL: $($PublicBaseUrl.TrimEnd('/'))/trust/bundles/$BundleVersion/release-public-keys.meta.json"
