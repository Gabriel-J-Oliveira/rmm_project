#requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$path = Join-Path $PSScriptRoot 'Configure-NightOwlWinRMHttps.ps1'
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw "PowerShell syntax errors: $($errors.Count)" }

$source = $ast.Extent.Text
$names = @($ast.ParamBlock.Parameters | ForEach-Object { $_.Name.VariablePath.UserPath })
foreach ($required in @('NightOwlSourceIp', 'CertificateTemplate', 'Apply', 'Rollback', 'StatePath')) {
    if ($names -notcontains $required) { throw "Missing parameter: $required" }
}

$commands = @($ast.FindAll({ param($node) $node -is [Management.Automation.Language.CommandAst] }, $true) |
    ForEach-Object { $_.GetCommandName() })
foreach ($forbidden in @('New-SelfSignedCertificate', 'Export-PfxCertificate', 'Export-Certificate',
        'Enable-PSRemoting', 'Set-Item', 'Restart-Service', 'Restart-Computer')) {
    if ($commands -contains $forbidden) { throw "Forbidden command: $forbidden" }
}
foreach ($forbiddenText in @('AllowUnencrypted', 'TrustedHosts', 'Basic=true', '-RemoteAddress Any',
        '-RemoteAddress 0.0.0.0/0', 'Exportable=TRUE', 'winrm quickconfig')) {
    if ($source.Contains($forbiddenText)) { throw "Forbidden text: $forbiddenText" }
}

foreach ($requiredText in @(
        "if (`$mode -eq 'AUDIT') {",
        'Get-AuditSnapshot $source',
        'Add-AuditDiagnostics $summary $snapshot.Components',
        'if (-not (Test-Administrator))',
        'CERT_ENROLLMENT_ATTEMPTED',
        'certreq.exe -enroll -machine',
        'Get-CertificateAssessment',
        'HasPrivateKey',
        '1.3.6.1.5.5.7.3.1',
        'DnsNameList',
        'ExpectedRootThumbprint',
        'New-BackupState',
        'Read-BackupState',
        'Get-NetFirewallRule -PolicyStore PersistentStore -ErrorAction Stop',
        '-RemoteAddress $SourceIp',
        "READY_FOR_NIGHTOWL_PREFLIGHT")) {
    if (-not $source.Contains($requiredText)) { throw "Missing safety gate: $requiredText" }
}

Write-Output 'STATIC_BOOTSTRAP_TESTS=PASS; REAL_WINDOWS_MUTATIONS=0'
