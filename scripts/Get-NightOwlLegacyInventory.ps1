# Read-only, local inventory. Never starts/stops services, runs an agent or reads token values.
param([string]$RootPath = '', [string]$LegacyRootPath = 'C:\RMM')
$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($RootPath)) {
    $RootPath = if ($env:NIGHTOWL_HOME) { $env:NIGHTOWL_HOME } else { Join-Path $env:ProgramData 'NightOwl' }
}
$services = @()
foreach ($name in @('NightOwlAgentDotNet', 'NightOwlAgent')) {
    try {
        $service = Get-CimInstance Win32_Service -Filter ("Name='{0}'" -f $name) -ErrorAction Stop
        $services += [ordered]@{ name=$name; present=($null -ne $service); state=[string]$service.State;
            start_mode=[string]$service.StartMode; query_status='available' }
    }
    catch { $services += [ordered]@{name=$name; present=$null; query_status='unavailable'; error_type=$_.Exception.GetType().Name} }
}
$tasks = @()
foreach ($name in @('RMM-Agent-Heartbeat', 'NightOwl Agent Tray')) {
    try {
        $task = Get-ScheduledTask -ErrorAction Stop | Where-Object TaskName -eq $name
        $tasks += [ordered]@{ name=$name; present=(@($task).Count -gt 0);
            states=@($task | ForEach-Object { [string]$_.State });
            executables=@($task.Actions | ForEach-Object { [IO.Path]::GetFileName([string]$_.Execute) }); query_status='available' }
    }
    catch { $tasks += [ordered]@{name=$name; present=$null; query_status='unavailable'; error_type=$_.Exception.GetType().Name} }
}
$sources = @()
$relativePaths = @('Config\agent.config.json', 'Identity\agent.identity.json', 'State\agent.state.json',
    'AgentDotNet\agent.config.json', 'AgentDotNet\agent-dotnet.state.json', 'Agent\agent.state.json')
$paths = @($relativePaths | ForEach-Object { Join-Path $RootPath $_ }) + @(Join-Path $LegacyRootPath 'agent.state.json')
foreach ($path in $paths) {
    $source = [ordered]@{path=$path; status='absent'; machine_id_sha256=''}
    if (Test-Path -LiteralPath $path) {
        try {
            # Never evaluate legacy .ps1 configuration or return arbitrary JSON fields.
            if ((Get-Item -LiteralPath $path).Length -gt 1048576) { throw 'Inventory input exceeds limit' }
            $value = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
            $id = [string]$value.machine_id
            if ([string]::IsNullOrWhiteSpace($id)) { $id = [string]$value.machineId }
            $source.status = 'identity_unknown'
            if (-not [string]::IsNullOrWhiteSpace($id)) {
                $sha = [Security.Cryptography.SHA256]::Create()
                try { $source.machine_id_sha256 = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($id)))).Replace('-','').ToLowerInvariant() }
                finally { $sha.Dispose() }
                $source.status = 'available'
            }
        }
        catch { $source.status = 'unavailable' }
    }
    $sources += $source
}
$hashes = @($sources | Where-Object status -eq 'available' | ForEach-Object { $_.machine_id_sha256 } | Select-Object -Unique)
[ordered]@{
    schema_version=1; collected_at=[datetimeoffset]::UtcNow.ToString('o'); read_only=$true
    services=$services; tasks=$tasks; identity_sources=$sources
    identity_consistent=($hashes.Count -eq 1); distinct_identity_count=$hashes.Count
    retirement_authorized=$false
    decision='Review identity provenance and real heartbeat/inventory/job ACK before disabling any legacy component.'
} | ConvertTo-Json -Depth 7
