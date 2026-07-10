param(
    [string]$OutputRoot = ".\assets\nightowl\icons"
)

$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Drawing

function Resolve-FullPath([string]$Path) {
    $executionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path)
}

function New-RoundedRectanglePath([float]$X, [float]$Y, [float]$Width, [float]$Height, [float]$Radius) {
    $path = [System.Drawing.Drawing2D.GraphicsPath]::new()
    $diameter = $Radius * 2
    $path.AddArc($X, $Y, $diameter, $diameter, 180, 90)
    $path.AddArc($X + $Width - $diameter, $Y, $diameter, $diameter, 270, 90)
    $path.AddArc($X + $Width - $diameter, $Y + $Height - $diameter, $diameter, $diameter, 0, 90)
    $path.AddArc($X, $Y + $Height - $diameter, $diameter, $diameter, 90, 90)
    $path.CloseFigure()
    return $path
}

function Add-Curve($Path, [float[]]$Values, [float]$Size) {
    $Path.AddBezier(
        $Values[0] * $Size, $Values[1] * $Size,
        $Values[2] * $Size, $Values[3] * $Size,
        $Values[4] * $Size, $Values[5] * $Size,
        $Values[6] * $Size, $Values[7] * $Size
    )
}

function New-OwlHeadPath([int]$Size) {
    $s = [float]$Size
    $path = [System.Drawing.Drawing2D.GraphicsPath]::new()
    $path.StartFigure()
    $path.AddLine(0.19 * $s, 0.63 * $s, 0.24 * $s, 0.36 * $s)
    Add-Curve $path @(0.24, 0.36, 0.28, 0.24, 0.38, 0.18, 0.43, 0.31) $s
    Add-Curve $path @(0.43, 0.31, 0.47, 0.26, 0.53, 0.26, 0.57, 0.31) $s
    Add-Curve $path @(0.57, 0.31, 0.62, 0.18, 0.76, 0.36, 0.81, 0.63) $s
    Add-Curve $path @(0.81, 0.63, 0.75, 0.82, 0.61, 0.89, 0.50, 0.89) $s
    Add-Curve $path @(0.50, 0.89, 0.39, 0.89, 0.25, 0.82, 0.19, 0.63) $s
    $path.CloseFigure()
    return $path
}

function Render-NightOwlIcon([int]$Size, [string]$Path) {
    $bitmap = [System.Drawing.Bitmap]::new($Size, $Size, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $graphics.Clear([System.Drawing.Color]::Transparent)

    $pad = [Math]::Max(1.0, $Size * 0.035)
    $radius = $Size * 0.20
    $bgRect = [System.Drawing.RectangleF]::new($pad, $pad, $Size - ($pad * 2), $Size - ($pad * 2))
    $bgPath = New-RoundedRectanglePath $bgRect.X $bgRect.Y $bgRect.Width $bgRect.Height $radius
    $bgBrush = [System.Drawing.Drawing2D.LinearGradientBrush]::new(
        $bgRect,
        [System.Drawing.Color]::FromArgb(255, 7, 10, 20),
        [System.Drawing.Color]::FromArgb(255, 37, 18, 82),
        45
    )
    $graphics.FillPath($bgBrush, $bgPath)

    $borderPen = [System.Drawing.Pen]::new([System.Drawing.Color]::FromArgb(210, 139, 92, 246), [Math]::Max(1.0, $Size * 0.025))
    $graphics.DrawPath($borderPen, $bgPath)

    $headPath = New-OwlHeadPath $Size
    $headBrush = [System.Drawing.Drawing2D.LinearGradientBrush]::new(
        [System.Drawing.RectangleF]::new(0, 0, $Size, $Size),
        [System.Drawing.Color]::FromArgb(255, 124, 58, 237),
        [System.Drawing.Color]::FromArgb(255, 39, 205, 135),
        90
    )
    $graphics.FillPath($headBrush, $headPath)
    $headPen = [System.Drawing.Pen]::new([System.Drawing.Color]::FromArgb(235, 200, 182, 255), [Math]::Max(1.0, $Size * 0.018))
    $graphics.DrawPath($headPen, $headPath)

    $eyeOuter = [System.Drawing.Color]::FromArgb(255, 240, 253, 244)
    $eyeInner = [System.Drawing.Color]::FromArgb(255, 11, 18, 32)
    $eyeGlow = [System.Drawing.Color]::FromArgb(255, 34, 197, 94)
    $eyeRadius = $Size * 0.128
    $pupilRadius = $Size * 0.058
    $leftEye = [System.Drawing.RectangleF]::new(($Size * 0.375) - $eyeRadius, ($Size * 0.49) - $eyeRadius, $eyeRadius * 2, $eyeRadius * 2)
    $rightEye = [System.Drawing.RectangleF]::new(($Size * 0.625) - $eyeRadius, ($Size * 0.49) - $eyeRadius, $eyeRadius * 2, $eyeRadius * 2)
    $eyeBrush = [System.Drawing.SolidBrush]::new($eyeOuter)
    $graphics.FillEllipse($eyeBrush, $leftEye)
    $graphics.FillEllipse($eyeBrush, $rightEye)
    $glowPen = [System.Drawing.Pen]::new($eyeGlow, [Math]::Max(1.0, $Size * 0.018))
    $graphics.DrawEllipse($glowPen, $leftEye)
    $graphics.DrawEllipse($glowPen, $rightEye)

    $pupilBrush = [System.Drawing.SolidBrush]::new($eyeInner)
    $graphics.FillEllipse($pupilBrush, [System.Drawing.RectangleF]::new(($Size * 0.375) - $pupilRadius, ($Size * 0.49) - $pupilRadius, $pupilRadius * 2, $pupilRadius * 2))
    $graphics.FillEllipse($pupilBrush, [System.Drawing.RectangleF]::new(($Size * 0.625) - $pupilRadius, ($Size * 0.49) - $pupilRadius, $pupilRadius * 2, $pupilRadius * 2))

    $beakPath = [System.Drawing.Drawing2D.GraphicsPath]::new()
    $beakPath.AddPolygon(@(
        [System.Drawing.PointF]::new($Size * 0.50, $Size * 0.57),
        [System.Drawing.PointF]::new($Size * 0.43, $Size * 0.67),
        [System.Drawing.PointF]::new($Size * 0.57, $Size * 0.67)
    ))
    $beakBrush = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(255, 250, 204, 21))
    $graphics.FillPath($beakBrush, $beakPath)

    $bitmap.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)

    $beakBrush.Dispose()
    $beakPath.Dispose()
    $pupilBrush.Dispose()
    $glowPen.Dispose()
    $eyeBrush.Dispose()
    $headPen.Dispose()
    $headBrush.Dispose()
    $headPath.Dispose()
    $borderPen.Dispose()
    $bgBrush.Dispose()
    $bgPath.Dispose()
    $graphics.Dispose()
    $bitmap.Dispose()
}

