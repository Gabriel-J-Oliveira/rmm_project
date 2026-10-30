$ErrorActionPreference = 'Stop'
$source = Get-Content -LiteralPath (Join-Path $PSScriptRoot '../NightOwl.Agent.Windows/Collectors/WindowsInventoryCollector.cs') -Raw
$platform = [regex]::Match($source, '(?s)ReadPlatform\(bool.*?"""\r?\n(.*?)\r?\n\s*""", timeoutSeconds').Groups[1].Value
$details = [regex]::Match($source, '(?s)private Dictionary<string, object\?> GetAssetHardwareDetails\(\).*?"""\r?\n(.*?)\r?\n\s*""", timeoutSeconds').Groups[1].Value
if (-not $platform -or -not $details) { throw 'Collector scripts not found' }
$script:queries = @{}
$script:failClass = ''
$script:notebook = $true
function Get-CimInstance {
    [CmdletBinding()]
    param([Parameter(Position=0)][string]$ClassName, [string]$Namespace)
    $script:queries[$ClassName] = 1 + [int]$script:queries[$ClassName]
    if ($ClassName -eq $script:failClass) { throw 'Synthetic query failure' }
    switch ($ClassName) {
        'Win32_ComputerSystem' { [pscustomobject]@{ PartOfDomain=$true; Domain='isolated.invalid'; Manufacturer='Synthetic'; Model='Synthetic Model'; TotalPhysicalMemory=17179869184; PCSystemTypeEx=2; UserName='SYNTHETIC\user' } }
        'Win32_OperatingSystem' { [pscustomobject]@{ ProductType=1; Caption='Synthetic Windows'; Version='10.0'; LastBootUpTime=[datetime]'2026-10-01T12:00:00Z'; InstallDate=[datetime]'2025-01-01T12:00:00Z' } }
        'Win32_BIOS' { [pscustomobject]@{ SerialNumber='SYNTH-123'; SMBIOSBIOSVersion='1.2'; ReleaseDate=[datetime]'2025-01-01T00:00:00Z' } }
        'Win32_Processor' { [pscustomobject]@{ Name='Synthetic CPU'; MaxClockSpeed=3600; NumberOfCores=6; NumberOfLogicalProcessors=12 } }
        'Win32_BaseBoard' { [pscustomobject]@{ Product='Synthetic Board'; Version='A1' } }
        'Win32_PhysicalMemory' { [pscustomobject]@{ Capacity=17179869184; PartNumber='SYNTH-PART'; Speed=3200; ConfiguredClockSpeed=2666; SMBIOSMemoryType=26 } }
        'Win32_PhysicalMemoryArray' { [pscustomobject]@{ Use=3; MemoryDevices=2; MaxCapacityEx=67108864 } }
        'Win32_Battery' { if ($script:notebook) { [pscustomobject]@{ Name='Synthetic Battery'; DeviceID='SYNTH-BAT'; BatteryStatus=2 } } }
        'BatteryStaticData' { [pscustomobject]@{ DesignedCapacity=50000; ManufactureName='Synthetic OEM'; DeviceName='Synthetic Battery' } }
        'BatteryFullChargedCapacity' { [pscustomobject]@{ FullChargedCapacity=40000 } }
        'BatteryCycleCount' { [pscustomobject]@{ CycleCount=123 } }
    }
}
$includeDetails = $true
$result = & ([scriptblock]::Create($platform))
if ($result.cpu_max_clock_mhz -ne 3600 -or $result.domain -ne 'isolated.invalid') { throw 'Platform projection failed' }
$result = & ([scriptblock]::Create($details))
if ($result.maximum_capacity_bytes[0] -ne 68719476736 -or $result.modules[0].part_number -ne 'SYNTH-PART' -or $result.battery.manufacturer -ne 'Synthetic OEM') { throw 'Hardware projection failed' }
foreach ($name in @('Win32_ComputerSystem','Win32_OperatingSystem','Win32_BIOS','Win32_Battery')) {
    if ($script:queries[$name] -ne 1) { throw "Duplicate query: $name" }
}
$script:notebook = $false
$result = & ([scriptblock]::Create($details))
if (-not $result.battery.query_succeeded -or $result.battery.present) { throw 'Desktop absence misclassified' }
$script:failClass = 'Win32_PhysicalMemory'
$result = & ([scriptblock]::Create($details))
if ($result.modules_query_succeeded -or -not $result.battery.query_succeeded) { throw 'Partial failure isolation failed' }
$script:failClass = 'Win32_Battery'
$result = & ([scriptblock]::Create($details))
if ($result.battery.query_succeeded -or -not $result.modules_query_succeeded) { throw 'Battery failure isolation failed' }
$script:failClass = ''
$script:queries = @{}
$includeDetails = $false
$null = & ([scriptblock]::Create($platform))
if ($script:queries.ContainsKey('Win32_Processor') -or $script:queries.ContainsKey('Win32_BaseBoard')) { throw 'Daily inventory ran detailed queries' }
Write-Output 'PASS: actual embedded scripts with synthetic CIM (platform, RAM, notebook, desktop, failures, daily query isolation)'
