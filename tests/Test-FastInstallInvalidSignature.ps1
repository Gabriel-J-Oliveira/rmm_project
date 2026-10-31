$ErrorActionPreference = 'Stop'
$path = (Resolve-Path (Join-Path $PSScriptRoot '..\NightOwl.Agent.Windows\scripts\Install-NightOwlAgentDotNet.ps1')).Path
$errors = $null; $ast = [Management.Automation.Language.Parser]::ParseFile($path, [ref]$null, [ref]$errors)
if ($errors.Count) { throw 'Installer syntax invalid' }
$fn = $ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Assert-DownloadedPackageTrust'}, $true)
Invoke-Expression $fn.Extent.Text
$root = Join-Path ([IO.Path]::GetTempPath()) ('nightowl-signature-fi01b-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root | Out-Null
try {
    $zip = Join-Path $root 'package.zip'; [IO.File]::WriteAllText($zip, 'fixture')
    $manifest = Join-Path $root 'manifest.json'; [IO.File]::WriteAllText($manifest, '{}')
    $signature = Join-Path $root 'manifest.sig'; [IO.File]::WriteAllText($signature, [Convert]::ToBase64String((New-Object byte[] 256)))
    $script:hash = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
    function Read-JsonFile($p) { [pscustomobject]@{key_id='fixture';package=[pscustomobject]@{sha256=$script:hash;size=7};required_zip_entries=@()} }
    function Get-FileSha256($p) { (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLowerInvariant() }
    function Get-TrustedReleaseKey { param($TrustedKeysPath,$KeyId) return 'fixture' }
    function New-RsaPssPublicKeyFromXmlText { param($Xml,$KeyId) return (New-Object System.Security.Cryptography.RSACng(2048)) }
    try {
        Assert-DownloadedPackageTrust -ExtractPath $root -ZipPath $zip -ManifestPath $manifest -SignaturePath $signature -TrustedKeysPath 'fixture' | Out-Null
        throw 'Invalid signature accepted'
    } catch { if ($_.Exception.Message -notmatch '^INSTALL_SIGNATURE_INVALID') { throw } }
    Write-Host 'PASS: real RSA-PSS verifier rejects an invalid signature.'
} finally {
    if ((Split-Path -Parent $root) -eq ([IO.Path]::GetTempPath().TrimEnd('\')) -and (Split-Path -Leaf $root) -like 'nightowl-signature-fi01b-*') { Remove-Item -LiteralPath $root -Recurse -Force }
}
