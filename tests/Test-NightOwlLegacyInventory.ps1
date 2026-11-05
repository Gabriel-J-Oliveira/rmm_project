$ErrorActionPreference = 'Stop'
$root = Join-Path ([IO.Path]::GetTempPath()) ('NightOwlLegacyInventoryTest-' + [guid]::NewGuid().ToString('N'))
try {
    [IO.Directory]::CreateDirectory((Join-Path $root 'Config')) | Out-Null
    [IO.Directory]::CreateDirectory((Join-Path $root 'Identity')) | Out-Null
    [IO.File]::WriteAllText((Join-Path $root 'Config/agent.config.json'), '{"machineId":"synthetic-one","agentToken":"fixture-secret-never-export"}')
    [IO.File]::WriteAllText((Join-Path $root 'Identity/agent.identity.json'), '{"machine_id":"synthetic-two"}')
    function Get-CimInstance {
        param($ClassName, $Filter, $ErrorAction)
        [pscustomobject]@{State='Running'; StartMode='Auto'; PathName='do-not-export-command-arguments'}
    }
    function Get-ScheduledTask {
        param($ErrorAction)
        [pscustomobject]@{TaskName='RMM-Agent-Heartbeat'; State='Ready'; Actions=@([pscustomobject]@{Execute='powershell.exe';Arguments='fixture-secret-never-export'})}
    }
    $scriptPath = Join-Path $PSScriptRoot '../scripts/Get-NightOwlLegacyInventory.ps1'
    $json = & $scriptPath -RootPath $root -LegacyRootPath (Join-Path $root 'legacy-absent')
    $value = $json | ConvertFrom-Json
    if (-not $value.read_only -or $value.retirement_authorized -or $value.identity_consistent -or $value.distinct_identity_count -ne 2) { throw 'Identity conflict or retirement gate incorrect' }
    if (($json | Out-String).Contains('fixture-secret-never-export') -or ($json | Out-String).Contains('do-not-export-command-arguments')) { throw 'Inventory exposed credential or arguments' }
    function Get-CimInstance { param($ClassName, $Filter, $ErrorAction) throw 'Synthetic query denied' }
    $value = (& $scriptPath -RootPath $root -LegacyRootPath (Join-Path $root 'legacy-absent')) | ConvertFrom-Json
    if (@($value.services | Where-Object query_status -eq 'unavailable').Count -ne 2) { throw 'Denied query must remain unknown' }
    Write-Output 'PASS legacy inventory: read-only fixtures, identity disagreement, credential/argument omission, unavailable queries.'
}
finally {
    $resolvedRoot = [IO.Path]::GetFullPath($root)
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not $resolvedRoot.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -or
        -not [IO.Path]::GetFileName($resolvedRoot).StartsWith('NightOwlLegacyInventoryTest-')) { throw 'Unsafe inventory fixture cleanup' }
    if (Test-Path -LiteralPath $resolvedRoot) { Remove-Item -LiteralPath $resolvedRoot -Recurse -Force }
}
