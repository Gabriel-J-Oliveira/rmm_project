$ErrorActionPreference = 'Stop'
$sourcePath = Join-Path $PSScriptRoot '..\agents\bootstrap\nightowl_deployment_bootstrap.ps1'
$source = Get-Content -LiteralPath $sourcePath -Raw
$root = Join-Path ([IO.Path]::GetTempPath()) ('nightowl-boot02-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root | Out-Null
$previousTemp = $env:TEMP; $env:TEMP = $root
$env:NIGHTOWL_DEPLOYMENT_CONFIRM_TIMEOUT_SECONDS = '0'
$env:NIGHTOWL_INSTALLER_SILENT = 'prior'
$syntheticToken = 'deploy_' + 'synthetic_credential_123456789'
$mock = @'
function Assert-Windows {}
function Assert-Administrator {}
function Assert-Https { param($Url) }
function Invoke-JsonGet { param($Url,$DeploymentToken) return [pscustomobject]@{ deployment_id='fixture'; manual_validation_required=$script:fixtureManual; server_url='https://example.invalid'; completion_url='https://example.invalid/complete'; trusted_public_keys=[pscustomobject]@{url='https://example.invalid/trust';sha256=('a'*64)}; release=[pscustomobject]@{version='1';channel='stable';id='fixture';sha256=('b'*64);installer_url='https://example.invalid/install';package_url='https://example.invalid/package'} } }
function Read-Host { param($Prompt) return 'manual_SYNTHETIC' }
function Get-ExistingInstallationState { param($Root,$Metadata) return $script:fixtureExisting }
function Get-VerifiedTrustBundle { param($Url,$Path,$ExpectedSha) }
function Invoke-WebRequest { param($Uri,$OutFile,[switch]$UseBasicParsing) [IO.File]::WriteAllText($OutFile,'fixture') }
function Read-AgentConfig { return [pscustomobject]@{machineId='fixture';agentToken='synthetic'} }
function Get-Service { param($Name,$ErrorAction) return [pscustomobject]@{Status='Running'} }
function Send-DeploymentCompletion { param($Metadata,$Status,$ErrorCode,$ErrorMessage) if ($script:fixturePending) { return [pscustomobject]@{ok=($Status -eq 'pending');error='awaiting_heartbeat'} }; return [pscustomobject]@{ok=$true;error=''} }
function powershell.exe { $script:childCalls++; if ($script:fixtureManual -and $env:NIGHTOWL_INSTALLER_MANUAL_VALIDATION_TOKEN -ne 'manual_SYNTHETIC') { throw 'manual token missing' }; $global:LASTEXITCODE=$script:childExit; Write-Output 'noisy installer stdout'; Write-Warning 'noisy installer warning'; Write-Host 'noisy installer host output' }
'@
$pattern = '(?m)^try \{\r?\n    \$Stage = "preflight"'
$injected = [regex]::Replace($source, $pattern, ($mock + "`r`ntry {`r`n    `$Stage = `"preflight`""), 1)
if ($injected -eq $source) { throw 'Could not inject fixtures' }
$block = [scriptblock]::Create($injected)
try {
    foreach ($case in @(
        @{existing='healthy_compatible';pending=$false;exit=0;result=0;calls=0},
        @{existing='healthy_compatible';pending=$true;exit=0;result=2;calls=0},
        @{existing='absent';pending=$false;exit=0;result=0;calls=1},
        @{existing='absent';pending=$false;exit=4;result=1;calls=1},
        @{existing='healthy_compatible';pending=$false;exit=0;result=0;calls=1;manual=$true}
    )) {
        $script:fixtureExisting=$case.existing; $script:fixturePending=$case.pending; $script:childExit=$case.exit; $script:childCalls=0; $script:fixtureManual=($case.manual -eq $true)
        $env:NIGHTOWL_DEPLOYMENT_TOKEN=$syntheticToken
        $global:NightOwlDeploymentBootstrapExitCode=-1
        $captured = @(& $block *>&1)
        if ($global:NightOwlDeploymentBootstrapExitCode -ne $case.result -or $script:childCalls -ne $case.calls) { throw "Incorrect result $($case.existing)/$($case.pending): code=$global:NightOwlDeploymentBootstrapExitCode calls=$script:childCalls output=$captured" }
        if ($case.result -ne 1 -and $captured.Count -ne 0) { throw 'Success or pending was noisy' }
        if ($case.result -eq 1 -and ($captured.Count -ne 1 -or [string]$captured[0] -notmatch 'Log:')) { throw "Failure output unexpected: count=$($captured.Count), output=$captured" }
        if ($env:NIGHTOWL_INSTALLER_SILENT -ne 'prior') { throw 'Caller environment was not restored' }
        if (Test-Path Env:\NIGHTOWL_DEPLOYMENT_TOKEN) { throw 'Deployment token not removed' }
        if (Test-Path Env:\NIGHTOWL_INSTALLER_MANUAL_VALIDATION_TOKEN) { throw 'Manual token not removed' }
    }
    $log = Get-Content -Raw -LiteralPath (Join-Path $root 'NightOwlDeployment\bootstrap.log')
    if ($log.Contains($syntheticToken) -or $log -match 'manual_SYNTHETIC|noisy installer') { throw 'Sensitive or raw child output leaked to log' }
    $ast = [Management.Automation.Language.Parser]::ParseFile((Resolve-Path $sourcePath).Path, [ref]$null, [ref]$null)
    foreach ($name in @('Protect-NightOwlLogValue','Write-NightOwlBootstrapLog')) {
        $definition = $ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name}, $true)
        Invoke-Expression $definition.Extent.Text
    }
    $TempRoot=Join-Path $root 'NightOwlDeployment'; $BootstrapLogPath=Join-Path $TempRoot 'bootstrap.log'; $Stage='fixture'
    [IO.File]::WriteAllText($BootstrapLogPath, ('x' * 1048576))
    Write-NightOwlBootstrapLog 'failed' @{ detail=$syntheticToken; code='TEST_FAILURE' }
    if (-not (Test-Path -LiteralPath ($BootstrapLogPath + '.1'))) { throw 'Log rotation missing' }
    $rotatedLog=Get-Content -Raw -LiteralPath $BootstrapLogPath
    if ($rotatedLog.Contains($syntheticToken) -or $rotatedLog -notmatch 'TEST_FAILURE') { throw 'Log redaction or useful failure code missing' }
    $beforePreference=$ErrorActionPreference
    try {
        $ErrorActionPreference='Continue'
        $nativePowerShell = Join-Path $PSHOME 'powershell.exe'
        $encoded=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes('[Console]::Error.WriteLine("synthetic"); exit 4'))
        & $nativePowerShell -NoProfile -NonInteractive -EncodedCommand $encoded *> $null
        if ($LASTEXITCODE -ne 4) { throw "Native stderr or exit code handling failed: $LASTEXITCODE" }
    } finally { $ErrorActionPreference=$beforePreference }
    Write-Host 'PASS: silent healthy/pending/install/error fixtures, child output suppression and credentials.'
} finally {
    $env:TEMP=$previousTemp
    Remove-Item Env:\NIGHTOWL_DEPLOYMENT_CONFIRM_TIMEOUT_SECONDS -ErrorAction SilentlyContinue
    Remove-Item Env:\NIGHTOWL_INSTALLER_SILENT -ErrorAction SilentlyContinue
    $resolvedRoot = (Resolve-Path -LiteralPath $root).Path
    $resolvedTemp = (Resolve-Path -LiteralPath ([IO.Path]::GetTempPath())).Path
    if ((Split-Path -Parent $resolvedRoot) -eq $resolvedTemp -and (Split-Path -Leaf $resolvedRoot) -like 'nightowl-boot02-*') { Remove-Item -LiteralPath $resolvedRoot -Recurse -Force }
}
