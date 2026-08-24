param(
    [string]$ScriptsPath = $PSScriptRoot
)

$ErrorActionPreference = "Stop"

function Assert-True($Condition, [string]$Message) {
    if (-not $Condition) {
        throw $Message
    }
}

function Assert-ParseOk([string]$Path) {
    $errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path $Path), [ref]$null, [ref]$errors) | Out-Null
    if ($errors.Count -gt 0) {
        throw ("Parser errors in {0}: {1}" -f $Path, ($errors | ForEach-Object { $_.Message } | Out-String))
    }
}

$install = Join-Path $ScriptsPath "Install-NightOwlAgentDotNet.ps1"
$uninstall = Join-Path $ScriptsPath "Uninstall-NightOwlAgentDotNet.ps1"

Assert-True (Test-Path $install) "Install script not found."
Assert-True (Test-Path $uninstall) "Uninstall script not found."
Assert-ParseOk $install
Assert-ParseOk $uninstall

$installText = Get-Content -Path $install -Raw
$uninstallText = Get-Content -Path $uninstall -Raw

foreach ($required in @(
    "[switch]`$Install",
    "[switch]`$Repair",
    "[switch]`$Reinstall",
    "[switch]`$ForceRecovery",
    "Global\NightOwl.Agent.Update",
    "INSTALL_UPDATE_IN_PROGRESS",
    "REPAIR_UPDATE_IN_PROGRESS",
    "REINSTALL_UPDATE_IN_PROGRESS",
    "REPAIR_FORCE_RECOVERY_REQUIRED",
    "pending-results",
    "update-state.json",
    "Copy-AgentBinaries",
    "NightOwl.Agent.Diagnostics.exe",
    "NightOwl.Agent.Uninstaller.exe",
    "S-1-5-4",
    "Register-ScheduledTask -TaskName `$taskName -Xml",
    "INSTALL_PACKAGE_INSECURE_URL",
    "INSTALL_MANIFEST_MISSING",
    "INSTALL_SIGNATURE_INVALID",
    "INSTALL_SIGNING_KEY_REVOKED",
    "ExpectedVersion",
    "ExpectedChannel",
    "ExpectedPackageSha256",
    "INSTALL_RELEASE_METADATA_MISMATCH",
    "legacy_config_present_on_clean_install",
    "INSTALL_TRAY_TASK_FAILED",
    "uninstall_agent",
    "TrustLocalPackage",
    "Protect-SecretValue"
)) {
    Assert-True ($installText.Contains($required)) "Install script missing required lifecycle marker: $required"
}

Assert-True (-not $installText.Contains("/RU INTERACTIVE")) "Install script must not depend on localized INTERACTIVE account name."
Assert-True (-not $installText.Contains('agent_version = "0.1.0.7"')) "Install script must not enroll with a hardcoded legacy version."
Assert-True (-not $installText.Contains('channel = "stable"')) "Install script must not write a hardcoded stable channel."
Assert-True (-not $installText.Contains('packageSha256 = ""')) "Install script must not write an empty package SHA."
Assert-True ($installText.Contains('throw ("{0}: {1}" -f $healthCode')) "Install script must fail closed on lifecycle health errors."

foreach ($required in @(
    "[switch]`$Purge",
    "Global\NightOwl.Agent.Update",
    "UNINSTALL_UPDATE_IN_PROGRESS",
    "PURGE_UPDATE_IN_PROGRESS",
    "PURGE_CONFIRMATION_REQUIRED",
    "UNINSTALL_SERVICE_REMOVE_FAILED",
    "update-state.json",
    "update-state.preserved",
    "persistent.removed",
    "state.marked_uninstalled",
    "Protect-SecretValue"
)) {
    Assert-True ($uninstallText.Contains($required)) "Uninstall script missing required lifecycle marker: $required"
}

foreach ($forbidden in @(
    "Remove-Item -Path `$script:Paths.State",
    "Remove-Item -Path `$script:Paths.Config",
    "Remove-Item -Path `$script:Paths.Identity"
)) {
    Assert-True (-not $uninstallText.Contains($forbidden) -or $uninstallText.Contains("if (`$Purge)")) "Uninstall may remove persistent data outside purge: $forbidden"
}

Write-Host "NightOwl lifecycle script tests passed."
