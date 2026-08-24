param(
    [string]$ScriptsPath = $PSScriptRoot,
    [string]$BuildScriptPath = (Join-Path (Join-Path $PSScriptRoot "..\..") "scripts\Build-NightOwlAgentRelease.ps1")
)

$ErrorActionPreference = "Stop"

function Assert-True($Condition, [string]$Message) {
    if (-not $Condition) {
        throw $Message
    }
}

function Assert-ParseOk([string]$Path) {
    $errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path $Path), [ref]$null, [ref]$errors) | Out-Null
    if ($errors.Count -gt 0) {
        throw ("Parser errors in {0}: {1}" -f $Path, ($errors | ForEach-Object { $_.Message } | Out-String))
    }
}

$installer = Join-Path $ScriptsPath "Install-NightOwlAgentDotNet.ps1"
$build = Resolve-Path $BuildScriptPath

Assert-True (Test-Path $installer) "Installer script not found."
Assert-True (Test-Path $build) "Build script not found."
Assert-ParseOk $installer
Assert-ParseOk $build

$installerText = Get-Content -Path $installer -Raw
$buildText = Get-Content -Path $build -Raw

foreach ($required in @(
    "[string]`$TrustedPublicKeysPath",
    "[switch]`$AllowReleaseBundledTrustForLab",
    "Resolve-TrustedPublicKeysForInstaller",
    "INSTALL_TRUST_BOOTSTRAP_REQUIRED",
    "INSTALL_TRUST_BOOTSTRAP_MISSING",
    "TrustBundlePath",
    "LAB INSEGURO",
    "Assert-DownloadedPackageTrust",
    "INSTALL_SIGNING_KEY_REVOKED",
    "INSTALL_SIGNATURE_INVALID"
)) {
    Assert-True ($installerText.Contains($required)) "Installer missing trust bootstrap marker: $required"
}

$releaseKeyDownload = 'Invoke-WebRequest -Uri ($baseUrl + "/release-public-keys.json")'
$releaseKeyDownloadIndex = $installerText.IndexOf($releaseKeyDownload, [StringComparison]::Ordinal)
Assert-True ($releaseKeyDownloadIndex -ge 0) "Installer should support explicit lab download of release-public-keys.json."
$flagBeforeDownload = $installerText.LastIndexOf('if ($AllowReleaseBundledTrustForLab)', $releaseKeyDownloadIndex, [StringComparison]::Ordinal)
Assert-True ($flagBeforeDownload -ge 0) "release-public-keys.json from release must only be downloaded inside AllowReleaseBundledTrustForLab block."
Assert-True (-not $installerText.Contains('Invoke-WebRequest -Uri ($baseUrl + "/release-public-keys.json") -OutFile $trustedKeysPath')) "Installer must not treat release-public-keys.json from same release as default trust anchor."
Assert-True ($installerText.Contains('return $TrustedPublicKeysPath')) "Explicit trusted path should be accepted."
Assert-True ($installerText.Contains('return $localTrust')) "Existing local trust bundle should be preferred."
Assert-True ($installerText.Contains('return $ReleaseBundledTrustPath')) "Release-bundled trust should require explicit lab flag."

$requiredPublicLatest = @(
    "NightOwl.Agent.Windows.zip",
    "checksums.json",
    "release-manifest.json",
    "release-manifest.sig",
    "release-public-keys.json",
    "version.json",
    "Install-NightOwlAgentDotNet.ps1",
    "Uninstall-NightOwlAgentDotNet.ps1",
    "NightOwl.ico"
)
foreach ($required in $requiredPublicLatest) {
    Assert-True ($buildText.Contains($required)) "Build script public artifacts missing marker: $required"
}
Assert-True ($buildText.Contains("test -s '`$remoteTemp/release-manifest.sig'")) "Remote upload validation must require release-manifest.sig."
Assert-True ($buildText.Contains("cp '`$remoteRelease/release-manifest.sig' '`$DestinationRoot/release-manifest.sig'")) "Remote latest publication must copy release-manifest.sig."
Assert-True ($buildText.Contains('"release-manifest.sig", "release-public-keys.json"')) "Local latest publication must include release-manifest.sig before release-public-keys.json."

Write-Host "NightOwl installer trust bootstrap tests passed."
