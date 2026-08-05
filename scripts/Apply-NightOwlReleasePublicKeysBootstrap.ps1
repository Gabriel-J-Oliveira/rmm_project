param(
    [string]$BundlePath,
    [string]$SignaturePath = "",
    [string]$ExpectedSha256 = "",
    [string]$SignerKeyId = "nightowl-release-2026-01",
    [string]$TargetPath = "C:\ProgramData\NightOwl\AgentDotNet\release-public-keys.json",
    [string]$LogPath = "C:\ProgramData\NightOwl\Logs\agent-key-bootstrap.jsonl",
    [switch]$TestBootstrapUntrusted,
    [string]$ConfirmTestBootstrap = "",
    [switch]$Rollback,
    [string]$BackupPath = "",
    [switch]$ValidateOnly,
    [switch]$SelfTest
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$script:SkipAclForSelfTest = $false

function Write-Step([string]$Message) { Write-Host ("[nightowl-key-bootstrap] {0}" -f $Message) }

function Write-Utf8NoBomText([string]$Path, [string]$Content) {
    $directory = Split-Path -Parent $Path
    if (-not [string]::IsNullOrWhiteSpace($directory)) { New-Item -ItemType Directory -Force -Path $directory | Out-Null }
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $encoding)
}

function Write-JsonLog([string]$EventType, [string]$Message, $Data = @{}) {
    $entry = [ordered]@{
        timestamp = (Get-Date).ToUniversalTime().ToString("O")
        event_type = $EventType
        message = $Message
        data = $Data
    }
    $existing = ""
    if (Test-Path $script:LogPath) {
        $existing = Get-Content -Raw -Path $script:LogPath
    }
    Write-Utf8NoBomText -Path $script:LogPath -Content ($existing + ($entry | ConvertTo-Json -Compress -Depth 8) + "`n")
}

function Assert-Administrator {
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object System.Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "KEY_BOOTSTRAP_ADMIN_REQUIRED: execute como administrador."
    }
}

function Assert-RsaPssProviderAvailable {
    if ($null -eq ("System.Security.Cryptography.RSACng" -as [type])) {
        throw "RELEASE_RSA_PSS_PROVIDER_UNAVAILABLE: Provedor CNG RSA indisponivel. RSA-PSS e obrigatorio."
    }
}

function New-RsaCngInstance {
    Assert-RsaPssProviderAvailable
    try { return New-Object System.Security.Cryptography.RSACng }
    catch { throw "RELEASE_RSA_PSS_PROVIDER_UNAVAILABLE: Falha ao criar RSACng. Detalhe: $($_.Exception.Message)" }
}

function Import-RsaParametersFromXml([string]$Xml, [bool]$IncludePrivateParameters = $false) {
    $legacyProvider = $null
    try {
        $legacyProvider = New-Object System.Security.Cryptography.RSACryptoServiceProvider
        $legacyProvider.PersistKeyInCsp = $false
        $legacyProvider.FromXmlString($Xml)
        return $legacyProvider.ExportParameters($IncludePrivateParameters)
    }
    catch { throw "RELEASE_PUBLIC_KEY_INVALID: chave publica RSA XML invalida. Detalhe: $($_.Exception.Message)" }
    finally {
        if ($null -ne $legacyProvider) {
            $legacyProvider.PersistKeyInCsp = $false
            $legacyProvider.Clear()
            $legacyProvider.Dispose()
        }
    }
}

function New-RsaPssPublicKeyFromXmlText([string]$Xml) {
    $rsa = New-RsaCngInstance
    try {
        $rsa.ImportParameters((Import-RsaParametersFromXml -Xml $Xml))
        return $rsa
    }
    catch {
        $rsa.Dispose()
        throw
    }
}

function Read-KeyBundle([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path $Path)) { throw "KEY_BUNDLE_MISSING: $Path" }
    try { return Get-Content -Raw -Path $Path | ConvertFrom-Json }
    catch { throw "KEY_BUNDLE_INVALID: JSON invalido em $Path. Detalhe: $($_.Exception.Message)" }
}

