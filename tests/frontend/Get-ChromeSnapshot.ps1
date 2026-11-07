# CI-only: inspect the owned Chrome tree without emitting command lines or paths.
$ErrorActionPreference = 'Stop'
$rootId = [int]$env:NIGHTOWL_SNAPSHOT_PID
$profile = $env:NIGHTOWL_SNAPSHOT_PROFILE
$early = [ordered]@{ snapshot_utc=[DateTime]::UtcNow.ToString('o'); root_pid=$rootId; process_state='unavailable';
    cpu_seconds=$null; handle_count=$null; threads=@(); profile_entry_count=$null }
try {
    $proc=Get-Process -Id $rootId -ErrorAction Stop
    $early.process_state='running'; $early.cpu_seconds=$proc.CPU; $early.handle_count=$proc.HandleCount
    $counts=@{}; foreach ($thread in $proc.Threads) {
        $state=[string]$thread.ThreadState; $wait='not_waiting'
        if ($state -eq 'Wait') { try { $wait=[string]$thread.WaitReason } catch { $wait='unavailable' } }
        $key=$state+':'+$wait; if (-not $counts.ContainsKey($key)) {$counts[$key]=0}; $counts[$key]++
    }
    $early.threads=@($counts.GetEnumerator() | Sort-Object Name | Select-Object -First 20 | ForEach-Object { @{state=$_.Name;count=$_.Value} })
} catch { }
try { $early.profile_entry_count=@(Get-ChildItem -LiteralPath $profile -Force -ErrorAction Stop).Count } catch { }
Write-Output ('CHROME_SNAPSHOT_EARLY='+($early | ConvertTo-Json -Compress -Depth 4))
$report = [ordered]@{ snapshot_utc = [DateTime]::UtcNow.ToString('o'); root_pid = $rootId;
    process_query = 'unavailable'; processes = @(); listeners = $null; events = $null;
    profile = @{ exists = (Test-Path -LiteralPath $profile); local_state = (Test-Path -LiteralPath (Join-Path $profile 'Local State'));
        default = (Test-Path -LiteralPath (Join-Path $profile 'Default')); cdp_file = (Test-Path -LiteralPath (Join-Path $profile 'DevToolsActivePort')) };
    open_profile_files = 'unavailable' }
