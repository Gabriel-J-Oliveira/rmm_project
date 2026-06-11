<#
.SYNOPSIS
Exports NTFS folder ACLs from a file server share to JSON for Django access_inventory.

.DESCRIPTION
This script walks folders below a UNC path, reads NTFS ACLs with Get-Acl, and writes
a JSON file compatible with the Django management command import_file_acl.

The output uses flat top-level lists: file_servers, shares, folders, acl_entries.
Errors are collected in a top-level errors list and do not stop the export.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$FileServerName,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$ShareName,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$UncPath,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$OutputPath,

    [Parameter(Mandatory = $false)]
    [int]$MaxDepth = -1,

    [Parameter(Mandatory = $false)]
    [switch]$IncludeInherited,

    [Parameter(Mandatory = $false)]
    [switch]$VerboseLog
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

function Write-Log {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Write-Host "[$timestamp] $Message"
}

function Write-VerboseLog {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    if ($VerboseLog) {
        Write-Log $Message
    }
}

function Normalize-PathNoTrailingSlash {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $trimmed = $Path.Trim()
    while ($trimmed.Length -gt 2 -and $trimmed.EndsWith('\')) {
        $trimmed = $trimmed.Substring(0, $trimmed.Length - 1)
    }
    return $trimmed
}

function Get-HostFqdn {
    param(
        [Parameter(Mandatory = $true)]
        [string]$HostName
    )

    try {
        return ([System.Net.Dns]::GetHostEntry($HostName)).HostName
    }
    catch {
        return ''
    }
}

function Convert-IdentityToSid {
    param(
        [Parameter(Mandatory = $true)]
        [System.Security.Principal.IdentityReference]$Identity
    )

    try {
        if ($Identity -is [System.Security.Principal.SecurityIdentifier]) {
            return $Identity.Value
        }

        $sid = $Identity.Translate([System.Security.Principal.SecurityIdentifier])
        return $sid.Value
    }
    catch {
        return ''
    }
}

function Convert-AccessType {
    param(
        [Parameter(Mandatory = $true)]
        [object]$AccessControlType
    )

    $value = [string]$AccessControlType
    if ($value -ieq 'Deny') {
        return 'deny'
    }
    return 'allow'
}

function Get-InventoryPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FullPath,

        [Parameter(Mandatory = $true)]
        [string]$RootPath,

        [Parameter(Mandatory = $true)]
        [string]$RootShareName
    )

    $normalizedFull = Normalize-PathNoTrailingSlash -Path $FullPath
    $normalizedRoot = Normalize-PathNoTrailingSlash -Path $RootPath

    if ($normalizedFull.Equals($normalizedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $RootShareName
    }

    $relative = $normalizedFull.Substring($normalizedRoot.Length).TrimStart('\')
    if ([string]::IsNullOrWhiteSpace($relative)) {
        return $RootShareName
    }

    return "$RootShareName\$relative"
}

function Get-ParentInventoryPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$InventoryPath,

        [Parameter(Mandatory = $true)]
        [string]$RootShareName
    )

    if ($InventoryPath.Equals($RootShareName, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $null
    }

    $lastSlash = $InventoryPath.LastIndexOf('\')
    if ($lastSlash -lt 0) {
        return $null
    }

    return $InventoryPath.Substring(0, $lastSlash)
}

function Add-ExportError {
    param(
        [System.Collections.Generic.List[object]]$ExportErrors,

        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Stage,

        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    $ExportErrors.Add([ordered]@{
        path = $Path
        stage = $Stage
        message = $Message
    })
}

function Get-FolderQueue {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RootPath,

        [Parameter(Mandatory = $true)]
        [int]$MaximumDepth,

        [System.Collections.Generic.List[object]]$ExportErrors
    )

    $queue = New-Object 'System.Collections.Generic.Queue[object]'
    $queue.Enqueue([pscustomobject]@{
        Path = $RootPath
        Depth = 0
    })

    while ($queue.Count -gt 0) {
        $item = $queue.Dequeue()
        Write-Output $item

        if ($MaximumDepth -ge 0 -and $item.Depth -ge $MaximumDepth) {
            continue
        }

        try {
            $children = Get-ChildItem -LiteralPath $item.Path -Directory -Force -ErrorAction Stop
        }
        catch {
            Add-ExportError -ExportErrors $ExportErrors -Path $item.Path -Stage 'enumerate_children' -Message $_.Exception.Message
            continue
        }

        foreach ($child in $children) {
            $queue.Enqueue([pscustomobject]@{
                Path = $child.FullName
                Depth = ($item.Depth + 1)
            })
        }
    }
}

$normalizedUncPath = Normalize-PathNoTrailingSlash -Path $UncPath
$fqdn = Get-HostFqdn -HostName $FileServerName
$startedAt = Get-Date

$folders = New-Object 'System.Collections.Generic.List[object]'
$aclEntries = New-Object 'System.Collections.Generic.List[object]'
$exportErrors = New-Object 'System.Collections.Generic.List[object]'
$processedFolders = 0
$processedAclEntries = 0

Write-Log "Starting NTFS ACL export."
Write-Log "File server: $FileServerName"
Write-Log "Share: $ShareName"
Write-Log "UNC path: $normalizedUncPath"
if ($MaxDepth -ge 0) {
    Write-Log "Max depth: $MaxDepth"
}
else {
    Write-Log "Max depth: unlimited"
}
Write-Log "Include inherited ACL entries: $($IncludeInherited.IsPresent)"

if (-not (Test-Path -LiteralPath $normalizedUncPath -PathType Container)) {
    throw "UNC path was not found or is not a folder: $normalizedUncPath"
}

foreach ($folderItem in Get-FolderQueue -RootPath $normalizedUncPath -MaximumDepth $MaxDepth -ExportErrors $exportErrors) {
    $folderPath = [string]$folderItem.Path
    $inventoryPath = Get-InventoryPath -FullPath $folderPath -RootPath $normalizedUncPath -RootShareName $ShareName
    $parentInventoryPath = Get-ParentInventoryPath -InventoryPath $inventoryPath -RootShareName $ShareName

    try {
        $acl = Get-Acl -LiteralPath $folderPath -ErrorAction Stop
    }
    catch {
        Add-ExportError -ExportErrors $exportErrors -Path $folderPath -Stage 'get_acl' -Message $_.Exception.Message
        Write-VerboseLog "Could not read ACL: $folderPath"
        continue
    }

    $inheritanceEnabled = -not $acl.AreAccessRulesProtected
    $folders.Add([ordered]@{
        share_unc_path = $normalizedUncPath
        path = $inventoryPath
        parent_path = $parentInventoryPath
        inheritance_enabled = $inheritanceEnabled
    })
    $processedFolders++

    foreach ($rule in $acl.Access) {
        if (-not $IncludeInherited -and $rule.IsInherited) {
            continue
        }

        $identityName = [string]$rule.IdentityReference
        $identitySid = Convert-IdentityToSid -Identity $rule.IdentityReference

        $aclEntries.Add([ordered]@{
            share_unc_path = $normalizedUncPath
            folder_path = $inventoryPath
            identity_sid = $identitySid
            identity_name = $identityName
            identity_type = 'unknown'
            rights = [string]$rule.FileSystemRights
            access_type = Convert-AccessType -AccessControlType $rule.AccessControlType
            inherited = [bool]$rule.IsInherited
            inheritance_flags = [string]$rule.InheritanceFlags
            propagation_flags = [string]$rule.PropagationFlags
            source = 'powershell'
        })
        $processedAclEntries++
    }

    if (($processedFolders % 100) -eq 0) {
        Write-Log "Processed folders: $processedFolders; ACL entries: $processedAclEntries; errors: $($exportErrors.Count)"
    }
    else {
        Write-VerboseLog "Processed folder: $inventoryPath"
    }
}

$result = [ordered]@{
    schema_version = 1
    generated_at = (Get-Date).ToUniversalTime().ToString('o')
    source = 'Export-FileServerAcl.ps1'
    parameters = [ordered]@{
        file_server_name = $FileServerName
        share_name = $ShareName
        unc_path = $normalizedUncPath
        max_depth = $MaxDepth
        include_inherited = [bool]$IncludeInherited
    }
    file_servers = @(
        [ordered]@{
            name = $FileServerName
            fqdn = $fqdn
            description = 'Exported by Export-FileServerAcl.ps1'
        }
    )
    shares = @(
        [ordered]@{
            file_server = $FileServerName
            name = $ShareName
            unc_path = $normalizedUncPath
            description = 'Exported by Export-FileServerAcl.ps1'
        }
    )
    folders = $folders.ToArray()
    acl_entries = $aclEntries.ToArray()
    errors = $exportErrors.ToArray()
}

$outputDirectory = Split-Path -Parent $OutputPath
if (-not [string]::IsNullOrWhiteSpace($outputDirectory) -and -not (Test-Path -LiteralPath $outputDirectory)) {
    New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
}

$json = $result | ConvertTo-Json -Depth 8
$json | Set-Content -LiteralPath $OutputPath -Encoding UTF8

$duration = New-TimeSpan -Start $startedAt -End (Get-Date)
Write-Log "Export finished."
Write-Log "Folders exported: $processedFolders"
Write-Log "ACL entries exported: $processedAclEntries"
Write-Log "Errors captured: $($exportErrors.Count)"
Write-Log "Output: $OutputPath"
Write-Log "Duration: $([math]::Round($duration.TotalSeconds, 2)) seconds"