function Assert-KeyBundleSafe([string]$Path, [bool]$RequireNewKey = $true) {
    $bundle = Read-KeyBundle $Path
    $seen = @{}
    foreach ($item in @($bundle.keys)) {
        $keyId = [string]$item.key_id
        $algorithm = [string]$item.algorithm
        $publicXml = [string]$item.public_key_xml
        if ([string]::IsNullOrWhiteSpace($keyId)) { throw "KEY_BUNDLE_INVALID: key_id vazio." }
        if ($seen.ContainsKey($keyId)) { throw "KEY_BUNDLE_INVALID: key_id duplicado: $keyId." }
        $seen[$keyId] = $true
        if ($algorithm -ne "RSA-PSS-SHA256") { throw "KEY_BUNDLE_INVALID: algoritmo nao permitido para $keyId." }
        if ([string]::IsNullOrWhiteSpace($publicXml)) { throw "KEY_BUNDLE_INVALID: public_key_xml vazio para $keyId." }
        foreach ($privateElement in @("<P>", "<Q>", "<DP>", "<DQ>", "<InverseQ>", "<D>")) {
            if ($publicXml.Contains($privateElement)) { throw "KEY_BUNDLE_UNSAFE: $keyId contem parametro privado $privateElement." }
        }
    }
    if (-not $seen.ContainsKey("nightowl-release-2026-01")) { throw "KEY_BUNDLE_INVALID: nightowl-release-2026-01 ausente." }
    if ($RequireNewKey -and -not $seen.ContainsKey("nightowl-release-2026-02")) { throw "KEY_BUNDLE_INVALID: nightowl-release-2026-02 ausente." }
    return $bundle
}

function Get-KeyEntry($Bundle, [string]$KeyId) {
    foreach ($item in @($Bundle.keys)) {
        if ([string]$item.key_id -eq $KeyId) { return $item }
    }
    return $null
}

function Get-FileSha256([string]$Path) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $stream = [System.IO.File]::OpenRead($Path)
        try {
            return [System.BitConverter]::ToString($sha.ComputeHash($stream)).Replace("-", "").ToLowerInvariant()
        }
        finally { $stream.Dispose() }
    }
    finally { $sha.Dispose() }
}

function Read-SignatureBytes([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path $Path)) { throw "KEY_BUNDLE_SIGNATURE_MISSING: assinatura obrigatoria ausente." }
    $raw = (Get-Content -Raw -Path $Path).Trim()
    try { return [Convert]::FromBase64String($raw) }
    catch { return [System.IO.File]::ReadAllBytes($Path) }
}

function Assert-BundleSignature([string]$CandidateBundlePath, [string]$CandidateSignaturePath, [string]$CurrentBundlePath, [string]$RequiredSignerKeyId) {
    if ($RequiredSignerKeyId -ne "nightowl-release-2026-01") {
        throw "KEY_BOOTSTRAP_SIGNER_INVALID: signer deve ser nightowl-release-2026-01."
    }
    $currentBundle = Assert-KeyBundleSafe -Path $CurrentBundlePath -RequireNewKey $false
    $signer = Get-KeyEntry $currentBundle $RequiredSignerKeyId
    if ($null -eq $signer) { throw "KEY_BOOTSTRAP_SIGNER_NOT_TRUSTED: chave antiga nao existe no bundle local." }
    $rsa = New-RsaPssPublicKeyFromXmlText -Xml ([string]$signer.public_key_xml)
    try {
        $valid = $rsa.VerifyData(
            [System.IO.File]::ReadAllBytes($CandidateBundlePath),
            (Read-SignatureBytes $CandidateSignaturePath),
            [System.Security.Cryptography.HashAlgorithmName]::SHA256,
            [System.Security.Cryptography.RSASignaturePadding]::Pss
        )
        if (-not $valid) { throw "KEY_BUNDLE_SIGNATURE_INVALID: assinatura nao valida com nightowl-release-2026-01." }
    }
    finally { $rsa.Dispose() }
}

function Set-PublicKeysAcl([string]$Path) {
    if ($script:SkipAclForSelfTest) {
        return
    }
    $currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
    $systemSid = New-Object System.Security.Principal.SecurityIdentifier "S-1-5-18"
    $adminsSid = New-Object System.Security.Principal.SecurityIdentifier "S-1-5-32-544"
    $usersSid = New-Object System.Security.Principal.SecurityIdentifier "S-1-5-32-545"
    $acl = New-Object System.Security.AccessControl.FileSecurity
    $acl.SetOwner($currentUser)
    $acl.SetAccessRuleProtection($true, $false)
    $allow = [System.Security.AccessControl.AccessControlType]::Allow
    $noneInheritance = [System.Security.AccessControl.InheritanceFlags]::None
    $nonePropagation = [System.Security.AccessControl.PropagationFlags]::None
    $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($systemSid, [System.Security.AccessControl.FileSystemRights]::FullControl, $noneInheritance, $nonePropagation, $allow)))
    $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($adminsSid, [System.Security.AccessControl.FileSystemRights]::FullControl, $noneInheritance, $nonePropagation, $allow)))
    $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($usersSid, [System.Security.AccessControl.FileSystemRights]::ReadAndExecute, $noneInheritance, $nonePropagation, $allow)))
    [System.IO.File]::SetAccessControl($Path, $acl)
}

