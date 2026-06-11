[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ServerUrl,

    [Parameter(Mandatory = $true)]
    [string]$EnrollmentToken,

    [string]$Hostname = $env:COMPUTERNAME,
    [string]$Domain = "",
    [string]$SerialNumber = "",
    [string]$AgentVersion = "0.1.0",
    [string]$AgentMode = "scheduled_task",
    [string]$InstallPath = "C:\RMM",
    [string]$TaskName = "RMM-Agent-Heartbeat",
    [string]$LogoPath = "",
    [string]$ResultPath = ""
)

$ErrorActionPreference = "Stop"
$script:ExitCode = 2

function Get-EnrollmentUrl {
    param([string]$Url)
    $trimmed = $Url.Trim()
    if ($trimmed -match "/api/agent/enroll/?$") {
        return ($trimmed -replace "/api/agent/enroll/?$", "/api/agent/enroll/")
    }
    if ($trimmed -match "/api/agent/heartbeat/?$") {
        return ($trimmed -replace "/api/agent/heartbeat/?$", "/api/agent/enroll/")
    }
    return ($trimmed.TrimEnd("/") + "/api/agent/enroll/")
}

function Resolve-LogoPath {
    param([string]$PreferredPath)
    $candidates = @()
    if (-not [string]::IsNullOrWhiteSpace($PreferredPath)) {
        $candidates += $PreferredPath
    }
    $candidates += "C:\RMM\assets\nightowl-logo.png"
    $candidates += (Join-Path (Get-Location) "assets\nightowl-logo.png")
    $candidates += "\\192.168.104.120\controlsul\Comum\_Agents\assets\nightowl-logo.png"
    $candidates += (Join-Path $PSScriptRoot "assets\nightowl-logo.png")
    $candidates += (Join-Path (Get-Location) "media\logo\nightowl-logo.png")

    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return (Resolve-Path $candidate).ProviderPath
        }
    }
    return ""
}

function New-BitmapImageFromFile {
    param([string]$Path)

    $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
    try {
        $bitmap = New-Object System.Windows.Media.Imaging.BitmapImage
        $bitmap.BeginInit()
        $bitmap.CacheOption = [System.Windows.Media.Imaging.BitmapCacheOption]::OnLoad
        $bitmap.StreamSource = $stream
        $bitmap.EndInit()
        $bitmap.Freeze()
        return $bitmap
    }
    finally {
        $stream.Dispose()
    }
}

function Get-FriendlyErrorMessage {
    param([string]$ErrorCode, [string]$Fallback)
    switch ($ErrorCode) {
        "manual_validation_token_expired" { return "Token expirado. Gere um novo token no portal Night Owl." }
        "invalid_manual_validation_token" { return "Token inválido. Verifique o código informado." }
        "manual_validation_token_used" { return "Este token já foi utilizado. Gere um novo token." }
        "domain_denied" { return "Este domínio não está autorizado para este enrollment." }
        "manual_validation_required" { return "Informe o token de validação manual para continuar." }
        default {
            if ([string]::IsNullOrWhiteSpace($Fallback)) {
                return "Não foi possível comunicar com o servidor Night Owl."
            }
            return $Fallback
        }
    }
}

function Invoke-Enrollment {
    param([string]$ManualToken)

    $payload = [ordered]@{
        enrollment_token = $EnrollmentToken
        manual_validation_token = $ManualToken
        hostname = $Hostname
        domain = $Domain
        serial_number = $SerialNumber
        agent_version = $AgentVersion
        agent_mode = $AgentMode
        install_path = $InstallPath
        task_name = $TaskName
    }
    $json = $payload | ConvertTo-Json -Depth 6
    $bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    $uri = Get-EnrollmentUrl -Url $ServerUrl

    try {
        return Invoke-RestMethod -Uri $uri -Method Post -Body $bodyBytes -ContentType "application/json; charset=utf-8"
    }
    catch {
        $body = ""
        $errorCode = ""
        if ($_.Exception.Response) {
            try {
                $stream = $_.Exception.Response.GetResponseStream()
                if ($stream) {
                    $reader = New-Object System.IO.StreamReader($stream)
                    $body = $reader.ReadToEnd()
                    if (-not [string]::IsNullOrWhiteSpace($body)) {
                        $parsed = $body | ConvertFrom-Json
                        $errorCode = [string]$parsed.error
                    }
                }
            }
            catch {
                $errorCode = ""
            }
        }
        $exception = New-Object System.Exception((Get-FriendlyErrorMessage -ErrorCode $errorCode -Fallback "Não foi possível comunicar com o servidor Night Owl."))
        $exception.Data["error"] = $errorCode
        throw $exception
    }
}

