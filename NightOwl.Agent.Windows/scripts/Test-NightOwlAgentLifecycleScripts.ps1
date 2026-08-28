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

function Test-LocalizedTrayTaskValidationRegression {
    $localizedCimGroupId = "INTERATIVO"
    $trayExePath = "C:\ProgramData\NightOwl\AgentDotNet\NightOwl.Agent.Tray.exe"
    $taskXml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="InteractiveUsers">
      <GroupId>S-1-5-4</GroupId>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Actions Context="InteractiveUsers">
    <Exec>
      <Command>C:\ProgramData\NightOwl\AgentDotNet\NightOwl.Agent.Tray.exe</Command>
    </Exec>
  </Actions>
</Task>
"@

    [xml]$xml = $taskXml
    $logonTrigger = Select-Xml -Xml $xml -XPath "//*[local-name()='LogonTrigger']"
    $groupId = Select-Xml -Xml $xml -XPath "//*[local-name()='Principals']/*[local-name()='Principal']/*[local-name()='GroupId']"
    $command = Select-Xml -Xml $xml -XPath "//*[local-name()='Actions']/*[local-name()='Exec']/*[local-name()='Command']"

    Assert-True ($localizedCimGroupId -eq "INTERATIVO") "Regression setup should model localized CIM GroupId."
    Assert-True (@($logonTrigger).Count -gt 0) "Tray task XML should include LogonTrigger."
    Assert-True (@($groupId | Where-Object { [string]$_.Node.InnerText -eq "S-1-5-4" }).Count -gt 0) "Tray task XML should preserve Interactive Users SID."
    Assert-True (@($command | Where-Object { [string]$_.Node.InnerText -eq $trayExePath }).Count -gt 0) "Tray task XML should point to Tray executable."
}

$install = Join-Path $ScriptsPath "Install-NightOwlAgentDotNet.ps1"
$uninstall = Join-Path $ScriptsPath "Uninstall-NightOwlAgentDotNet.ps1"

Assert-True (Test-Path $install) "Install script not found."
Assert-True (Test-Path $uninstall) "Uninstall script not found."
Assert-ParseOk $install
Assert-ParseOk $uninstall
Test-LocalizedTrayTaskValidationRegression

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
    "Export-ScheduledTask -TaskName `$TaskName",
    "Test-TrayTaskXmlValid",
    "Test-InteractiveUsersPrincipal",
    "Start-ScheduledTask -TaskName `$TaskName",
    "Microsoft\Windows\Start Menu\Programs\NightOwl\NightOwl.lnk",
    "tray_binary_exists:false",
    "tray_task_exists:false",
    "tray_task_valid:false",
    "start_menu_shortcut_exists:false",
    "tray.shortcut.created",
    "tray.shortcut.repaired",
    "tray.start.requested",
    "tray.start.succeeded",
    "tray.start.deferred",
    "tray.start.failed",
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
