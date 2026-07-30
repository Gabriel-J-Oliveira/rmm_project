param(
    [string]$RemoteHost = "",
    [string]$RemoteUser = "",
    [string]$Alias = "nightowl-release",
    [string]$KeyPath = (Join-Path $env:USERPROFILE ".ssh\nightowl_release_ed25519"),
    [switch]$Force,
    [switch]$SelfTest
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Step([string]$Message) {
    Write-Host ("[nightowl-ssh-init] {0}" -f $Message)
}

function Resolve-FullPath([string]$Path) {
    return $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path)
}

function Require-Command([string]$Name) {
    $commandInfo = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $commandInfo) {
        throw "Comando obrigatorio nao encontrado: $Name. Instale/habilite o OpenSSH Client do Windows."
    }
    return $commandInfo.Source
}

function Quote-NativeArgument([string]$Value) {
    if ($null -eq $Value) {
        return '""'
    }
    if ($Value.Length -eq 0) {
        return '""'
    }
    if ($Value -notmatch '[\s"]') {
        return $Value
    }
    return '"' + $Value.Replace('"', '\"') + '"'
}

function Invoke-NativeCommand([string]$FilePath, [string[]]$ArgumentList, [switch]$Interactive) {
    if ($Interactive) {
        $nativeOutput = & $FilePath @ArgumentList 2>&1
        return [pscustomobject]@{
            ExitCode = $LASTEXITCODE
            Stdout = ($nativeOutput | Out-String)
            Stderr = ""
        }
    }

    $processStartInfo = New-Object System.Diagnostics.ProcessStartInfo
    $processStartInfo.FileName = $FilePath
    $processStartInfo.Arguments = (($ArgumentList | ForEach-Object { Quote-NativeArgument $_ }) -join " ")
    $processStartInfo.UseShellExecute = $false
    $processStartInfo.RedirectStandardOutput = $true
    $processStartInfo.RedirectStandardError = $true
    $processStartInfo.CreateNoWindow = $true

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $processStartInfo
    [void]$process.Start()
    $stdoutText = $process.StandardOutput.ReadToEnd()
    $stderrText = $process.StandardError.ReadToEnd()
    $process.WaitForExit()

    return [pscustomobject]@{
        ExitCode = $process.ExitCode
        Stdout = $stdoutText
        Stderr = $stderrText
    }
}

function Get-SshKeygenArguments([string]$PrivateKeyPath, [string]$Comment) {
    return @(
        "-t", "ed25519",
        "-f", $PrivateKeyPath,
        "-N", "",
        "-C", $Comment
    )
}

function Test-KeyPairExists([string]$PrivateKeyPath) {
    return (Test-Path -LiteralPath $PrivateKeyPath) -and (Test-Path -LiteralPath "$PrivateKeyPath.pub")
}

