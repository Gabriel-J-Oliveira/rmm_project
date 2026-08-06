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

function ConvertTo-WindowsCommandLineArgument([string]$Value) {
    if ($null -eq $Value) { $Value = "" }
    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') {
        return $Value
    }
    $result = New-Object System.Text.StringBuilder
    [void]$result.Append('"')
    $backslashes = 0
    foreach ($char in $Value.ToCharArray()) {
        if ($char -eq '\') {
            $backslashes++
            continue
        }
        if ($char -eq '"') {
            [void]$result.Append(('\' * (($backslashes * 2) + 1)))
            [void]$result.Append('"')
            $backslashes = 0
            continue
        }
        if ($backslashes -gt 0) {
            [void]$result.Append(('\' * $backslashes))
            $backslashes = 0
        }
        [void]$result.Append($char)
    }
    if ($backslashes -gt 0) {
        [void]$result.Append(('\' * ($backslashes * 2)))
    }
    [void]$result.Append('"')
    return $result.ToString()
}

function ConvertTo-WindowsCommandLine([string[]]$Arguments) {
    return (@($Arguments | ForEach-Object { ConvertTo-WindowsCommandLineArgument ([string]$_) }) -join " ")
}

function Set-ProcessStartInfoArguments([System.Diagnostics.ProcessStartInfo]$ProcessStartInfo, [string[]]$Arguments, [switch]$ForceArgumentsFallback) {
    $hasArgumentList = $ProcessStartInfo.PSObject.Properties.Match("ArgumentList").Count -gt 0
    if ($hasArgumentList -and -not $ForceArgumentsFallback) {
        foreach ($arg in $Arguments) {
            [void]$ProcessStartInfo.ArgumentList.Add($arg)
        }
        return "ArgumentList"
    }
    $ProcessStartInfo.Arguments = ConvertTo-WindowsCommandLine $Arguments
    return "Arguments"
}

function Invoke-SelfTest {
    $build = Join-Path $PSScriptRoot "Build-NightOwlReleaseTrustBundle.ps1"
    $test = Join-Path $PSScriptRoot "Test-NightOwlReleaseTrustBundle.ps1"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $build -SelfTest
    if ($LASTEXITCODE -ne 0) { Fail "TRUST_SELFTEST_FAILED" "Build self-test falhou." }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $test -SelfTest
    if ($LASTEXITCODE -ne 0) { Fail "TRUST_SELFTEST_FAILED" "Test self-test falhou." }
    $quoted = ConvertTo-WindowsCommandLine @("simple", "with space", "C:\Path With Spaces\file.txt", 'quote "inside"', "", 'C:\path\ending\')
    if ($quoted -ne 'simple "with space" "C:\Path With Spaces\file.txt" "quote \"inside\"" "" C:\path\ending\') {
        Fail "TRUST_SELFTEST_FAILED" "Quoting Windows inesperado: $quoted"
    }
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $mode = Set-ProcessStartInfoArguments -ProcessStartInfo $psi -Arguments @("a b", 'c"d', "", 'C:\x\') -ForceArgumentsFallback
    if ($mode -ne "Arguments" -or [string]::IsNullOrWhiteSpace($psi.Arguments)) {
        Fail "TRUST_SELFTEST_FAILED" "Fallback Arguments nao foi exercitado."
    }
    $probeScript = Join-Path ([System.IO.Path]::GetTempPath()) ("nightowl-native-args-{0}.ps1" -f ([guid]::NewGuid().ToString("N")))
    try {
        $encoding = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($probeScript, 'foreach ($arg in $args) { "ARG=[$arg]" }', $encoding)
        $argumentProbe = @(
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", $probeScript,
            "simple",
            "with space",
            "C:\Path With Spaces\file.txt",
            'quote "inside"',
            "",
            'C:\path\ending\'
        )
        $output = Invoke-Native "powershell.exe" $argumentProbe -ForceArgumentsFallback
        foreach ($expected in @("ARG=[simple]", "ARG=[with space]", "ARG=[C:\Path With Spaces\file.txt]", 'ARG=[quote "inside"]', "ARG=[]", 'ARG=[C:\path\ending\')) {
            if ($output -notmatch [regex]::Escape($expected)) {
                Fail "TRUST_SELFTEST_FAILED" "Argumento nao preservado no fallback: $expected. Saida=$output"
            }
        }
    }
    finally {
        Remove-Item -LiteralPath $probeScript -Force -ErrorAction SilentlyContinue
    }
    try {
        Invoke-Native "powershell.exe" @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "Write-Error 'expected failure'; exit 7") -ForceArgumentsFallback | Out-Null
        Fail "TRUST_SELFTEST_FAILED" "Comando com exit code diferente de zero deveria falhar."
    }
    catch {
        if ($_.Exception.Message -notmatch "exit_code=7") {
            throw
        }
    }
    Write-Step "SelfTest OK."
}

function Invoke-Native([string]$FileName, [string[]]$Arguments, [switch]$Sensitive, [int]$TimeoutSeconds = 0, [switch]$ForceArgumentsFallback) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $FileName
    $argumentMode = Set-ProcessStartInfoArguments -ProcessStartInfo $psi -Arguments $Arguments -ForceArgumentsFallback:$ForceArgumentsFallback
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $process = [System.Diagnostics.Process]::Start($psi)
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    if ($TimeoutSeconds -gt 0) {
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            try { $process.Kill() } catch {}
            $shownArgs = if ($Sensitive) { "<redacted>" } else { ConvertTo-WindowsCommandLine $Arguments }
            Fail "TRUST_NATIVE_COMMAND_TIMEOUT" "$FileName $shownArgs excedeu timeout de $TimeoutSeconds segundos."
        }
    }
    else {
        $process.WaitForExit()
    }
    $stdout = $stdoutTask.Result
    $stderr = $stderrTask.Result
    if ($process.ExitCode -ne 0) {
        $shownArgs = if ($Sensitive) { "<redacted>" } else { ConvertTo-WindowsCommandLine $Arguments }
        Fail "TRUST_NATIVE_COMMAND_FAILED" "$FileName $shownArgs falhou com exit_code=$($process.ExitCode), argument_mode=$argumentMode. STDOUT=$stdout STDERR=$stderr"
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
    $bundle = Get-Content -Raw -Path (Join-Path $BundleDir "release-public-keys.json") | ConvertFrom-Json
    $activeKeyIds = @($bundle.keys | Where-Object { [string]$_.status -eq "active" -or [string]::IsNullOrWhiteSpace($_.status) } | ForEach-Object { [string]$_.key_id })
    $revokedKeyIds = @($bundle.keys | Where-Object { [string]$_.status -eq "revoked" } | ForEach-Object { [string]$_.key_id })
    Write-Step "DRY RUN: nenhum SSH/SCP/import Django sera executado."
    Write-Step "Bundle v$BundleVersion pronto localmente em $BundleDir"
    Write-Step "Root key ID: $($meta.root_key_id)"
    Write-Step "Bundle SHA-256: $($meta.bundle_sha256)"
    Write-Step "Signature SHA-256: $($meta.signature_sha256)"
    Write-Step "Tamanho: $($meta.size) bytes"
    Write-Step "Active key IDs: $($activeKeyIds -join ', ')"
    Write-Step "Revoked key IDs: $($revokedKeyIds -join ', ')"
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
