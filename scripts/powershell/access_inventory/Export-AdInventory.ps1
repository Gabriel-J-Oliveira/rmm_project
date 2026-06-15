<#
.SYNOPSIS
Exports Active Directory inventory to JSON for Django access_inventory.

.DESCRIPTION
Uses the ActiveDirectory PowerShell module when available to collect OUs, users,
groups, and direct group memberships. The output JSON is compatible with
POST /api/access-inventory/agent/ad-inventory/ and the import_ad_inventory command.

Compatible with Windows PowerShell 5.1.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$OutputPath,

    [Parameter(Mandatory = $false)]
    [string]$Domain = '',

    [Parameter(Mandatory = $false)]
    [string]$SearchBase = '',

    [Parameter(Mandatory = $false)]
    [switch]$IncludeDisabledUsers,

    [Parameter(Mandatory = $false)]
    [switch]$SkipGroupMemberships,

    [Parameter(Mandatory = $false)]
    [switch]$VerboseSkippedMembers,

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

function Get-ParentDistinguishedName {
    param(
        [Parameter(Mandatory = $true)]
        [string]$DistinguishedName
    )

    $index = $DistinguishedName.IndexOf(',')
    if ($index -lt 0 -or $index -ge ($DistinguishedName.Length - 1)) {
        return ''
    }
    return $DistinguishedName.Substring($index + 1)
}

function Get-ObjectSidValue {
    param(
        [Parameter(Mandatory = $false)]
        [object]$ObjectSid
    )

    if ($null -eq $ObjectSid) {
        return ''
    }

    try {
        if ($ObjectSid -is [System.Security.Principal.SecurityIdentifier]) {
            return $ObjectSid.Value
        }
        return ([System.Security.Principal.SecurityIdentifier]$ObjectSid).Value
    }
    catch {
        return [string]$ObjectSid
    }
}

function Add-ExportError {
    param(
        [System.Collections.Generic.List[object]]$ExportErrors,

        [Parameter(Mandatory = $true)]
        [string]$Stage,

        [Parameter(Mandatory = $true)]
        [string]$Message,

        [Parameter(Mandatory = $false)]
        [string]$ObjectName = ''
    )

    $ExportErrors.Add([ordered]@{
        stage = $Stage
        object = $ObjectName
        message = $Message
    })
}

function Write-JsonNoBom {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Data,

        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $directory = Split-Path -Parent $Path
    if (-not [string]::IsNullOrWhiteSpace($directory) -and -not (Test-Path -LiteralPath $directory)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }

    $json = $Data | ConvertTo-Json -Depth 8
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    $resolvedPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path)
    [System.IO.File]::WriteAllText($resolvedPath, $json, $utf8NoBom)
}

function Build-AdCommandParams {
    param(
        [Parameter(Mandatory = $false)]
        [string]$Server,

        [Parameter(Mandatory = $false)]
        [string]$Base
    )

    $params = @{}
    if (-not [string]::IsNullOrWhiteSpace($Server)) {
        $params.Server = $Server
    }
    if (-not [string]::IsNullOrWhiteSpace($Base)) {
        $params.SearchBase = $Base
    }
    return $params
}

$startedAt = Get-Date
$errors = New-Object 'System.Collections.Generic.List[object]'
$ous = New-Object 'System.Collections.Generic.List[object]'
$users = New-Object 'System.Collections.Generic.List[object]'
$groups = New-Object 'System.Collections.Generic.List[object]'
$memberships = New-Object 'System.Collections.Generic.List[object]'
$userByDn = @{}
$groupByDn = @{}
$unsupportedMembersSkipped = 0

Write-Log "Starting Active Directory inventory export."
if ($Domain) {
    Write-Log "Domain/server: $Domain"
}
if ($SearchBase) {
    Write-Log "Search base: $SearchBase"
}
Write-Log "Include disabled users: $($IncludeDisabledUsers.IsPresent)"
Write-Log "Collect group memberships: $(-not $SkipGroupMemberships.IsPresent)"

try {
    Import-Module ActiveDirectory -ErrorAction Stop
}
catch {
    throw "ActiveDirectory module is not available. Install RSAT Active Directory tools or run on a domain controller. $($_.Exception.Message)"
}

$baseParams = Build-AdCommandParams -Server $Domain -Base $SearchBase

try {
    $adOus = Get-ADOrganizationalUnit @baseParams -Filter * -Properties DistinguishedName,Name -ErrorAction Stop
    foreach ($ou in $adOus) {
        $ous.Add([ordered]@{
            distinguished_name = [string]$ou.DistinguishedName
            name = [string]$ou.Name
            parent_distinguished_name = Get-ParentDistinguishedName -DistinguishedName ([string]$ou.DistinguishedName)
        })
    }
    Write-Log "OUs collected: $($ous.Count)"
}
catch {
    Add-ExportError -ExportErrors $errors -Stage 'ous' -Message $_.Exception.Message
}

