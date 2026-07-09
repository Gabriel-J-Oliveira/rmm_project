[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$SourcePath = "\\192.168.104.120\controlsul\Comum\_Agents",
    [string]$InstallPath = "C:\RMM",
    [string]$ProgramDataPath = "C:\ProgramData\NightOwl",
    [switch]$RunCheck,
    [switch]$RunAfterUpdate,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$LogDirectory = Join-Path $InstallPath "logs"
$UpdateLog = Join-Path $LogDirectory "update.log"
$StatePath = Join-Path $InstallPath "agent.state.json"

function Write-UpdateLog {
    param([string]$Message, [string]$Level = "INFO")
    if (-not (Test-Path $LogDirectory)) {
        New-Item -Path $LogDirectory -ItemType Directory -Force | Out-Null
    }
    $timestamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    Add-Content -Path $UpdateLog -Value "[$timestamp] [$Level] $Message"
    Write-Host "[$Level] $Message"
}

function Read-VersionFromPath {
    param([string]$Path)
    $versionPath = Join-Path $Path "VERSION"
    if (Test-Path $versionPath) {
        return (Get-Content -Path $versionPath -Raw).Trim()
    }
    $manifestPath = Join-Path $Path "manifest.json"
    if (Test-Path $manifestPath) {
        try { return (Get-Content -Path $manifestPath -Raw | ConvertFrom-Json).version } catch { return "" }
    }
    return ""
}

function Read-State {
    if (-not (Test-Path $StatePath)) { return @{} }
    try {
        $object = Get-Content -Path $StatePath -Raw | ConvertFrom-Json
        $state = @{}
        foreach ($property in $object.PSObject.Properties) {
            $state[$property.Name] = $property.Value
        }
        return $state
    }
    catch {
        return @{}
    }
}

function Write-State {
    param([hashtable]$State)
    $State | ConvertTo-Json -Depth 6 | Set-Content -Path $StatePath -Encoding UTF8
}

function Set-StateValue {
    param([hashtable]$State, [string]$Status, [string]$ErrorMessage = "")
    $State["update_source"] = $SourcePath
    $State["package_manifest_path"] = Join-Path $SourcePath "manifest.json"
    $State["last_update_check_at"] = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    $State["last_update_status"] = $Status
    $State["last_update_error"] = $ErrorMessage
    $State["local_version"] = Read-VersionFromPath -Path $InstallPath
    return $State
}

function Test-ReadableFile {
    param([string]$Path)
    try {
        Get-Content -Path $Path -TotalCount 1 -ErrorAction Stop | Out-Null
        return $true
    }
    catch {
        throw "Package file is not readable: $Path :: $($_.Exception.Message)"
    }
}

function Copy-PackageFile {
    param([string]$FileName)
    $source = Join-Path $SourcePath $FileName
    $destination = Join-Path $InstallPath $FileName
    if (-not (Test-Path $source)) {
        Write-UpdateLog "Skipping missing package file: $source" "WARN"
        return $false
    }
    Test-ReadableFile -Path $source | Out-Null
    $sourceFull = [System.IO.Path]::GetFullPath($source)
    $destinationFull = [System.IO.Path]::GetFullPath($destination)
    if ([string]::Equals($sourceFull, $destinationFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        Write-UpdateLog "Skipping same source/destination: $destinationFull"
        return $false
    }
    if ($PSCmdlet.ShouldProcess($destinationFull, "Copy $FileName from package source")) {
        $destinationDirectory = Split-Path -Path $destinationFull -Parent
        if ($destinationDirectory -and -not (Test-Path $destinationDirectory)) {
            New-Item -Path $destinationDirectory -ItemType Directory -Force | Out-Null
        }
        Copy-Item -Path $sourceFull -Destination $destinationFull -Force
        Write-UpdateLog "Copied $FileName"
        return $true
    }
    return $false
}

function Copy-PackageAssets {
    $sourceAssets = Join-Path $SourcePath "assets"
    $destinationAssets = Join-Path $InstallPath "assets"
    if (-not (Test-Path $sourceAssets)) {
        Write-UpdateLog "Skipping missing assets directory: $sourceAssets" "WARN"
        return $false
    }
    if ($PSCmdlet.ShouldProcess($destinationAssets, "Copy package assets directory")) {
        if (-not (Test-Path $destinationAssets)) {
            New-Item -Path $destinationAssets -ItemType Directory -Force | Out-Null
        }
        Copy-Item -Path (Join-Path $sourceAssets "*") -Destination $destinationAssets -Recurse -Force
        Write-UpdateLog "Copied assets directory"
        return $true
    }
    return $false
}

try {
    if (-not (Test-Path $SourcePath)) {
        throw "SourcePath not accessible: $SourcePath"
    }
    if (-not (Test-Path $InstallPath)) {
        New-Item -Path $InstallPath -ItemType Directory -Force | Out-Null
    }
    if (-not (Test-Path $LogDirectory)) {
        New-Item -Path $LogDirectory -ItemType Directory -Force | Out-Null
    }

    $state = Read-State
    $localVersion = Read-VersionFromPath -Path $InstallPath
    $centralVersion = Read-VersionFromPath -Path $SourcePath
    Write-UpdateLog "Local version: $localVersion; central version: $centralVersion"

    $files = @(
        "RmmAgent.ps1",
        "Install-RmmAgent.ps1",
        "Update-RmmAgent.ps1",
        "Uninstall-RmmAgent.ps1",
        "Check-RmmAgent.ps1",
        "NightOwlManualValidation.ps1",
        "assets\nightowl-logo.png",
        "RmmAgent.config.json.example",
        "RmmAgentService.ps1",
        "Install-RmmAgentService.ps1",
        "Uninstall-RmmAgentService.ps1",
        "VERSION",
        "manifest.json",
        "README.md"
    )

    $missingLocal = $false
    foreach ($file in $files) {
        if (-not (Test-Path (Join-Path $InstallPath $file))) {
            $missingLocal = $true
        }
    }

    if (-not $Force -and -not $missingLocal -and $localVersion -and $centralVersion -and $localVersion -eq $centralVersion) {
        Write-UpdateLog "Local package is already up to date."
        $state = Set-StateValue -State $state -Status "up_to_date"
        Write-State -State $state
    }
    else {
        $copied = 0
        foreach ($file in $files) {
            if (Copy-PackageFile -FileName $file) {
                $copied++
            }
        }
        if (Copy-PackageAssets) {
            $copied++
        }

        $agentScript = Join-Path $InstallPath "RmmAgent.ps1"
        if (-not (Test-Path $agentScript)) {
            throw "Local RmmAgent.ps1 missing after update."
        }
        $tokens = $null
        $errors = $null
        [System.Management.Automation.Language.Parser]::ParseFile($agentScript, [ref]$tokens, [ref]$errors) | Out-Null
        if ($errors.Count -gt 0) {
            throw "RmmAgent.ps1 syntax validation failed: $($errors[0].Message)"
        }

        $state = Set-StateValue -State $state -Status "success"
        Write-State -State $state
        Write-UpdateLog "Update complete. Files copied: $copied"
    }

    $programDataAgentPath = Join-Path $ProgramDataPath "Agent"
    if (Test-Path $programDataAgentPath) {
        foreach ($serviceFile in @("RmmAgentService.ps1", "Install-RmmAgentService.ps1", "Uninstall-RmmAgentService.ps1", "RmmAgent.config.json.example", "VERSION", "manifest.json", "README.md")) {
            $sourceServiceFile = Join-Path $SourcePath $serviceFile
            $destinationServiceFile = Join-Path $programDataAgentPath $serviceFile
            if (Test-Path $sourceServiceFile) {
                Copy-Item -Path $sourceServiceFile -Destination $destinationServiceFile -Force
                Write-UpdateLog "Updated service file in ProgramData: $serviceFile"
            }
        }
        Write-UpdateLog "Service ProgramData update completed while preserving config, logs and state."
    }

    if ($RunAfterUpdate) {
        powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $InstallPath "RmmAgent.ps1")
        Write-UpdateLog "RunAfterUpdate completed with exit code $LASTEXITCODE"
    }

    if ($RunCheck) {
        powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $InstallPath "Check-RmmAgent.ps1") -SourcePath $SourcePath -InstallPath $InstallPath
        Write-UpdateLog "RunCheck completed with exit code $LASTEXITCODE"
    }
}
catch {
    $state = Read-State
    $state = Set-StateValue -State $state -Status "failed" -ErrorMessage $_.Exception.Message
    Write-State -State $state
    Write-UpdateLog "Update failed: $($_.Exception.Message)" "ERROR"
    exit 1
}