function ConvertTo-SshConfigPath([string]$Path) {
    $fullPath = Resolve-FullPath $Path
    $localUserProfilePath = Resolve-FullPath $env:USERPROFILE
    if ($fullPath.StartsWith($localUserProfilePath, [StringComparison]::OrdinalIgnoreCase)) {
        return ("~" + $fullPath.Substring($localUserProfilePath.Length).Replace("\", "/"))
    }
    return $fullPath.Replace("\", "/")
}

function Set-PrivateKeyAcl([string]$PrivateKeyPath) {
    try {
        $aclResult = Invoke-NativeCommand -FilePath "icacls.exe" -ArgumentList @(
            $PrivateKeyPath,
            "/inheritance:r",
            "/grant:r",
            "$env:USERNAME`:R"
        )
        if ($aclResult.ExitCode -ne 0) {
            Write-Step "Aviso: icacls retornou $($aclResult.ExitCode): $($aclResult.Stderr.Trim())"
        }
    }
    catch {
        Write-Step "Aviso: nao foi possivel ajustar ACL local da chave automaticamente: $($_.Exception.Message)"
    }
}

function New-SshConfigBlock([string]$AliasName, [string]$HostName, [string]$UserName, [string]$IdentityFile) {
    return @(
        "Host $AliasName",
        "    HostName $HostName",
        "    User $UserName",
        "    IdentityFile $IdentityFile",
        "    IdentitiesOnly yes",
        "    ServerAliveInterval 30",
        "    ServerAliveCountMax 3"
    )
}

function Update-SshConfig([string]$ConfigPath, [string]$AliasName, [string]$HostName, [string]$UserName, [string]$IdentityFile) {
    $sshDirectory = Split-Path -Parent $ConfigPath
    New-Item -ItemType Directory -Force -Path $sshDirectory | Out-Null

    $existingLines = @()
    if (Test-Path -LiteralPath $ConfigPath) {
        $existingLines = @(Get-Content -LiteralPath $ConfigPath)
    }

    $outputLines = New-Object System.Collections.Generic.List[string]
    for ($lineIndex = 0; $lineIndex -lt $existingLines.Count; $lineIndex++) {
        $lineText = $existingLines[$lineIndex]
        $hostMatch = [regex]::Match($lineText, '^\s*Host\s+(.+?)\s*$')
        if ($hostMatch.Success) {
            $configuredHostNames = $hostMatch.Groups[1].Value.Split(" ", [System.StringSplitOptions]::RemoveEmptyEntries)
            if ($configuredHostNames -contains $AliasName) {
                while (($lineIndex + 1) -lt $existingLines.Count -and -not [regex]::IsMatch($existingLines[$lineIndex + 1], '^\s*Host\s+.+?\s*$')) {
                    $lineIndex++
                }
                continue
            }
        }
        [void]$outputLines.Add($lineText)
    }

    if ($outputLines.Count -gt 0 -and -not [string]::IsNullOrWhiteSpace($outputLines[$outputLines.Count - 1])) {
        [void]$outputLines.Add("")
    }
    foreach ($blockLine in (New-SshConfigBlock -AliasName $AliasName -HostName $HostName -UserName $UserName -IdentityFile $IdentityFile)) {
        [void]$outputLines.Add($blockLine)
    }

    $outputLines | Set-Content -LiteralPath $ConfigPath -Encoding ascii
}

function ConvertTo-BashSingleQuoted([string]$Value) {
    return "'" + $Value.Replace("'", "'\''") + "'"
}

function Install-RemotePublicKey([string]$SshPath, [string]$RemoteTarget, [string]$PublicKeyText) {
    $publicKeySingleQuoted = ConvertTo-BashSingleQuoted ($PublicKeyText.Trim())
    $remoteCommand = "umask 077; mkdir -p /root/.ssh; touch /root/.ssh/authorized_keys; grep -qxF $publicKeySingleQuoted /root/.ssh/authorized_keys || printf '%s\n' $publicKeySingleQuoted >> /root/.ssh/authorized_keys; chmod 700 /root/.ssh; chmod 600 /root/.ssh/authorized_keys"
    return Invoke-NativeCommand -FilePath $SshPath -ArgumentList @($RemoteTarget, $remoteCommand) -Interactive
}

function Test-SshAlias([string]$SshPath, [string]$AliasName) {
    return Invoke-NativeCommand -FilePath $SshPath -ArgumentList @("-o", "BatchMode=yes", $AliasName, "echo ok")
}

function Invoke-SelfTest {
    Write-Step "Executando self-test estatico."
    $testRootPath = Join-Path ([System.IO.Path]::GetTempPath()) ("NightOwlReleaseInitTests-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $testRootPath | Out-Null
    try {
        $testKeyPath = Join-Path $testRootPath "nightowl_release_ed25519"
        $keygenArgs = Get-SshKeygenArguments -PrivateKeyPath $testKeyPath -Comment "nightowl-release"
        if ($keygenArgs.Count -ne 8 -or $keygenArgs[4] -ne "-N" -or $keygenArgs[5] -ne "") {
            throw "Self-test falhou: argumentos do ssh-keygen nao preservam passphrase vazia."
        }

        Set-Content -LiteralPath $testKeyPath -Value "private" -Encoding ascii
        if (Test-KeyPairExists $testKeyPath) {
            throw "Self-test falhou: par de chaves detectado sem .pub."
        }
        Set-Content -LiteralPath "$testKeyPath.pub" -Value "public" -Encoding ascii
        if (-not (Test-KeyPairExists $testKeyPath)) {
            throw "Self-test falhou: par de chaves existente nao foi detectado."
        }

        $configPath = Join-Path $testRootPath "config"
        @(
            "Host existing-host",
            "    HostName example.local",
            "",
            "Host nightowl-release",
            "    HostName old",
            "    User old"
        ) | Set-Content -LiteralPath $configPath -Encoding ascii
        Update-SshConfig -ConfigPath $configPath -AliasName "nightowl-release" -HostName "192.168.106.51" -UserName "root" -IdentityFile "~/.ssh/nightowl_release_ed25519"
        Update-SshConfig -ConfigPath $configPath -AliasName "nightowl-release" -HostName "192.168.106.51" -UserName "root" -IdentityFile "~/.ssh/nightowl_release_ed25519"
        $configText = Get-Content -LiteralPath $configPath -Raw
        $aliasBlockCount = [regex]::Matches($configText, '(?m)^Host nightowl-release\s*$').Count
        if ($aliasBlockCount -ne 1) {
            throw "Self-test falhou: bloco Host nightowl-release duplicado."
        }
        if ($configText -notmatch '(?m)^Host existing-host\s*$') {
            throw "Self-test falhou: entrada SSH existente nao foi preservada."
        }

        $scriptText = Get-Content -LiteralPath $PSCommandPath -Raw
        $reservedPattern = '(?im)^\s*\$(home|host|pid|error|args|input|matches|psversiontable|psscriptroot|myinvocation|lastExitCode|null|true|false)\s*='
        if ($scriptText -match $reservedPattern) {
            throw "Self-test falhou: atribuicao a variavel automatica/reservada detectada."
        }
    }
    finally {
        Remove-Item -LiteralPath $testRootPath -Recurse -Force -ErrorAction SilentlyContinue
    }
    Write-Step "Self-test concluido com sucesso."
}

try {
    if ($SelfTest) {
        Invoke-SelfTest
        exit 0
    }

    if ([string]::IsNullOrWhiteSpace($RemoteHost) -or [string]::IsNullOrWhiteSpace($RemoteUser)) {
        throw "Informe -RemoteHost e -RemoteUser, ou use -SelfTest."
    }

    $sshPath = Require-Command "ssh.exe"
    $scpPath = Require-Command "scp.exe"
    $sshKeygenPath = Require-Command "ssh-keygen.exe"
    Write-Step "OpenSSH encontrado: ssh=$sshPath; scp=$scpPath; ssh-keygen=$sshKeygenPath"

    $privateKeyPath = Resolve-FullPath $KeyPath
    $sshDirectoryPath = Split-Path -Parent $privateKeyPath
    $publicKeyPath = "$privateKeyPath.pub"
    New-Item -ItemType Directory -Force -Path $sshDirectoryPath | Out-Null

    if ((Test-Path -LiteralPath $privateKeyPath) -and $Force) {
        Write-Step "Removendo chave existente por -Force: $privateKeyPath"
        Remove-Item -LiteralPath $privateKeyPath -Force
        if (Test-Path -LiteralPath $publicKeyPath) {
            Remove-Item -LiteralPath $publicKeyPath -Force
        }
    }

    if (-not (Test-Path -LiteralPath $privateKeyPath)) {
        Write-Step "Criando chave dedicada Ed25519: $privateKeyPath"
        $keygenResult = Invoke-NativeCommand -FilePath $sshKeygenPath -ArgumentList (Get-SshKeygenArguments -PrivateKeyPath $privateKeyPath -Comment "nightowl-release")
        if ($keygenResult.ExitCode -ne 0) {
            throw "ssh-keygen falhou com exit code $($keygenResult.ExitCode). stdout=$($keygenResult.Stdout.Trim()) stderr=$($keygenResult.Stderr.Trim())"
        }
    }
    else {
        Write-Step "Chave existente preservada: $privateKeyPath"
    }

    if (-not (Test-Path -LiteralPath $privateKeyPath)) {
        throw "Chave privada ausente apos inicializacao: $privateKeyPath"
    }
    if (-not (Test-Path -LiteralPath $publicKeyPath)) {
        throw "Chave publica ausente: $publicKeyPath"
    }
    Set-PrivateKeyAcl $privateKeyPath

    $sshConfigPath = Join-Path $env:USERPROFILE ".ssh\config"
    Update-SshConfig -ConfigPath $sshConfigPath -AliasName $Alias -HostName $RemoteHost -UserName $RemoteUser -IdentityFile (ConvertTo-SshConfigPath $privateKeyPath)
    Write-Step "SSH config atualizado em $sshConfigPath com alias $Alias"

    Write-Step "Copiando chave publica para $RemoteUser@$RemoteHost. A senha pode ser solicitada apenas nesta etapa inicial."
    $publicKey = Get-Content -LiteralPath $publicKeyPath -Raw
    $installResult = Install-RemotePublicKey -SshPath $sshPath -RemoteTarget "$RemoteUser@$RemoteHost" -PublicKeyText $publicKey
    if ($installResult.ExitCode -ne 0) {
        throw "Falha ao instalar chave publica no servidor. stdout=$($installResult.Stdout.Trim()) stderr=$($installResult.Stderr.Trim())"
    }

    Write-Step "Testando autenticacao sem senha com alias $Alias"
    $testResult = Test-SshAlias -SshPath $sshPath -AliasName $Alias
    if ($testResult.ExitCode -ne 0 -or $testResult.Stdout.Trim() -ne "ok") {
        throw "Autenticacao sem senha nao funcionou para $Alias. stdout=$($testResult.Stdout.Trim()) stderr=$($testResult.Stderr.Trim())"
    }

    Write-Step "Concluido. Use: ssh $Alias `"echo ok`""
    exit 0
}
catch {
    Write-Error $_.Exception.Message
    exit 1
}