try {
    $userFilter = if ($IncludeDisabledUsers) { '*' } else { 'Enabled -eq $true' }
    $adUsers = Get-ADUser @baseParams -Filter $userFilter -Properties ObjectSid,SamAccountName,DisplayName,UserPrincipalName,EmailAddress,DistinguishedName,Enabled -ErrorAction Stop
    foreach ($user in $adUsers) {
        $userRow = [ordered]@{
            sid = Get-ObjectSidValue -ObjectSid $user.ObjectSid
            sam_account_name = [string]$user.SamAccountName
            display_name = [string]$user.DisplayName
            user_principal_name = [string]$user.UserPrincipalName
            email = [string]$user.EmailAddress
            distinguished_name = [string]$user.DistinguishedName
            ou_distinguished_name = Get-ParentDistinguishedName -DistinguishedName ([string]$user.DistinguishedName)
            enabled = [bool]$user.Enabled
        }
        $users.Add($userRow)
        if ($userRow.distinguished_name) {
            $userByDn[$userRow.distinguished_name.ToLowerInvariant()] = $userRow
        }
    }
    Write-Log "Users collected: $($users.Count)"
}
catch {
    Add-ExportError -ExportErrors $errors -Stage 'users' -Message $_.Exception.Message
}

try {
    $adGroups = Get-ADGroup @baseParams -Filter * -Properties ObjectSid,SamAccountName,Name,Description,DistinguishedName,member -ErrorAction Stop
    foreach ($group in $adGroups) {
        $groupRow = [ordered]@{
            sid = Get-ObjectSidValue -ObjectSid $group.ObjectSid
            sam_account_name = [string]$group.SamAccountName
            name = [string]$group.Name
            description = [string]$group.Description
            distinguished_name = [string]$group.DistinguishedName
            ou_distinguished_name = Get-ParentDistinguishedName -DistinguishedName ([string]$group.DistinguishedName)
            member_dns = @($group.member)
        }
        $groups.Add($groupRow)
        if ($groupRow.distinguished_name) {
            $groupByDn[$groupRow.distinguished_name.ToLowerInvariant()] = $groupRow
        }
    }
    Write-Log "Groups collected: $($groups.Count)"
}
catch {
    Add-ExportError -ExportErrors $errors -Stage 'groups' -Message $_.Exception.Message
}

if ($SkipGroupMemberships) {
    Write-Log "Group membership collection skipped by parameter."
}
else {
    foreach ($group in $groups) {
        foreach ($memberDn in @($group.member_dns)) {
            if ([string]::IsNullOrWhiteSpace([string]$memberDn)) {
                continue
            }

            $memberKey = ([string]$memberDn).ToLowerInvariant()
            if ($userByDn.ContainsKey($memberKey)) {
                $memberUser = $userByDn[$memberKey]
                $memberships.Add([ordered]@{
                    parent_group_sid = [string]$group.sid
                    member_user_sid = [string]$memberUser.sid
                })
            }
            elseif ($groupByDn.ContainsKey($memberKey)) {
                $memberGroup = $groupByDn[$memberKey]
                $memberships.Add([ordered]@{
                    parent_group_sid = [string]$group.sid
                    member_group_sid = [string]$memberGroup.sid
                })
            }
            else {
                $unsupportedMembersSkipped++
                if ($VerboseSkippedMembers) {
                    Write-Log "Skipping unsupported or out-of-scope member '$memberDn' in group '$($group.name)'."
                }
            }
        }
    }
}
Write-Log "Group memberships collected: $($memberships.Count)"
Write-Log "Unsupported members skipped: $unsupportedMembersSkipped"

$result = [ordered]@{
    schema_version = 1
    generated_at = (Get-Date).ToUniversalTime().ToString('o')
    source = 'Export-AdInventory.ps1'
    parameters = [ordered]@{
        domain = $Domain
        search_base = $SearchBase
        include_disabled_users = [bool]$IncludeDisabledUsers
        skip_group_memberships = [bool]$SkipGroupMemberships
    }
    ous = $ous.ToArray()
    users = $users.ToArray()
    groups = @(
        foreach ($group in $groups) {
            [ordered]@{
                sid = $group.sid
                sam_account_name = $group.sam_account_name
                name = $group.name
                description = $group.description
                distinguished_name = $group.distinguished_name
                ou_distinguished_name = $group.ou_distinguished_name
            }
        }
    )
    memberships = $memberships.ToArray()
    errors = $errors.ToArray()
}

Write-JsonNoBom -Data $result -Path $OutputPath

$duration = New-TimeSpan -Start $startedAt -End (Get-Date)
Write-Log "Active Directory inventory export finished."
Write-Log "OUs collected: $($ous.Count)"
Write-Log "Users collected: $($users.Count)"
Write-Log "Groups collected: $($groups.Count)"
Write-Log "Group memberships collected: $($memberships.Count)"
Write-Log "Unsupported members skipped: $unsupportedMembersSkipped"
Write-Log "Errors captured: $($errors.Count)"
Write-Log "Output: $OutputPath"
Write-Log "Duration: $([math]::Round($duration.TotalSeconds, 2)) seconds"