if ([string]::IsNullOrWhiteSpace($ResultPath)) {
    $ResultPath = Join-Path $env:TEMP ("nightowl-enroll-result-{0}.json" -f ([guid]::NewGuid().ToString("N")))
}

try {
    Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase
}
catch {
    Write-Host "WPF não está disponível neste Windows/.NET. Use -UseConsoleManualValidation no instalador."
    exit 2
}

[xml]$xaml = @"
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Validação Night Owl"
        Height="540"
        Width="460"
        ResizeMode="NoResize"
        WindowStartupLocation="CenterScreen"
        Background="#F3F4F8"
        FontFamily="Segoe UI">
    <Grid Margin="24">
        <Border Background="#FFFFFF" BorderBrush="#D8DCE5" BorderThickness="1" CornerRadius="22" Padding="28">
            <Border.Effect>
                <DropShadowEffect Color="#111827" BlurRadius="18" ShadowDepth="3" Opacity="0.12" />
            </Border.Effect>
            <StackPanel>
                <Grid Height="92" Margin="0,0,0,8">
                    <Border x:Name="LogoFallback" MinWidth="148" Height="56" CornerRadius="18" Background="#F8F7FF" BorderBrush="#DDD6FE" BorderThickness="1" HorizontalAlignment="Center" VerticalAlignment="Center" Padding="18,0">
                        <TextBlock Text="Night Owl" Foreground="#7C3AED" FontSize="22" FontWeight="SemiBold" HorizontalAlignment="Center" VerticalAlignment="Center" />
                    </Border>
                    <Image x:Name="LogoImage" Width="82" Height="82" Stretch="Uniform" Visibility="Collapsed" HorizontalAlignment="Center" VerticalAlignment="Center" />
                </Grid>

                <TextBlock Text="Validação Night Owl" Foreground="#111827" FontSize="25" FontWeight="SemiBold" TextAlignment="Center" Margin="0,0,0,8" />
                <TextBlock Text="Esta máquina precisa de validação manual para concluir a instalação." Foreground="#6B7280" FontSize="14" TextAlignment="Center" TextWrapping="Wrap" Margin="12,0,12,16" />

                <Border Background="#F8F7FF" BorderBrush="#DDD6FE" BorderThickness="1" CornerRadius="12" Padding="14" Margin="0,0,0,20">
                    <TextBlock Text="Gere um token de validação no portal do Night Owl e informe abaixo. O token expira em 5 minutos." Foreground="#4B5563" FontSize="13" TextWrapping="Wrap" />
                </Border>

                <TextBlock Text="Token de validação" Foreground="#374151" FontSize="13" FontWeight="SemiBold" Margin="2,0,0,7" />
                <Grid Margin="0,0,0,12">
                    <TextBox x:Name="TokenBox" Height="44" FontSize="15" Padding="13,8" BorderBrush="#D8DCE5" BorderThickness="1" Background="#FFFFFF" Foreground="#111827" />
                    <TextBlock x:Name="PlaceholderText" Text="manual_..." Foreground="#9CA3AF" FontSize="15" Margin="14,11,0,0" IsHitTestVisible="False" />
                </Grid>

                <TextBlock x:Name="StatusText" Text="" Foreground="#6B7280" FontSize="13" TextWrapping="Wrap" MinHeight="38" Margin="2,0,2,12" />

                <Button x:Name="ValidateButton" Content="Validar e continuar" Height="44" Background="#7C3AED" Foreground="White" BorderThickness="0" FontWeight="SemiBold" FontSize="14" Margin="0,0,0,10" />
                <Button x:Name="CancelButton" Content="Cancelar" Height="40" Background="#FFFFFF" Foreground="#374151" BorderBrush="#D8DCE5" BorderThickness="1" FontWeight="SemiBold" FontSize="13" />

                <TextBlock Text="Night Owl RMM" Foreground="#9CA3AF" FontSize="12" TextAlignment="Center" Margin="0,20,0,0" />
            </StackPanel>
        </Border>
    </Grid>
