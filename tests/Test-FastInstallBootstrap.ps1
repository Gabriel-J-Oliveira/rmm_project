$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot '..\agents\bootstrap\nightowl_deployment_bootstrap.ps1'
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path $source), [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Bootstrap syntax invalid' }
foreach ($name in @('Get-Sha256', 'Get-VerifiedTrustBundle')) {
    $node = $ast.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }, $true)
    if (-not $node) { throw "Function missing: $name" }
    Invoke-Expression $node.Extent.Text
}
$root = Join-Path ([IO.Path]::GetTempPath()) ('nightowl-fi01-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root | Out-Null
$script:downloads = 0; $script:failDownload = $false
function Invoke-WebRequest {
    param($Uri, $OutFile, [switch]$UseBasicParsing)
    $script:downloads++
    if ($script:failDownload) { throw 'synthetic download failure' }
    [IO.File]::WriteAllText($OutFile, 'synthetic-trust')
}
try {
    $fixture = Join-Path $root 'fixture'; [IO.File]::WriteAllText($fixture, 'synthetic-trust')
    $expected = Get-Sha256 $fixture
    $trust = Join-Path $root 'trust.json'
    $config = Join-Path $root 'identity.json'; [IO.File]::WriteAllText($config, 'identity-preserved')
    Get-VerifiedTrustBundle -Url 'https://example.invalid/trust' -Path $trust -ExpectedSha $expected
    if ($script:downloads -ne 1 -or (Get-Sha256 $trust) -ne $expected) { throw 'Fresh download failed' }
    Get-VerifiedTrustBundle -Url 'https://example.invalid/trust' -Path $trust -ExpectedSha $expected
    if ($script:downloads -ne 1) { throw 'Valid cache downloaded again' }
    [IO.File]::WriteAllText($trust, 'corrupt')
    $script:failDownload = $true
    try { Get-VerifiedTrustBundle -Url 'https://example.invalid/trust' -Path $trust -ExpectedSha $expected; throw 'Download failure accepted' }
    catch { if ($_.Exception.Message -eq 'Download failure accepted') { throw } }
    if ([IO.File]::ReadAllText($config) -ne 'identity-preserved') { throw 'Identity modified' }
    if (@(Get-ChildItem -LiteralPath $root -Filter '*.partial').Count) { throw 'Partial download left behind' }
    $script:failDownload = $false
    Get-VerifiedTrustBundle -Url 'https://example.invalid/trust' -Path $trust -ExpectedSha $expected
    if ((Get-Sha256 $trust) -ne $expected -or $script:downloads -ne 3) { throw 'Corrupt cache not replaced' }
    Write-Host 'PASS: fresh trust download, verified cache hit, failed replacement, identity preservation.'
}
finally {
    if ((Split-Path -Parent $root) -eq ([IO.Path]::GetTempPath().TrimEnd('\')) -and (Split-Path -Leaf $root) -like 'nightowl-fi01-test-*') {
        Remove-Item -LiteralPath $root -Recurse -Force
    }
}