try {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class NightOwlSnapshotToken {
    [DllImport("kernel32.dll", SetLastError=true)] static extern IntPtr OpenProcess(uint a, bool i, int id);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
    [DllImport("advapi32.dll", SetLastError=true)] static extern bool OpenProcessToken(IntPtr p, uint a, out IntPtr t);
    [DllImport("advapi32.dll", SetLastError=true)] static extern bool GetTokenInformation(IntPtr t, int c, IntPtr p, int n, out int r);
    [DllImport("advapi32.dll")] static extern IntPtr GetSidSubAuthorityCount(IntPtr sid);
    [DllImport("advapi32.dll")] static extern IntPtr GetSidSubAuthority(IntPtr sid, int i);
    public static int Integrity(int pid) {
        IntPtr p=OpenProcess(0x1000,false,pid), t=IntPtr.Zero, buf=IntPtr.Zero;
        try {
            if (p==IntPtr.Zero || !OpenProcessToken(p,8,out t)) return -1;
            int n; GetTokenInformation(t,25,IntPtr.Zero,0,out n);
            if (n<=0 || n>65536) return -1;
            buf=Marshal.AllocHGlobal(n);
            if (!GetTokenInformation(t,25,buf,n,out n)) return -1;
            IntPtr sid=Marshal.ReadIntPtr(buf); int count=Marshal.ReadByte(GetSidSubAuthorityCount(sid));
            return Marshal.ReadInt32(GetSidSubAuthority(sid,count-1));
        } finally { if(buf!=IntPtr.Zero) Marshal.FreeHGlobal(buf); if(t!=IntPtr.Zero) CloseHandle(t); if(p!=IntPtr.Zero) CloseHandle(p); }
    }
}
'@ -ErrorAction Stop
} catch { }
try {
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $all = @(Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" -ErrorAction Stop)
    $ids = [Collections.Generic.HashSet[int]]::new(); [void]$ids.Add($rootId)
    do {
        $changed = $false
        foreach ($p in $all) { if ($ids.Contains([int]$p.ParentProcessId) -and $ids.Add([int]$p.ProcessId)) { $changed=$true } }
    } while ($changed -and $ids.Count -lt 64)
    $allow = @('headless','remote-debugging-port','user-data-dir','no-first-run','no-default-browser-check',
        'disable-sync','disable-background-networking','disable-background-timer-throttling','disable-renderer-backgrounding',
        'disable-backgrounding-occluded-windows','disable-features','enable-logging','type','no-sandbox','disable-gpu')
    $items = @()
    foreach ($p in @($all | Where-Object { $ids.Contains([int]$_.ProcessId) } | Select-Object -First 64)) {
        $owner = $null; $integrity = $null; $threads = @(); $handles = $null; $cpu = $null
        try { $o=Invoke-CimMethod -InputObject $p -MethodName GetOwnerSid -ErrorAction Stop; if ($o.ReturnValue -eq 0) { $owner=($o.Sid -eq $sid) } } catch { }
        try { $i=[NightOwlSnapshotToken]::Integrity([int]$p.ProcessId); if ($i -ge 0) { $integrity=$i } } catch { }
        try {
            $proc=Get-Process -Id $p.ProcessId -ErrorAction Stop; $handles=$proc.HandleCount; $cpu=$proc.CPU
            $counts=@{}; foreach ($thread in $proc.Threads) {
                $state=[string]$thread.ThreadState; $wait='not_waiting'
                if ($state -eq 'Wait') { try { $wait=[string]$thread.WaitReason } catch { $wait='unavailable' } }
                $key=$state+':'+$wait; if (-not $counts.ContainsKey($key)) {$counts[$key]=0}; $counts[$key]++
            }
            $threads=@($counts.GetEnumerator() | Sort-Object Name | Select-Object -First 20 | ForEach-Object { @{ state=$_.Name; count=$_.Value } })
        } catch { }
        $switches=@(); $kind='unknown'
        if ($p.CommandLine) {
            foreach ($m in [regex]::Matches($p.CommandLine,'(?:^|\s)--([a-z0-9-]+)(?:=([^\s"]+))?','IgnoreCase')) {
                $name=$m.Groups[1].Value.ToLowerInvariant(); if ($name -in $allow) { $switches += $name }
                if ($name -eq 'type' -and $m.Groups[2].Value -in @('renderer','gpu-process','utility','crashpad-handler','zygote')) { $kind=$m.Groups[2].Value }
            }
            if ([int]$p.ProcessId -eq $rootId) { $kind='browser' }
        }
        $items += @{ pid=[int]$p.ProcessId; parent_pid=[int]$p.ParentProcessId; started_utc=$p.CreationDate.ToUniversalTime().ToString('o');
            same_user=$owner; integrity_rid=$integrity; session_id=[int]$p.SessionId; process_kind=$kind;
            known_switches=@($switches | Sort-Object -Unique); handle_count=$handles; cpu_seconds=$cpu; threads=$threads }
    }
    $report.process_query='ok'; $report.processes=$items
    try { $report.listeners=@(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object {
        $ids.Contains([int]$_.OwningProcess) -and $_.LocalAddress -in @('127.0.0.1','::1')
    } | Select-Object @{n='pid';e={[int]$_.OwningProcess}}, @{n='port';e={[int]$_.LocalPort}}) } catch { }
} catch { }
try {
    $report.events=@(Get-WinEvent -FilterHashtable @{LogName='Application'; StartTime=(Get-Date).AddSeconds(-45); Level=2} -MaxEvents 50 -ErrorAction Stop |
        Where-Object { $_.ProviderName -in @('Application Error','Windows Error Reporting') } |
        Select-Object -First 10 @{n='provider';e={$_.ProviderName}}, @{n='event_id';e={$_.Id}}, @{n='utc';e={$_.TimeCreated.ToUniversalTime().ToString('o')}})
} catch { }
$report | ConvertTo-Json -Compress -Depth 8