function Install-KeyBundle([string]$SourcePath, [string]$DestinationPath) {
    $destinationDirectory = Split-Path -Parent $DestinationPath
    New-Item -ItemType Directory -Force -Path $destinationDirectory | Out-Null
    $targetSha = if (Test-Path $DestinationPath) { Get-FileSha256 $DestinationPath } else { "" }
    $sourceSha = Get-FileSha256 $SourcePath
    if ($targetSha -eq $sourceSha) {
        Write-JsonLog "key_bootstrap.idempotent" "Bundle ja instalado." @{ sha256 = $sourceSha }
        Write-Step "Bundle ja estava instalado. Nada a fazer."
        return
    }
    $timestamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddHHmmss")
    $backup = "$DestinationPath.backup-$timestamp"
    if (Test-Path $DestinationPath) {
        Copy-Item -LiteralPath $DestinationPath -Destination $backup -Force
    }
    $temp = "$DestinationPath.tmp-$timestamp"
    Copy-Item -LiteralPath $SourcePath -Destination $temp -Force
    Set-PublicKeysAcl -Path $temp
    Move-Item -LiteralPath $temp -Destination $DestinationPath -Force
    Set-PublicKeysAcl -Path $DestinationPath
    Write-JsonLog "key_bootstrap.installed" "Bundle de chaves atualizado." @{ sha256 = $sourceSha; backup = $backup }
    Write-Step "Bundle instalado em: $DestinationPath"
    if (Test-Path $backup) { Write-Step "Backup preservado em: $backup" }
}

function Restore-KeyBundle([string]$SelectedBackupPath, [string]$DestinationPath) {
    if ([string]::IsNullOrWhiteSpace($SelectedBackupPath) -or -not (Test-Path $SelectedBackupPath)) { throw "KEY_BOOTSTRAP_BACKUP_MISSING: informe -BackupPath valido." }
    Assert-KeyBundleSafe -Path $SelectedBackupPath -RequireNewKey $false | Out-Null
    Install-KeyBundle -SourcePath $SelectedBackupPath -DestinationPath $DestinationPath
    Write-JsonLog "key_bootstrap.rollback" "Rollback do bundle de chaves aplicado." @{ backup = $SelectedBackupPath }
}

