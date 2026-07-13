param(
    [string]$SourcePng = ".\assets\nightowl\icon - novo\NightOwl-icon-source-1024.png",
    [string]$OutputRoot = ".\assets\icons",
    [string]$ApprovedRoot = ".\assets\nightowl\icon - novo",
    [string]$LegacyRoot = ".\assets\nightowl\icons",
    [int[]]$Sizes = @(16, 20, 24, 32, 40, 48, 64, 128, 256)
)

$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Drawing

function Resolve-FullPath([string]$Path) {
    $executionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path)
}

function Save-TransparentPng([System.Drawing.Image]$Source, [int]$Size, [string]$Path) {
    $bitmap = [System.Drawing.Bitmap]::new($Size, $Size, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    try {
        $graphics.Clear([System.Drawing.Color]::Transparent)
        $graphics.CompositingMode = [System.Drawing.Drawing2D.CompositingMode]::SourceOver
        $graphics.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality
        $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
        $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
        $graphics.DrawImage($Source, 0, 0, $Size, $Size)
        $bitmap.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
    }
    finally {
        $graphics.Dispose()
        $bitmap.Dispose()
    }
}

function Write-IcoFromPngs([string[]]$PngPaths, [string]$IcoPath) {
    $orderedPaths = $PngPaths | Sort-Object {
        $file = [System.IO.Path]::GetFileNameWithoutExtension($_)
        if ($file -match '(\d+)$') { [int]$Matches[1] } else { 0 }
    }
    $pngBytes = @($orderedPaths | ForEach-Object { ,[System.IO.File]::ReadAllBytes($_) })
    $count = [uint16]$pngBytes.Count
    $headerSize = 6
    $entrySize = 16
    $offset = $headerSize + ($entrySize * $count)

    $stream = [System.IO.File]::Create($IcoPath)
    $writer = [System.IO.BinaryWriter]::new($stream)
    try {
        $writer.Write([uint16]0)
        $writer.Write([uint16]1)
        $writer.Write($count)

        for ($i = 0; $i -lt $orderedPaths.Count; $i++) {
            $imagePath = $orderedPaths[$i]
            $size = [int](([System.IO.Path]::GetFileNameWithoutExtension($imagePath) -replace '\D+', ''))
            $dimensionByte = if ($size -ge 256) { [byte]0 } else { [byte]$size }
            $writer.Write($dimensionByte)
            $writer.Write($dimensionByte)
            $writer.Write([byte]0)
            $writer.Write([byte]0)
            $writer.Write([uint16]1)
            $writer.Write([uint16]32)
            $writer.Write([uint32]$pngBytes[$i].Length)
            $writer.Write([uint32]$offset)
            $offset += $pngBytes[$i].Length
        }

        foreach ($bytes in $pngBytes) {
            $writer.Write($bytes)
        }
    }
    finally {
        $writer.Dispose()
        $stream.Dispose()
    }
}

function Copy-GeneratedSet([string]$FromRoot, [string]$ToRoot) {
    New-Item -ItemType Directory -Force -Path $ToRoot | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $ToRoot "png") | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $ToRoot "source") | Out-Null
    Copy-Item -LiteralPath (Join-Path $FromRoot "NightOwl.ico") -Destination (Join-Path $ToRoot "NightOwl.ico") -Force
    Copy-Item -LiteralPath (Join-Path $FromRoot "source\NightOwl-icon-source-1024.png") -Destination (Join-Path $ToRoot "source\NightOwl-icon-source-1024.png") -Force
    Copy-Item -LiteralPath (Join-Path $FromRoot "source\NightOwl-icon-preview.png") -Destination (Join-Path $ToRoot "source\NightOwl-icon-preview.png") -Force
    Get-ChildItem -LiteralPath (Join-Path $FromRoot "png") -Filter "*.png" | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $ToRoot "png") -Force
    }
}

$sourcePath = Resolve-FullPath $SourcePng
$outputRootPath = Resolve-FullPath $OutputRoot
$approvedRootPath = Resolve-FullPath $ApprovedRoot
$legacyRootPath = Resolve-FullPath $LegacyRoot

if (-not (Test-Path -LiteralPath $sourcePath)) {
    throw "PNG fonte nao encontrado: $sourcePath"
}

New-Item -ItemType Directory -Force -Path $outputRootPath | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $outputRootPath "png") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $outputRootPath "source") | Out-Null

$sourceImage = [System.Drawing.Bitmap]::new($sourcePath)
try {
    if (-not [System.Drawing.Image]::IsAlphaPixelFormat($sourceImage.PixelFormat)) {
        throw "PNG fonte nao possui canal alpha. Use um arquivo com fundo transparente."
    }

    foreach ($size in $Sizes) {
        Save-TransparentPng -Source $sourceImage -Size $size -Path (Join-Path $outputRootPath ("png\nightowl-{0}.png" -f $size))
    }

    Copy-Item -LiteralPath $sourcePath -Destination (Join-Path $outputRootPath "source\NightOwl-icon-source-1024.png") -Force
    Save-TransparentPng -Source $sourceImage -Size 256 -Path (Join-Path $outputRootPath "source\NightOwl-icon-preview.png")
}
finally {
    $sourceImage.Dispose()
}

$pngPaths = @($Sizes | ForEach-Object { Join-Path $outputRootPath ("png\nightowl-{0}.png" -f $_) })
Write-IcoFromPngs -PngPaths $pngPaths -IcoPath (Join-Path $outputRootPath "NightOwl.ico")

Copy-GeneratedSet -FromRoot $outputRootPath -ToRoot $approvedRootPath

$legacyIcoRoot = Join-Path $legacyRootPath "ico"
New-Item -ItemType Directory -Force -Path $legacyIcoRoot | Out-Null
Copy-Item -LiteralPath (Join-Path $outputRootPath "NightOwl.ico") -Destination (Join-Path $legacyIcoRoot "NightOwl.ico") -Force
Copy-GeneratedSet -FromRoot $outputRootPath -ToRoot $legacyRootPath

Write-Host "NightOwl transparent icon assets generated."
Write-Host "Source: $sourcePath"
Write-Host "Canonical ICO: $(Join-Path $outputRootPath 'NightOwl.ico')"
