$ErrorActionPreference = 'Stop'
$path = (Resolve-Path (Join-Path $PSScriptRoot '..\NightOwl.Agent.Windows\scripts\Install-NightOwlAgentDotNet.ps1')).Path
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($path, [ref]$null, [ref]$errors)
if ($errors.Count) { throw 'Invalid installer syntax' }
$fn = $ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Download-AgentPackage'}, $true)
Invoke-Expression $fn.Extent.Text
$root = Join-Path ([IO.Path]::GetTempPath()) ('nightowl-fi01b-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root | Out-Null
$cache = Join-Path $root 'cache'; New-Item -ItemType Directory -Path $cache | Out-Null
$script:NightOwlPaths = @{ Cache = $cache }
$fixture = Join-Path $root 'fixture.zip'; [IO.File]::WriteAllText($fixture, 'synthetic-zip')
$ExpectedPackageSha256 = (Get-FileHash $fixture -Algorithm SHA256).Hash.ToLowerInvariant()
$AllowInsecureTls = $false; $AllowReleaseBundledTrustForLab = $false
$script:downloads = 0; $script:failDownload = $false; $script:failSignature = $false
function Enable-InsecureTlsForLab {}
function Write-Step($a, $b) {}
function Get-FileSha256($p) { (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLowerInvariant() }
function Get-UrlDirectory($u) { 'https://example.invalid' }
function Read-JsonFile($p) { return @{} }
function Get-ChecksumFromManifest($a, $b) { return $ExpectedPackageSha256 }
function Resolve-TrustedPublicKeysForInstaller { param($ReleaseBundledTrustPath) return 'trusted' }
function Assert-RequiredPackageFiles { param($SourceDir, [switch]$RequireTrust) }
function Expand-Archive { param($Path, $DestinationPath, [switch]$Force) New-Item -ItemType Directory -Force -Path $DestinationPath | Out-Null; [IO.File]::WriteAllText((Join-Path $DestinationPath 'NightOwl.Agent.Windows.exe'), 'fixture') }
function Assert-DownloadedPackageTrust { param($ExtractPath, $ZipPath, $ManifestPath, $SignaturePath, $TrustedKeysPath) if ($script:failSignature) { throw 'synthetic invalid signature' }; return @{ valid = $true } }
function Invoke-WebRequest {
    param($Uri, $OutFile, [switch]$UseBasicParsing, $TimeoutSec)
    if ($Uri -like '*.zip') { $script:downloads++; if ($script:failDownload) { throw 'synthetic download failure' }; Copy-Item -LiteralPath $fixture -Destination $OutFile }
    else { [IO.File]::WriteAllText($OutFile, 'metadata') }
}
try {
    $url = 'https://example.invalid/NightOwl.Agent.Windows.zip'
    Download-AgentPackage $url (Join-Path $root 'first') | Out-Null
    Download-AgentPackage $url (Join-Path $root 'second') | Out-Null
    if ($script:downloads -ne 1) { throw 'Cache hit performed a download' }
    $cached = Join-Path $cache ('installer-' + $ExpectedPackageSha256 + '.zip')
    [IO.File]::WriteAllText($cached, 'corrupt')
    $script:failDownload = $true
    try { Download-AgentPackage $url (Join-Path $root 'failed') | Out-Null; throw 'Download failure accepted' } catch { if ($_.Exception.Message -eq 'Download failure accepted') { throw } }
    $script:failDownload = $false; $script:failSignature = $true
    try { Download-AgentPackage $url (Join-Path $root 'bad-signature') | Out-Null; throw 'Invalid signature accepted' } catch { if ($_.Exception.Message -eq 'Invalid signature accepted') { throw } }
    if ([IO.File]::ReadAllText($cached) -ne 'corrupt') { throw 'Invalid signature published' }
    $script:failSignature = $false
    Download-AgentPackage $url (Join-Path $root 'replacement') | Out-Null
    if ((Get-FileSha256 $cached) -ne $ExpectedPackageSha256) { throw 'Cache replacement failed' }
    $mutex = New-Object System.Threading.Mutex($false, 'Global\NightOwlInstallerPackageCache')
    if (-not $mutex.WaitOne(5000)) { throw 'Unable to acquire test mutex' }
    try {
        $job = Start-Job -ScriptBlock {
            $other = New-Object System.Threading.Mutex($false, 'Global\NightOwlInstallerPackageCache')
            try { $acquired = $other.WaitOne(500); if ($acquired) { $other.ReleaseMutex() }; return $acquired } finally { $other.Dispose() }
        }
        if (-not (Wait-Job $job -Timeout 15)) { throw 'Concurrent mutex test timed out' }
        if (Receive-Job $job) { throw 'Concurrent worker acquired held mutex' }
        Remove-Job $job
    } finally { $mutex.ReleaseMutex(); $mutex.Dispose() }
    Write-Host 'PASS: cache hit, corrupt cache replacement, download failure, simulated signature rejection, cross-process mutex.'
}
finally {
    if ((Split-Path -Parent $root) -eq ([IO.Path]::GetTempPath().TrimEnd('\')) -and (Split-Path -Leaf $root) -like 'nightowl-fi01b-*') { Remove-Item -LiteralPath $root -Recurse -Force }
}
