param(
    [string]$ConfigDirectory = "C:\ProgramData\NightOwl\ReleasePublisher",
    [string]$SigningKeyDirectory = "C:\ProgramData\NightOwl\ReleaseSigning",
    [string]$RunnerAccount = "",
    [switch]$ApplyAcl,
    [switch]$WriteTemplateConfig,
    [switch]$SelfTest
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Step([string]$Message) {
    Write-Host ("[nightowl-runner-prereq] {0}" -f $Message)
}

function Assert-CommandAvailable([string]$Name, [string]$Hint) {
    if ($null -eq (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name nao encontrado. $Hint"
    }
}

function Assert-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Execute como administrador para aplicar ACLs ou preparar diretorios em ProgramData."
    }
}

function Grant-DirectoryAcl([string]$Path, [string]$AccountName) {
    $acl = Get-Acl -LiteralPath $Path
    $acl.SetAccessRuleProtection($true, $false)
    $systemSid = New-Object System.Security.Principal.SecurityIdentifier "S-1-5-18"
    $rules = @(
        New-Object System.Security.AccessControl.FileSystemAccessRule($systemSid, "FullControl", "ContainerInherit,ObjectInherit", "None", "Allow")
    )
    if (-not [string]::IsNullOrWhiteSpace($AccountName)) {
        $account = New-Object System.Security.Principal.NTAccount($AccountName)
        $rules += New-Object System.Security.AccessControl.FileSystemAccessRule($account, "FullControl", "ContainerInherit,ObjectInherit", "None", "Allow")
    }
    foreach ($rule in $rules) { $acl.AddAccessRule($rule) | Out-Null }
    Set-Acl -LiteralPath $Path -AclObject $acl
}

function Write-Utf8NoBomJson([string]$Path, $Value) {
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, ($Value | ConvertTo-Json -Depth 6), $encoding)
}

if ($SelfTest) {
    Assert-CommandAvailable "dotnet" "Instale o .NET SDK 8.x no runner."
    Assert-CommandAvailable "git" "Instale Git for Windows no runner."
    Assert-CommandAvailable "ssh.exe" "Habilite OpenSSH Client do Windows."
    Assert-CommandAvailable "scp.exe" "Habilite OpenSSH Client do Windows."
    Write-Step "SelfTest OK: comandos essenciais encontrados."
    exit 0
}

Assert-Admin
foreach ($directory in @($ConfigDirectory, $SigningKeyDirectory)) {
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
    Write-Step "Diretorio garantido: $directory"
}

if ($ApplyAcl) {
    Grant-DirectoryAcl -Path $ConfigDirectory -AccountName $RunnerAccount
    Grant-DirectoryAcl -Path $SigningKeyDirectory -AccountName $RunnerAccount
    Write-Step "ACLs restritivas aplicadas. SYSTEM e conta do runner mantem controle."
}

if ($WriteTemplateConfig) {
    $templatePath = Join-Path $ConfigDirectory "release-publisher.template.json"
    $template = [ordered]@{
        signing_key_path = "C:\ProgramData\NightOwl\ReleaseSigning\nightowl-release-private.xml"
        signing_key_id = "nightowl-release-YYYY-MM"
        trusted_public_keys_path = "C:\ProgramData\NightOwl\ReleaseSigning\release-public-keys.json"
        remote_alias = "nightowl-release"
        remote_project_path = "/opt/nightowl"
        public_base_url = "https://nightowl.controlsul.com.br/downloads/nightowl-agent"
    }
    Write-Utf8NoBomJson -Path $templatePath -Value $template
    Write-Step "Modelo de configuracao criado: $templatePath"
}

Write-Step "Pre-requisitos preparados. Nenhuma chave privada foi criada ou substituida."
