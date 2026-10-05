param([string]$InstallerPath = (Join-Path $PSScriptRoot 'Install-NightOwlAgentDotNet.ps1'))
$ErrorActionPreference = 'Stop'
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path $InstallerPath), [ref]$null, [ref]$errors)
if ($errors.Count) { throw 'Installer AST invalid' }
$names = @($ast.ParamBlock.Parameters | ForEach-Object { $_.Name.VariablePath.UserPath })
foreach ($name in @('ServerUrl','EnrollmentToken','ManualValidationToken','AgentToken','PackageUrl','InstallPath',
    'ServiceName','DisplayName','InstallAsService','Force','StartService','RunCheck','KeepPowerShellAgent',
    'DisablePowerShellAgent','AllowInsecureTls','NoGui','NoTray','StartTray','DebugLog','TrustedPublicKeysPath',
    'ExpectedVersion','ExpectedChannel','ExpectedPackageSha256','ExpectedReleaseId','ExpectedGitCommit',
    'Install','Repair','Reinstall','ForceRecovery','TrustLocalPackage','AllowReleaseBundledTrustForLab','NonInteractive')) {
    if ($names -notcontains $name) { throw "Missing installer parameter: $name" }
}
# Extract only the enrollment function. Never execute installer top-level code.
$fn = $ast.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $n.Name -eq 'Invoke-NightOwlEnrollment' }, $true)
Invoke-Expression $fn.Extent.Text
function Get-ComputerInfoLite { @{ Hostname='synthetic'; Domain='example.test' } }
function Write-InstallLog { param($EventType, $Message, $Metadata) }
function Read-Host { throw 'PROMPT_REACHED' }
function Show-ManualValidationDialog { throw 'GUI_REACHED' }
function Invoke-EnrollmentRequest {
    if ($script:ManualRequired) {
        $e = New-Object System.Exception('synthetic manual requirement')
        $e.Data['nightowl_error'] = 'manual_validation_required'
        throw $e
    }
    return @{ success=$true }
}
$NonInteractive = $true
$script:ManualRequired = $false
$result = Invoke-NightOwlEnrollment -BaseUrl 'https://example.test' -MachineId 'synthetic' -NoGuiMode
if (-not $result.success) { throw 'Automatic enrollment did not continue' }
$script:ManualRequired = $true
$failed = $false
try { Invoke-NightOwlEnrollment -BaseUrl 'https://example.test' -MachineId 'synthetic' -NoGuiMode }
catch {
    if (-not $_.Exception.Message.StartsWith('INSTALL_MANUAL_VALIDATION_REQUIRED:')) { throw }
    $failed = $true
}
if (-not $failed) { throw 'Manual enrollment did not fail closed' }
$escapedFunction = $fn.Extent.Text
$childTest = @"
`$ErrorActionPreference = 'Stop'
$escapedFunction
function Get-ComputerInfoLite { @{ Hostname='synthetic'; Domain='example.test' } }
function Write-InstallLog { param(`$EventType, `$Message, `$Metadata) }
function Read-Host { throw 'PROMPT_REACHED' }
function Show-ManualValidationDialog { throw 'GUI_REACHED' }
function Invoke-EnrollmentRequest {
    `$e = New-Object System.Exception('synthetic')
    `$e.Data['nightowl_error'] = 'manual_validation_required'
    throw `$e
}
`$NonInteractive = `$true
Invoke-NightOwlEnrollment -BaseUrl 'https://example.test' -MachineId 'synthetic' -NoGuiMode
"@
$encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($childTest))
$childInfo = New-Object System.Diagnostics.ProcessStartInfo
$childInfo.FileName = (Join-Path $PSHOME 'powershell.exe')
$childInfo.Arguments = '-NoProfile -NonInteractive -EncodedCommand ' + $encoded
$childInfo.UseShellExecute = $false
$childInfo.RedirectStandardOutput = $true
$childInfo.RedirectStandardError = $true
$child = New-Object System.Diagnostics.Process
$child.StartInfo = $childInfo
$child.Start() | Out-Null
$stdout = $child.StandardOutput.ReadToEndAsync()
$stderr = $child.StandardError.ReadToEndAsync()
if (-not $child.WaitForExit(10000)) { $child.Kill(); throw 'Noninteractive test hung' }
if ($child.ExitCode -eq 0) { throw 'Missing manual token must return a nonzero process exit code' }
if ($stderr.Result.Contains('PROMPT_REACHED') -or $stderr.Result.Contains('GUI_REACHED')) { throw 'Prompt reached' }
if (-not $stderr.Result.Contains('INSTALL_MANUAL_VALIDATION_REQUIRED:')) { throw 'Missing deterministic error' }
$child.Dispose()
# Recovery's only other prompt is guarded by both Force and NonInteractive.
$text = $ast.Extent.Text
if (-not $text.Contains('if (-not $Force -and -not $NonInteractive)')) { throw 'Recovery prompt not guarded' }
Write-Host 'Remote installer AST/noninteractive contract: PASS (32 parameters; auto/manual enrollment; no prompts).'
