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

function Get-AlphaBounds([System.Drawing.Bitmap]$Source, [int]$Threshold = 12, [int]$Margin = 4) {
    $minX = $Source.Width
    $minY = $Source.Height
    $maxX = -1
    $maxY = -1
    for ($y = 0; $y -lt $Source.Height; $y++) {
        for ($x = 0; $x -lt $Source.Width; $x++) {
            if ($Source.GetPixel($x, $y).A -gt $Threshold) {
                if ($x -lt $minX) { $minX = $x }
                if ($y -lt $minY) { $minY = $y }
                if ($x -gt $maxX) { $maxX = $x }
                if ($y -gt $maxY) { $maxY = $y }
            }
        }
    }

    if ($maxX -lt 0 -or $maxY -lt 0) {
        throw "PNG fonte nao contem pixels visiveis."
    }

    $minX = [Math]::Max(0, $minX - $Margin)
    $minY = [Math]::Max(0, $minY - $Margin)
    $maxX = [Math]::Min($Source.Width - 1, $maxX + $Margin)
    $maxY = [Math]::Min($Source.Height - 1, $maxY + $Margin)
    return [System.Drawing.Rectangle]::FromLTRB($minX, $minY, $maxX + 1, $maxY + 1)
}

function New-ScaledForeground([System.Drawing.Image]$Source, [System.Drawing.Rectangle]$Crop, [int]$Size, [int]$Padding) {
    $foreground = [System.Drawing.Bitmap]::new($Size, $Size, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $graphics = [System.Drawing.Graphics]::FromImage($foreground)
    try {
        $graphics.Clear([System.Drawing.Color]::Transparent)
        $graphics.CompositingMode = [System.Drawing.Drawing2D.CompositingMode]::SourceOver
        $graphics.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality
        $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
        $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality

        $maxSide = [Math]::Max(1, $Size - ($Padding * 2))
        $scale = [Math]::Min($maxSide / $Crop.Width, $maxSide / $Crop.Height)
        $drawWidth = [Math]::Max(1, [int][Math]::Round($Crop.Width * $scale))
        $drawHeight = [Math]::Max(1, [int][Math]::Round($Crop.Height * $scale))
        $drawX = [int][Math]::Floor(($Size - $drawWidth) / 2)
        $drawY = [int][Math]::Floor(($Size - $drawHeight) / 2)
        $dest = [System.Drawing.Rectangle]::new($drawX, $drawY, $drawWidth, $drawHeight)
        $graphics.DrawImage($Source, $dest, $Crop, [System.Drawing.GraphicsUnit]::Pixel)
        return $foreground
    }
    finally {
        $graphics.Dispose()
    }
}

function Save-MicroOptimizedPng([System.Drawing.Bitmap]$Source, [System.Drawing.Rectangle]$Crop, [int]$Size, [string]$Path) {
    $padding = switch ($Size) {
        16 { 0 }
        20 { 1 }
        24 { 1 }
        32 { 2 }
        default { 1 }
    }
    $foreground = New-ScaledForeground -Source $Source -Crop $Crop -Size $Size -Padding $padding
    $result = [System.Drawing.Bitmap]::new($Size, $Size, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    try {
        $outlineColor = [System.Drawing.Color]::FromArgb(220, 4, 7, 16)
        for ($y = 0; $y -lt $Size; $y++) {
            for ($x = 0; $x -lt $Size; $x++) {
                $pixel = $foreground.GetPixel($x, $y)
                if ($pixel.A -gt 24) {
                    for ($oy = -1; $oy -le 1; $oy++) {
                        for ($ox = -1; $ox -le 1; $ox++) {
                            if ($ox -eq 0 -and $oy -eq 0) { continue }
                            $tx = $x + $ox
                            $ty = $y + $oy
                            if ($tx -ge 0 -and $tx -lt $Size -and $ty -ge 0 -and $ty -lt $Size) {
                                $existing = $result.GetPixel($tx, $ty)
                                if ($existing.A -lt $outlineColor.A) {
                                    $result.SetPixel($tx, $ty, $outlineColor)
                                }
                            }
                        }
                    }
                }
            }
        }

        for ($y = 0; $y -lt $Size; $y++) {
            for ($x = 0; $x -lt $Size; $x++) {
                $pixel = $foreground.GetPixel($x, $y)
                if ($pixel.A -gt 0) {
                    $result.SetPixel($x, $y, $pixel)
                }
            }
        }

        $result.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
    }
    finally {
        $foreground.Dispose()
        $result.Dispose()
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

    $contentBounds = Get-AlphaBounds -Source $sourceImage
    foreach ($size in $Sizes) {
        $targetPath = Join-Path $outputRootPath ("png\nightowl-{0}.png" -f $size)
        if ($size -in @(16, 20, 24, 32)) {
            Save-MicroOptimizedPng -Source $sourceImage -Crop $contentBounds -Size $size -Path $targetPath
        }
        else {
            Save-TransparentPng -Source $sourceImage -Size $size -Path $targetPath
        }
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