</Window>
"@

$reader = New-Object System.Xml.XmlNodeReader $xaml
$window = [Windows.Markup.XamlReader]::Load($reader)

$logoImage = $window.FindName("LogoImage")
$logoFallback = $window.FindName("LogoFallback")
$tokenBox = $window.FindName("TokenBox")
$placeholderText = $window.FindName("PlaceholderText")
$statusText = $window.FindName("StatusText")
$validateButton = $window.FindName("ValidateButton")
$cancelButton = $window.FindName("CancelButton")

$resolvedLogo = Resolve-LogoPath -PreferredPath $LogoPath
if (-not [string]::IsNullOrWhiteSpace($resolvedLogo)) {
    try {
        $bitmap = New-BitmapImageFromFile -Path $resolvedLogo
        $logoImage.Source = $bitmap
        $logoImage.Visibility = "Visible"
        $logoFallback.Visibility = "Collapsed"
    }
    catch {
        $logoImage.Visibility = "Collapsed"
        $logoFallback.Visibility = "Visible"
    }
}

$tokenBox.Add_TextChanged({
    if ([string]::IsNullOrWhiteSpace($tokenBox.Text)) {
        $placeholderText.Visibility = "Visible"
    }
    else {
        $placeholderText.Visibility = "Collapsed"
    }
})

$cancelButton.Add_Click({
    $script:ExitCode = 1
    $window.Close()
})

$validateButton.Add_Click({
    $manualToken = $tokenBox.Text.Trim()
    if ([string]::IsNullOrWhiteSpace($manualToken)) {
        $statusText.Foreground = "#DC2626"
        $statusText.Text = "Informe o token de validação manual."
        return
    }
    if (-not $manualToken.StartsWith("manual_")) {
        $statusText.Foreground = "#DC2626"
        $statusText.Text = "O token deve começar com manual_."
        return
    }

    $validateButton.IsEnabled = $false
    $cancelButton.IsEnabled = $false
    $statusText.Foreground = "#6B7280"
    $statusText.Text = "Validando token..."

    try {
        $response = Invoke-Enrollment -ManualToken $manualToken
        if (-not $response.agent_token) {
            throw "Resposta do servidor não incluiu agent_token."
        }

        $resultDirectory = Split-Path -Path $ResultPath -Parent
        if ($resultDirectory -and -not (Test-Path $resultDirectory)) {
            New-Item -Path $resultDirectory -ItemType Directory -Force | Out-Null
        }
        $response | ConvertTo-Json -Depth 6 | Set-Content -Path $ResultPath -Encoding UTF8
        $statusText.Foreground = "#16A34A"
        $statusText.Text = "Validação concluída. Continuando instalação..."
        $script:ExitCode = 0
        $window.Close()
    }
    catch {
        $statusText.Foreground = "#DC2626"
        $statusText.Text = $_.Exception.Message
        $validateButton.IsEnabled = $true
        $cancelButton.IsEnabled = $true
        $script:ExitCode = 2
    }
})

$window.Add_Closed({
    if ($script:ExitCode -eq 2 -and [string]::IsNullOrWhiteSpace($statusText.Text)) {
        $script:ExitCode = 1
    }
})

$null = $window.ShowDialog()
exit $script:ExitCode
