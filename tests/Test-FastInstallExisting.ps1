$ErrorActionPreference = 'Stop'
$path = (Resolve-Path (Join-Path $PSScriptRoot '..\agents\bootstrap\nightowl_deployment_bootstrap.ps1')).Path
$errors = $null; $ast = [Management.Automation.Language.Parser]::ParseFile($path, [ref]$null, [ref]$errors)
if ($errors.Count) { throw 'Invalid bootstrap syntax' }
foreach ($name in @('Get-OptionalJsonProperty','Get-ExistingInstallationState')) {
    $fn = $ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name}, $true)
    Invoke-Expression $fn.Extent.Text
}
$root = Join-Path ([IO.Path]::GetTempPath()) ('nightowl-existing-fi01b-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root | Out-Null
$script:service = $null
function Get-Service { param($Name, $ErrorAction) return $script:service }
$hash = 'a' * 64
$metadata = [pscustomobject]@{ server_url='https://example.invalid'; release=[pscustomobject]@{version='1.0'; sha256=$hash} }
try {
    if ((Get-ExistingInstallationState $root $metadata) -ne 'absent') { throw 'Absent classification failed' }
    $script:service = [pscustomobject]@{Status='Running'}
    if ((Get-ExistingInstallationState $root $metadata) -ne 'incomplete') { throw 'Incomplete classification failed' }
    foreach ($dir in 'AgentDotNet','Config','Identity','State') { New-Item -ItemType Directory -Path (Join-Path $root $dir) | Out-Null }
    [IO.File]::WriteAllText((Join-Path $root 'AgentDotNet\NightOwl.Agent.Windows.exe'), 'fixture')
    @{machineId='fixture-id';agentToken='fixture-token';serverBaseUrl='https://example.invalid'} | ConvertTo-Json | Set-Content (Join-Path $root 'Config\agent.config.json')
    @{machine_id='fixture-id'} | ConvertTo-Json | Set-Content (Join-Path $root 'Identity\agent.identity.json')
    @{machine_id='fixture-id';lastHeartbeatAt=[DateTimeOffset]::UtcNow.ToString('o')} | ConvertTo-Json | Set-Content (Join-Path $root 'State\agent.state.json')
    @{version='other';packageSha256=$hash} | ConvertTo-Json | Set-Content (Join-Path $root 'AgentDotNet\agent.version.json')
    if ((Get-ExistingInstallationState $root $metadata) -ne 'different_version') { throw 'Version classification failed' }
    @{version='1.0';packageSha256=$hash} | ConvertTo-Json | Set-Content (Join-Path $root 'AgentDotNet\agent.version.json')
    if ((Get-ExistingInstallationState $root $metadata) -ne 'healthy_compatible') { throw 'Healthy classification failed' }
    @{machine_id='fixture-id';lastHeartbeatAt=[DateTimeOffset]::UtcNow.AddMinutes(-20).ToString('o')} | ConvertTo-Json | Set-Content (Join-Path $root 'State\agent.state.json')
    if ((Get-ExistingInstallationState $root $metadata) -ne 'unverified') { throw 'Stale heartbeat accepted' }
    @{machine_id='other-id';lastHeartbeatAt=[DateTimeOffset]::UtcNow.ToString('o')} | ConvertTo-Json | Set-Content (Join-Path $root 'State\agent.state.json')
    if ((Get-ExistingInstallationState $root $metadata) -ne 'incomplete') { throw 'Mismatched identity accepted' }
    @{machine_id='fixture-id';lastHeartbeatAt=[DateTimeOffset]::UtcNow.ToString('o')} | ConvertTo-Json | Set-Content (Join-Path $root 'State\agent.state.json')
    $script:service.Status = 'Stopped'
    if ((Get-ExistingInstallationState $root $metadata) -ne 'unverified') { throw 'Unverified classification failed' }
    if ((Get-Content -Raw (Join-Path $root 'Identity\agent.identity.json')) -notmatch 'fixture-id') { throw 'Identity changed' }
    Write-Host 'PASS: absent, incomplete, different version, compatible and unverified; identity preserved.'
} finally {
    if ((Split-Path -Parent $root) -eq ([IO.Path]::GetTempPath().TrimEnd('\')) -and (Split-Path -Leaf $root) -like 'nightowl-existing-fi01b-*') { Remove-Item -LiteralPath $root -Recurse -Force }
}