function Write-IcoFile([string[]]$PngPaths, [string]$IcoPath) {
    $pngBytes = @()
    foreach ($png in $PngPaths) {
        $pngBytes += ,([System.IO.File]::ReadAllBytes($png))
    }

    $stream = [System.IO.File]::Create($IcoPath)
    $writer = [System.IO.BinaryWriter]::new($stream)
    try {
        $count = [UInt16]$PngPaths.Count
        $writer.Write([UInt16]0)
        $writer.Write([UInt16]1)
        $writer.Write($count)
        $offset = 6 + (16 * $PngPaths.Count)

        for ($i = 0; $i -lt $PngPaths.Count; $i++) {
            $name = [System.IO.Path]::GetFileNameWithoutExtension($PngPaths[$i])
            $size = [int]($name -replace '\D+', '')
            $writer.Write([byte]($(if ($size -eq 256) { 0 } else { $size })))
            $writer.Write([byte]($(if ($size -eq 256) { 0 } else { $size })))
            $writer.Write([byte]0)
            $writer.Write([byte]0)
            $writer.Write([UInt16]1)
            $writer.Write([UInt16]32)
            $writer.Write([UInt32]$pngBytes[$i].Length)
            $writer.Write([UInt32]$offset)
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

$root = Resolve-FullPath $OutputRoot
$sourceDir = Join-Path $root "source"
$pngDir = Join-Path $root "png"
$icoDir = Join-Path $root "ico"
New-Item -ItemType Directory -Force -Path $sourceDir, $pngDir, $icoDir | Out-Null

$svg = @'
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" role="img" aria-label="NightOwl Agent icon">
  <defs>
    <linearGradient id="bg" x1="24" y1="24" x2="232" y2="232" gradientUnits="userSpaceOnUse">
      <stop stop-color="#070A14"/>
      <stop offset="1" stop-color="#251252"/>
    </linearGradient>
    <linearGradient id="owl" x1="128" y1="46" x2="128" y2="228" gradientUnits="userSpaceOnUse">
      <stop stop-color="#7C3AED"/>
      <stop offset="1" stop-color="#27CD87"/>
    </linearGradient>
  </defs>
  <rect x="9" y="9" width="238" height="238" rx="51" fill="url(#bg)" stroke="#8B5CF6" stroke-width="7"/>
  <path d="M49 161L62 92C62 92 75 61 97 46L110 79C120 67 136 67 146 79L159 46C181 61 194 92 207 161C190 209 156 228 128 228C100 228 66 209 49 161Z" fill="url(#owl)" stroke="#C8B6FF" stroke-width="5" stroke-linejoin="round"/>
  <circle cx="96" cy="125" r="33" fill="#F0FDF4" stroke="#22C55E" stroke-width="5"/>
  <circle cx="160" cy="125" r="33" fill="#F0FDF4" stroke="#22C55E" stroke-width="5"/>
  <circle cx="96" cy="125" r="15" fill="#0B1220"/>
  <circle cx="160" cy="125" r="15" fill="#0B1220"/>
  <path d="M128 146L110 171H146L128 146Z" fill="#FACC15"/>
</svg>
'@
$svgPath = Join-Path $sourceDir "nightowl-icon-master.svg"
$svg | Set-Content -Path $svgPath -Encoding UTF8

$sizes = @(16, 20, 24, 32, 40, 48, 64, 128, 256)
$pngPaths = @()
foreach ($size in $sizes) {
    $pngPath = Join-Path $pngDir ("nightowl-{0}.png" -f $size)
    Render-NightOwlIcon -Size $size -Path $pngPath
    $pngPaths += $pngPath
}

$masterPng = Join-Path $sourceDir "nightowl-icon-master.png"
Copy-Item -Path (Join-Path $pngDir "nightowl-256.png") -Destination $masterPng -Force

$icoPath = Join-Path $icoDir "NightOwl.ico"
Write-IcoFile -PngPaths $pngPaths -IcoPath $icoPath

$projectIconTargets = @(
    ".\NightOwl.Agent.Tray\assets\icons\NightOwl.ico",
    ".\NightOwl.Agent.Windows\assets\icons\NightOwl.ico",
    ".\NightOwl.Agent.Updater\assets\icons\NightOwl.ico"
)
foreach ($target in $projectIconTargets) {
    $targetPath = Resolve-FullPath $target
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $targetPath) | Out-Null
    Copy-Item -Path $icoPath -Destination $targetPath -Force
}

Write-Host "NightOwl icon assets generated in $root"
Write-Host "ICO: $icoPath"
