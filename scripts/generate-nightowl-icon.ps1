param(
    [string]$ApprovedIconRoot = ".\assets\nightowl\icon - novo",
    [string]$OutputRoot = ".\assets\icons"
)

$ErrorActionPreference = "Stop"

function Resolve-FullPath([string]$Path) {
    $executionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path)
}

$sourceRoot = Resolve-FullPath $ApprovedIconRoot
$outputRootPath = Resolve-FullPath $OutputRoot
$sourceIco = Join-Path $sourceRoot "NightOwl.ico"

if (-not (Test-Path -LiteralPath $sourceIco)) {
    throw "Icone aprovado nao encontrado em: $sourceIco"
}

New-Item -ItemType Directory -Force -Path $outputRootPath | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $outputRootPath "source") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $outputRootPath "png") | Out-Null

Copy-Item -LiteralPath $sourceIco -Destination (Join-Path $outputRootPath "NightOwl.ico") -Force

$sourcePreview = Join-Path $sourceRoot "NightOwl-icon-preview.png"
if (Test-Path -LiteralPath $sourcePreview) {
    Copy-Item -LiteralPath $sourcePreview -Destination (Join-Path $outputRootPath "source\NightOwl-icon-preview.png") -Force
}

$sourceMaster = Join-Path $sourceRoot "NightOwl-icon-source-1024.png"
if (Test-Path -LiteralPath $sourceMaster) {
    Copy-Item -LiteralPath $sourceMaster -Destination (Join-Path $outputRootPath "source\NightOwl-icon-source-1024.png") -Force
}

$sourcePngDir = Join-Path $sourceRoot "png"
if (Test-Path -LiteralPath $sourcePngDir) {
    Get-ChildItem -LiteralPath $sourcePngDir -Filter "*.png" | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $outputRootPath "png") -Force
    }
}

Write-Host "NightOwl icon assets synchronized from approved source."
Write-Host "ICO: $(Join-Path $outputRootPath 'NightOwl.ico')"
