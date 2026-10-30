$ErrorActionPreference = 'Stop'
$installer = Join-Path $PSScriptRoot '../NightOwl.Agent.Windows/scripts/Install-NightOwlAgentDotNet.ps1'
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile((Resolve-Path $installer), [ref]$null, [ref]$errors)
if ($errors.Count) { throw 'Installer parse failed' }
foreach ($name in @('Get-TelemetryConfig','Merge-AgentRuntimeConfig')) {
    $fn = $ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name}, $true)
    if (-not $fn) { throw "Required installer helper missing: $name" }
    . ([scriptblock]::Create($fn.Extent.Text))
}
function New-GeneratedConfig {
    [ordered]@{agentToken='new-fixture-token'; machineId='fixture-machine'; agentVersion='fixture-version';
        installPath='C:\Fixture\AgentDotNet'; serverBaseUrl='https://isolated.invalid';
        heartbeatUrl='https://isolated.invalid/api/agent/heartbeat/'; collectUrl='https://isolated.invalid/api/agent/collect/';
        jobsPullUrl='https://isolated.invalid/api/agent/jobs/pull/'; jobsResultUrl='https://isolated.invalid/api/agent/jobs/result/';
        intervals=[ordered]@{heartbeatSeconds=300;collectSeconds=3600;jobsSeconds=10}; allowedJobTypes=@('ping','repair_agent')}
}
$fresh = Merge-AgentRuntimeConfig (New-GeneratedConfig) $null
if (-not $fresh.telemetryEnabled -or $fresh.telemetryMode -ne 'legacy' -or $fresh.telemetryFlushSeconds -ne 900) { throw 'Installer effective defaults changed' }
$upgradedFresh = Merge-AgentRuntimeConfig (New-GeneratedConfig) ($fresh | ConvertTo-Json -Depth 8 | ConvertFrom-Json)
if (($fresh | ConvertTo-Json -Depth 8 -Compress) -ne ($upgradedFresh | ConvertTo-Json -Depth 8 -Compress)) { throw 'Clean and unchanged updated installation diverged' }
$existing = [pscustomobject]@{
    agentToken='old-fixture-token';machineId='old-id';agentVersion='old-version';installPath='C:\OldInstall'
    serverBaseUrl='https://prior.invalid/prefix';heartbeatUrl='https://prior.invalid/custom/heartbeat'
    telemetryEnabled=$true;telemetryMode='continuous';telemetryWindowMaxBytes=2097152;telemetryWindowMaxAgeHours=48
    telemetrySampleSeconds=600;telemetryFlushSeconds=1800;telemetryBufferMaxSamples=100
    jobSignalSeconds=7;softwareInstallEnabled=$false;softwareCatalogSha256=('a'*64)
    configMigrationVersion=6;allowedJobTypes=@('ping');futureSetting='preserve-me'
    intervals=[pscustomobject]@{heartbeatSeconds=125;collectSeconds=7200;jobsSeconds=31}
}
$merged = Merge-AgentRuntimeConfig (New-GeneratedConfig) $existing
foreach ($key in @('telemetryMode','telemetryWindowMaxBytes','telemetryWindowMaxAgeHours','jobSignalSeconds','futureSetting','serverBaseUrl','heartbeatUrl','softwareCatalogSha256')) {
    if ($merged[$key] -ne $existing.$key) { throw "Explicit setting lost: $key" }
}
if ($merged.agentToken -ne 'new-fixture-token' -or $merged.machineId -ne 'fixture-machine' -or $merged.installPath -ne 'C:\Fixture\AgentDotNet' -or
    @($merged.allowedJobTypes).Count -ne 1 -or $merged.intervals.heartbeatSeconds -ne 125) { throw 'Lifecycle authority/allowlist/interval contract failed' }
$moved = Merge-AgentRuntimeConfig (New-GeneratedConfig) $existing $true
if ($moved.serverBaseUrl -ne 'https://isolated.invalid' -or $moved.heartbeatUrl -ne 'https://isolated.invalid/api/agent/heartbeat/') { throw 'Explicit server override retained stale routes' }
$legacy = Merge-AgentRuntimeConfig (New-GeneratedConfig) ([pscustomobject]@{})
if ($legacy.telemetryEnabled -or $legacy.telemetryMode -ne 'legacy' -or $legacy.telemetryFlushSeconds -ne 3600) { throw 'Legacy config was silently enabled' }
$exe = Join-Path $PSScriptRoot '../NightOwl.Agent.Windows.Tests/bin/Release/net8.0-windows/NightOwl.Agent.Windows.Tests.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw 'Build Windows.Tests first; contract gate cannot be skipped' }
$contract = (($merged | ConvertTo-Json -Depth 8 -Compress) | & $exe --installer-config-contract) | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $contract.telemetryMode -ne 'continuous' -or $contract.jobSignalSeconds -ne 7 -or
    $contract.telemetryWindowMaxBytes -ne 2097152 -or @($contract.allowedJobTypes).Count -ne 1 -or
    $contract.intervals.heartbeatSeconds -ne 125 -or $contract.softwareInstallEnabled -or -not $contract.futureSettingPreserved) { throw 'Actual .NET configuration loader changed the installer contract' }
if ($ast.Extent.Text.IndexOf('$preservedConfig = Read-JsonFile $configPath') -gt $ast.Extent.Text.IndexOf('$sourcePath = Download-AgentPackage')) { throw 'Existing server/config must be resolved before download' }
Write-Output 'PASS installer configuration: clean/update equivalence, preserved explicit policy/identity authority, routes, legacy opt-out and actual .NET loader.'
