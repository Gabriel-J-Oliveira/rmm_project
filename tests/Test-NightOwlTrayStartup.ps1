$ErrorActionPreference = 'Stop'
$exe = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../NightOwl.Agent.Tray/bin/Release/net8.0-windows/NightOwl.Agent.Tray.exe')).Path
$root = Join-Path ([IO.Path]::GetTempPath()) ('NightOwlTrayStartupTest-' + [guid]::NewGuid().ToString('N'))
$priorHome = $env:NIGHTOWL_HOME
$priorDotnet = $env:DOTNET_ROOT
$env:NIGHTOWL_HOME = $root
$env:DOTNET_ROOT = Join-Path $env:LOCALAPPDATA 'NightOwlDev/dotnet'
function Invoke-TrayFixture([string]$ExpectedEvent) {
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $exe
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
    $child = [Diagnostics.Process]::Start($info)
    try {
        if (-not $child.WaitForExit(15000)) {
            $child.Kill(); $child.WaitForExit()
            throw 'Owned development Tray did not exit within fixture budget'
        }
        if ($child.ExitCode -ne 0) { throw 'Development Tray failed' }
        $log = Join-Path $root 'Logs/agent-tray.jsonl'
        if (-not (Test-Path -LiteralPath $log) -or -not ([IO.File]::ReadAllText($log)).Contains($ExpectedEvent)) { throw 'Tray startup branch was not observed' }
        foreach ($name in @('Config','Identity','State','AgentDotNet','Updates')) {
            if (Test-Path -LiteralPath (Join-Path $root $name)) { throw 'Interactive Tray must not bootstrap protected state' }
        }
        Write-Output ("PASS actual Tray process: {0}; session={1}; exit_ms={2}" -f $ExpectedEvent,[Diagnostics.Process]::GetCurrentProcess().SessionId,$watch.ElapsedMilliseconds)
    }
    finally { $child.Dispose() }
}
try {
    $created = $false
    $mutex = New-Object Threading.Mutex($false, 'NightOwl.Agent.Tray.User', [ref]$created)
    try { Invoke-TrayFixture 'tray.mutex.exists' }
    finally { $mutex.Dispose() }
    # Read-only SCM check. Never start, stop or register any service to make this test pass.
    $service = Get-Service -Name 'NightOwlAgentDotNet' -ErrorAction SilentlyContinue
    if ($null -eq $service -and $created) { Invoke-TrayFixture 'tray.exit.service_not_running' }
    else { Write-Output 'NOT_RUN service-absent branch: installed service or preexisting Tray mutex; no service changed.' }
}
finally {
    $env:NIGHTOWL_HOME = $priorHome
    $env:DOTNET_ROOT = $priorDotnet
    $resolved = [IO.Path]::GetFullPath($root)
    $prefix = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not $resolved.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase) -or
        -not [IO.Path]::GetFileName($resolved).StartsWith('NightOwlTrayStartupTest-')) { throw 'Unsafe Tray fixture cleanup' }
    if (Test-Path -LiteralPath $resolved) { Remove-Item -LiteralPath $resolved -Recurse -Force }
}