function Invoke-SelfTest {
    $temp = Join-Path ([System.IO.Path]::GetTempPath()) ("nightowl-key-bootstrap-selftest-{0}" -f ([guid]::NewGuid().ToString("N")))
    New-Item -ItemType Directory -Force -Path $temp | Out-Null
    try {
        $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
        $principal = New-Object System.Security.Principal.WindowsPrincipal($identity)
        $script:SkipAclForSelfTest = -not $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)
        $oldProvider = New-Object System.Security.Cryptography.RSACryptoServiceProvider 3072
        $newProvider = New-Object System.Security.Cryptography.RSACryptoServiceProvider 3072
        try {
            $current = Join-Path $temp "current.json"
            $candidate = Join-Path $temp "candidate.json"
            $signature = Join-Path $temp "candidate.sig"
            $oldPrivateRsa = New-RsaCngInstance
            $oldPrivateRsa.ImportParameters((Import-RsaParametersFromXml -Xml $oldProvider.ToXmlString($true) -IncludePrivateParameters $true))
            Write-Utf8NoBomText -Path $script:LogPath -Content ""
            Write-Utf8NoBomText -Path $current -Content (([ordered]@{ keys = @([ordered]@{ key_id = "nightowl-release-2026-01"; algorithm = "RSA-PSS-SHA256"; public_key_xml = $oldProvider.ToXmlString($false); status = "active"; valid_from = ""; valid_until = ""; revoked_at = "" }) } | ConvertTo-Json -Depth 8))
            Write-Utf8NoBomText -Path $candidate -Content (([ordered]@{ keys = @(
                [ordered]@{ key_id = "nightowl-release-2026-01"; algorithm = "RSA-PSS-SHA256"; public_key_xml = $oldProvider.ToXmlString($false); status = "active"; valid_from = ""; valid_until = ""; revoked_at = "" },
                [ordered]@{ key_id = "nightowl-release-2026-02"; algorithm = "RSA-PSS-SHA256"; public_key_xml = $newProvider.ToXmlString($false); status = "active"; valid_from = ""; valid_until = ""; revoked_at = "" }
            ) } | ConvertTo-Json -Depth 8))
            $sig = $oldPrivateRsa.SignData([System.IO.File]::ReadAllBytes($candidate), [System.Security.Cryptography.HashAlgorithmName]::SHA256, [System.Security.Cryptography.RSASignaturePadding]::Pss)
            Write-Utf8NoBomText -Path $signature -Content ([Convert]::ToBase64String($sig))
            Assert-KeyBundleSafe -Path $candidate | Out-Null
            Assert-BundleSignature -CandidateBundlePath $candidate -CandidateSignaturePath $signature -CurrentBundlePath $current -RequiredSignerKeyId "nightowl-release-2026-01"
            $target = Join-Path $temp "target.json"
            Copy-Item -LiteralPath $current -Destination $target
            Install-KeyBundle -SourcePath $candidate -DestinationPath $target
            Restore-KeyBundle -SelectedBackupPath (Get-ChildItem "$target.backup-*" | Select-Object -First 1).FullName -DestinationPath $target
            if ($script:SkipAclForSelfTest) {
                Write-Step "SelfTest OK: validacao assinada, instalacao atomica e rollback testados. ACL pulada porque o PowerShell nao esta elevado."
            }
            else {
                Write-Step "SelfTest OK: validacao assinada, instalacao atomica, ACL e rollback testados."
            }
        }
        finally {
            if ($null -ne $oldPrivateRsa) { $oldPrivateRsa.Dispose() }
            $oldProvider.Clear(); $oldProvider.Dispose()
            $newProvider.Clear(); $newProvider.Dispose()
        }
    }
    finally {
        $script:SkipAclForSelfTest = $false
        Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

if ($SelfTest) {
    $script:LogPath = Join-Path ([System.IO.Path]::GetTempPath()) "nightowl-key-bootstrap-selftest.jsonl"
    Invoke-SelfTest
    return
}

$script:LogPath = $LogPath
Assert-Administrator

if ($Rollback) {
    Restore-KeyBundle -SelectedBackupPath $BackupPath -DestinationPath $TargetPath
    return
}

if ([string]::IsNullOrWhiteSpace($BundlePath)) { throw "BundlePath obrigatorio." }
$bundle = Assert-KeyBundleSafe -Path $BundlePath
$bundleSha = Get-FileSha256 $BundlePath
if (-not [string]::IsNullOrWhiteSpace($ExpectedSha256) -and $bundleSha -ne $ExpectedSha256.ToLowerInvariant()) {
    throw "KEY_BUNDLE_HASH_MISMATCH: esperado $ExpectedSha256, obtido $bundleSha."
}

if ([string]::IsNullOrWhiteSpace($SignaturePath)) {
    if (-not $TestBootstrapUntrusted -or $ConfirmTestBootstrap -ne "TEST_BOOTSTRAP_UNTRUSTED") {
        throw "KEY_BUNDLE_SIGNATURE_MISSING: bundle sem assinatura so pode ser aplicado em laboratorio com -TestBootstrapUntrusted -ConfirmTestBootstrap TEST_BOOTSTRAP_UNTRUSTED."
    }
    Write-JsonLog "key_bootstrap.test_untrusted" "TEST_BOOTSTRAP_UNTRUSTED aplicado por administrador local." @{ sha256 = $bundleSha }
    Write-Step "ATENCAO: TEST_BOOTSTRAP_UNTRUSTED. Uso apenas laboratorio/development, nunca pilot/stable."
}
else {
    Assert-BundleSignature -CandidateBundlePath $BundlePath -CandidateSignaturePath $SignaturePath -CurrentBundlePath $TargetPath -RequiredSignerKeyId $SignerKeyId
    Write-JsonLog "key_bootstrap.signature_valid" "Assinatura do bundle validada pela chave antiga." @{ signer_key_id = $SignerKeyId; sha256 = $bundleSha }
}

if ($ValidateOnly) {
    Write-Step "Validacao concluida. Nenhum arquivo alterado."
    return
}

Install-KeyBundle -SourcePath $BundlePath -DestinationPath $TargetPath
