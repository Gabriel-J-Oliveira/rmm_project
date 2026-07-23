param(
    [ValidateSet("Simulated", "Integration", "WindowsVm")]
    [string]$Mode = "Simulated",

    [string]$BackendUrl = "",
    [string]$EnrollmentToken = "",
    [string]$TestEndpointPrefix = "nightowl-e2e",
    [string]$ReleaseDir = "",
    [switch]$AllowDestructive,
    [switch]$NonInteractive
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$script:ReportsDir = Join-Path $PSScriptRoot "reports"
$script:ScenariosDir = Join-Path $PSScriptRoot "scenarios"
$script:FixturesDir = Join-Path $PSScriptRoot "fixtures"
$script:StartedAt = Get-Date
$script:Results = New-Object System.Collections.ArrayList
$script:Warnings = New-Object System.Collections.ArrayList
$script:Artifacts = New-Object System.Collections.ArrayList

New-Item -ItemType Directory -Force -Path $script:ReportsDir,$script:ScenariosDir,$script:FixturesDir | Out-Null

function Add-Warning([string]$Code, [string]$Message, $Metadata = @{}) {
    [void]$script:Warnings.Add([ordered]@{
        code = $Code
        message = $Message
        metadata = $Metadata
    })
}

function Protect-Secret([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) { return "" }
    if ($Value.Length -le 8) { return "***" }
    return ("{0}...{1}" -f $Value.Substring(0, 4), $Value.Substring($Value.Length - 4))
}

function Invoke-Scenario([string]$Name, [scriptblock]$Body, [switch]$Optional) {
    $start = Get-Date
    try {
        & $Body
        [void]$script:Results.Add([ordered]@{
            name = $Name
            status = "passed"
            duration_ms = [int]((Get-Date) - $start).TotalMilliseconds
            error = ""
        })
        Write-Host "[PASS] $Name"
    }
    catch {
        $status = if ($Optional) { "skipped" } else { "failed" }
        [void]$script:Results.Add([ordered]@{
            name = $Name
            status = $status
            duration_ms = [int]((Get-Date) - $start).TotalMilliseconds
            error = $_.Exception.Message
        })
        if ($Optional) {
            Write-Host "[SKIP] $Name - $($_.Exception.Message)"
        }
        else {
            Write-Host "[FAIL] $Name - $($_.Exception.Message)"
        }
    }
}

function Assert-True($Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

function Get-FileSha256([string]$Path) {
    return (Get-FileHash -Algorithm SHA256 -Path $Path).Hash.ToLowerInvariant()
}

function Convert-VersionParts([string]$Value) {
    $clean = (($Value -split "\+")[0] -split "-", 2)[0]
    $parts = @($clean.Split(".") | ForEach-Object { [int]$_ })
    while ($parts.Count -lt 4) { $parts += 0 }
    return ,$parts[0..3]
}

function Find-ReleaseDir {
    if (-not [string]::IsNullOrWhiteSpace($ReleaseDir)) {
        return (Resolve-Path $ReleaseDir).Path
    }
    $root = Join-Path $script:RepoRoot "artifacts\nightowl-agent\releases"
    $latest = Get-ChildItem -Path $root -Directory -ErrorAction SilentlyContinue |
        Sort-Object { Convert-VersionParts $_.Name } -Descending |
        Select-Object -First 1
    if (-not $latest) {
        throw "Nenhuma release encontrada em $root. Gere uma release com scripts\Build-NightOwlAgentRelease.ps1."
    }
    return $latest.FullName
}

function Read-ZipEntryText($Zip, [string]$EntryName) {
    $entry = $Zip.GetEntry($EntryName)
    if ($null -eq $entry) { throw "Entrada ausente no ZIP: $EntryName" }
    $reader = [System.IO.StreamReader]::new($entry.Open())
    try { return $reader.ReadToEnd() }
    finally { $reader.Dispose() }
}

function Test-ForbiddenZipEntry([string]$Name) {
    $normalized = $Name.Replace("\", "/").ToLowerInvariant()
    if ($normalized -match '(^|/)(config|identity|state|logs?|diagnostics|bin|obj|artifacts|downloads|publish|releases)(/|$)') { return $true }
    if ($normalized -match 'agent\.config\.json$|agent\.identity\.json$|agent(\.|-)state\.json$|agent-dotnet\.state\.json$|update-state\.json$') { return $true }
    if ($normalized -match '\.preserved-|\.log$|\.tmp$|\.pdb$') { return $true }
    if ($normalized -match 'token|machine_id') { return $true }
    return $false
}

function Assert-ReleasePackage([string]$Path) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zipPath = Join-Path $Path "NightOwl.Agent.Windows.zip"
    $versionPath = Join-Path $Path "version.json"
    $checksumsPath = Join-Path $Path "checksums.json"
    $releaseManifestPath = Join-Path $Path "release-manifest.json"
    foreach ($required in @($zipPath, $versionPath, $checksumsPath, $releaseManifestPath)) {
        Assert-True (Test-Path $required) "Artefato ausente: $required"
    }

    $zipSha = Get-FileSha256 $zipPath
    $version = Get-Content -Path $versionPath -Raw | ConvertFrom-Json
    $checksums = Get-Content -Path $checksumsPath -Raw | ConvertFrom-Json
    Assert-True ([string]$version.sha256 -eq $zipSha) "version.json SHA-256 inconsistente."
    Assert-True ([long]$version.size -eq (Get-Item $zipPath).Length) "version.json tamanho inconsistente."
    $zipChecksum = @($checksums.files | Where-Object { $_.name -eq "NightOwl.Agent.Windows.zip" }) | Select-Object -First 1
    Assert-True ($null -ne $zipChecksum) "checksums.json sem entrada do ZIP."
    Assert-True ([string]$zipChecksum.sha256 -eq $zipSha) "checksums.json SHA-256 inconsistente."

    $zip = [System.IO.Compression.ZipFile]::OpenRead($zipPath)
    try {
        $entries = @($zip.Entries | ForEach-Object { $_.FullName.Replace("\", "/") })
        foreach ($requiredEntry in @(
            "NightOwl.Agent.Windows.exe",
            "NightOwl.Agent.Tray.exe",
            "NightOwl.Agent.Updater.exe",
            "NightOwl.Agent.Diagnostics.exe",
            "NightOwl.Agent.Shared.dll",
            "assets/icons/NightOwl.ico",
            "agent.version.json"
        )) {
            Assert-True ($entries -contains $requiredEntry) "ZIP sem arquivo obrigatorio: $requiredEntry"
        }
        foreach ($entry in $entries) {
            Assert-True (-not (Test-ForbiddenZipEntry $entry)) "ZIP contem arquivo proibido: $entry"
        }
        $agentVersion = Read-ZipEntryText -Zip $zip -EntryName "agent.version.json" | ConvertFrom-Json
        Assert-True ([string]$agentVersion.version -eq [string]$version.version) "agent.version.json e version.json divergem."
    }
    finally {
        $zip.Dispose()
    }
}

function New-TempRoot([string]$Prefix) {
    $path = Join-Path ([System.IO.Path]::GetTempPath()) ("$Prefix-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $path | Out-Null
    return $path
}

function Write-JsonFile([string]$Path, $Value) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path) | Out-Null
    $Value | ConvertTo-Json -Depth 12 | Set-Content -Path $Path -Encoding UTF8
}

function Assert-UpdateStateScenario([string]$Stage, [string]$Status, [bool]$ExpectActive, [bool]$ExpectBlocked) {
    $state = [ordered]@{
        update_id = [guid]::NewGuid().ToString()
        job_id = [guid]::NewGuid().ToString()
        from_version = "0.1.0.7"
        target_version = "0.1.0.8"
        current_stage = $Stage
        status = $Status
        started_at = (Get-Date).ToUniversalTime().ToString("o")
        updated_at = (Get-Date).ToUniversalTime().ToString("o")
        rollback_required = $Stage -eq "rollback_required"
    }
    $activeStages = @("downloading","validating","staging","creating_backup","stopping_service","replacing_files","starting_service","waiting_health_check","rollback_required","rollback_starting","rollback_stopping_service","rollback_restoring_files","rollback_starting_service","rollback_waiting_health_check")
    $active = ($Status -eq "running") -or ($activeStages -contains $Stage)
    Assert-True ($active -eq $ExpectActive) "Estado ativo inesperado para $Stage/$Status."
    $blocked = $active -or $Stage -eq "rollback_failed" -or [bool]$state.rollback_required
    Assert-True ($blocked -eq $ExpectBlocked) "Bloqueio lifecycle inesperado para $Stage/$Status."
}

function Invoke-DotnetSharedTests {
    Push-Location $script:RepoRoot
    try {
        & dotnet run --project NightOwl.Agent.Shared.Tests\NightOwl.Agent.Shared.Tests.csproj -c Release
        if ($LASTEXITCODE -ne 0) { throw "NightOwl.Agent.Shared.Tests falhou." }
    }
    finally {
        Pop-Location
    }
}

function Invoke-LifecycleStaticTests {
    Push-Location $script:RepoRoot
    try {
        & powershell -NoProfile -ExecutionPolicy Bypass -File NightOwl.Agent.Windows\scripts\Test-NightOwlAgentLifecycleScripts.ps1
        if ($LASTEXITCODE -ne 0) { throw "Lifecycle script tests falharam." }
    }
    finally {
        Pop-Location
    }
}

function Invoke-DiagnosticsFixtureTest {
    $root = New-TempRoot "NightOwlE2EDiagnostics"
    try {
        foreach ($dir in @("Config","Identity","State","State\jobs","State\pending-results","Logs","Updates\Backup","Updates\Staging","Diagnostics","AgentDotNet")) {
            New-Item -ItemType Directory -Force -Path (Join-Path $root $dir) | Out-Null
        }
        $secret = "fixture-secret-token-123456789"
        Write-JsonFile -Path (Join-Path $root "Config\agent.config.json") -Value ([ordered]@{
            serverBaseUrl = "https://nightowl.controlsul.com.br?token=$secret"
            agentToken = $secret
            machineId = "fixture-machine-id"
        })
        Write-JsonFile -Path (Join-Path $root "Identity\agent.identity.json") -Value ([ordered]@{ machine_id = "fixture-machine-id" })
        Write-JsonFile -Path (Join-Path $root "State\update-state.json") -Value ([ordered]@{
            update_id = [guid]::NewGuid().ToString()
            job_id = [guid]::NewGuid().ToString()
            from_version = "0.1.0.7"
            target_version = "0.1.0.8"
            current_stage = "rollback_failed"
            status = "failed"
            package_url = "https://nightowl.controlsul.com.br/download.zip?token=$secret"
            error_message = "Authorization: Bearer $secret"
        })
        Write-JsonFile -Path (Join-Path $root "State\pending-results\result-fixture.json") -Value ([ordered]@{
            result_id = [guid]::NewGuid().ToString()
            job_id = [guid]::NewGuid().ToString()
            job_type = "update_agent"
            status = "failed"
            created_at = (Get-Date).ToUniversalTime().ToString("o")
            attempt_count = 2
            payload = @{ token = $secret; output = "should not be included in summary" }
        })
        "Authorization: Bearer $secret`nagentToken=$secret`nhttps://nightowl.example/a?token=$secret" | Set-Content -Path (Join-Path $root "Logs\agent-dotnet.jsonl") -Encoding UTF8
        "{ invalid json" | Set-Content -Path (Join-Path $root "State\jobs\corrupt.json") -Encoding UTF8
        $env:NIGHTOWL_HOME = $root
        $out = Join-Path $root "out"
        Push-Location $script:RepoRoot
        try {
            & dotnet run --project NightOwl.Agent.Diagnostics\NightOwl.Agent.Diagnostics.csproj -c Release -- collect -NoNetworkTests -OutputPath $out -NonInteractive | Out-Null
            if ($LASTEXITCODE -gt 1) { throw "Diagnostics retornou exit code $LASTEXITCODE." }
        }
        finally {
            Pop-Location
        }
        $zip = Get-ChildItem $out -Filter "*.zip" | Select-Object -First 1
        Assert-True ($null -ne $zip) "Diagnostics nao criou ZIP."
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $extract = Join-Path $root "extract"
        [System.IO.Compression.ZipFile]::ExtractToDirectory($zip.FullName, $extract)
        foreach ($required in @("manifest.json","summary.json","config-summary.json","identity-summary.json","update-state.sanitized.json","pending-results-summary.json","logs/agent-dotnet.jsonl")) {
            Assert-True (Test-Path (Join-Path $extract $required)) "Diagnostics ZIP sem $required."
        }
        $hits = Get-ChildItem $extract -Recurse -File | Select-String -Pattern $secret,"Authorization: Bearer","?token=" -SimpleMatch
        Assert-True ($null -eq $hits) "Diagnostics ZIP contem segredo ficticio ou Authorization."
        $manifest = Get-Content -Path (Join-Path $extract "manifest.json") -Raw | ConvertFrom-Json
        foreach ($file in $manifest.files) {
            $actual = Get-FileSha256 (Join-Path $extract $file.path)
            Assert-True ($actual -eq [string]$file.sha256) "Hash invalido no manifest para $($file.path)."
        }
        [void]$script:Artifacts.Add($zip.FullName)
    }
    finally {
        $env:NIGHTOWL_HOME = $null
        try { Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue } catch {}
    }
}

function Invoke-PackageNegativeTests([string]$Path) {
    $zipPath = Join-Path $Path "NightOwl.Agent.Windows.zip"
    $tmp = New-TempRoot "NightOwlE2EPackage"
    try {
        Copy-Item -Path (Join-Path $Path "*") -Destination $tmp -Recurse -Force
        $versionPath = Join-Path $tmp "version.json"
        $version = Get-Content -Path $versionPath -Raw | ConvertFrom-Json
        $version.sha256 = "0000000000000000000000000000000000000000000000000000000000000000"
        $version | ConvertTo-Json -Depth 8 | Set-Content -Path $versionPath -Encoding UTF8
        $failed = $false
        try { Assert-ReleasePackage $tmp } catch { $failed = $true }
        Assert-True $failed "Release com hash invalido deveria falhar."

        $broken = Join-Path $tmp "broken"
        New-Item -ItemType Directory -Force -Path $broken | Out-Null
        Set-Content -Path (Join-Path $broken "agent.version.json") -Value '{"version":"0.0.0"}' -Encoding UTF8
        Compress-Archive -Path (Join-Path $broken "*") -DestinationPath (Join-Path $tmp "NightOwl.Agent.Windows.zip") -Force
        $version.sha256 = Get-FileSha256 (Join-Path $tmp "NightOwl.Agent.Windows.zip")
        $version.size = (Get-Item (Join-Path $tmp "NightOwl.Agent.Windows.zip")).Length
        $version | ConvertTo-Json -Depth 8 | Set-Content -Path $versionPath -Encoding UTF8
        $checksums = Get-Content -Path (Join-Path $tmp "checksums.json") -Raw | ConvertFrom-Json
        foreach ($file in $checksums.files) {
            if ($file.name -eq "NightOwl.Agent.Windows.zip") {
                $file.sha256 = $version.sha256
                $file.size = $version.size
            }
        }
        $checksums | ConvertTo-Json -Depth 8 | Set-Content -Path (Join-Path $tmp "checksums.json") -Encoding UTF8
        $failed = $false
        try { Assert-ReleasePackage $tmp } catch { $failed = $true }
        Assert-True $failed "Pacote sem executaveis obrigatorios deveria falhar."
    }
    finally {
        try { Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue } catch {}
    }
}

function Invoke-Simulated {
    $release = Find-ReleaseDir
    Invoke-Scenario "release.valida" { Assert-ReleasePackage $release }
    Invoke-Scenario "release.hash_invalido_e_exe_obrigatorio_ausente" { Invoke-PackageNegativeTests $release }
    Invoke-Scenario "update_state.valido" { Assert-UpdateStateScenario "completed" "completed" $false $false }
    Invoke-Scenario "update_state.corrompido" {
        $bad = "{ invalid json"
        $failed = $false
        try { $bad | ConvertFrom-Json | Out-Null } catch { $failed = $true }
        Assert-True $failed "update-state corrompido deveria falhar parsing."
    }
    Invoke-Scenario "update_state.rollback_requerido" { Assert-UpdateStateScenario "rollback_required" "running" $true $true }
    Invoke-Scenario "update_state.rollback_failed_bloqueia_repair" { Assert-UpdateStateScenario "rollback_failed" "failed" $false $true }
    Invoke-Scenario "jobs_fila_idempotencia_timeout_concorrencia" { Invoke-DotnetSharedTests }
    Invoke-Scenario "lifecycle.bloqueios_e_modos" { Invoke-LifecycleStaticTests }
    Invoke-Scenario "diagnostics.sanitizado" { Invoke-DiagnosticsFixtureTest }
}

function Invoke-Integration {
    if ([string]::IsNullOrWhiteSpace($BackendUrl) -or [string]::IsNullOrWhiteSpace($EnrollmentToken)) {
        throw "Modo Integration exige -BackendUrl e -EnrollmentToken."
    }
    $base = $BackendUrl.TrimEnd("/")
    $machineId = "$TestEndpointPrefix-$([guid]::NewGuid().ToString('N'))"
    $hostname = "$TestEndpointPrefix-$env:COMPUTERNAME"
    $agentToken = ""
    $headers = @{}
    Invoke-Scenario "integration.enrollment" {
        $body = @{
            enrollment_token = $EnrollmentToken
            machine_id = $machineId
            hostname = $hostname
            fqdn = $hostname
            os_name = "NightOwl E2E"
            agent_version = "0.0.0-e2e"
            serial_number = "E2E"
        } | ConvertTo-Json -Depth 5
        $response = Invoke-RestMethod -Method Post -Uri "$base/api/agent/enroll/" -Body $body -ContentType "application/json" -TimeoutSec 30
        $script:IntegrationAgentToken = [string]$response.agent_token
        Assert-True (-not [string]::IsNullOrWhiteSpace($script:IntegrationAgentToken)) "Enrollment nao retornou agent_token."
    }
    $agentToken = [string]$script:IntegrationAgentToken
    $headers = @{ Authorization = "Bearer $agentToken" }
    Invoke-Scenario "integration.heartbeat_collect_jobs_status_result_idempotente" {
        $heartbeat = @{ machine_id = $machineId; hostname = $hostname; agent_version = "0.0.0-e2e"; agent_mode = "e2e"; timestamp = (Get-Date).ToUniversalTime().ToString("o") } | ConvertTo-Json
        Invoke-RestMethod -Method Post -Uri "$base/api/agent/heartbeat/" -Headers $headers -Body $heartbeat -ContentType "application/json" -TimeoutSec 30 | Out-Null
        $collect = @{ machine_id = $machineId; agent_version = "0.0.0-e2e"; collected_at = (Get-Date).ToUniversalTime().ToString("o"); system = @{ hostname = $hostname }; hardware = @{}; network = @{}; disks = @(); software = @(); security = @{}; patches = @{} } | ConvertTo-Json -Depth 8
        Invoke-RestMethod -Method Post -Uri "$base/api/agent/collect/" -Headers $headers -Body $collect -ContentType "application/json" -TimeoutSec 30 | Out-Null
        $jobs = Invoke-RestMethod -Method Get -Uri "$base/api/agent/jobs/pull/" -Headers $headers -TimeoutSec 30
        Assert-True ($jobs.PSObject.Properties.Name -contains "jobs") "jobs/pull nao retornou envelope { jobs: [] }."
        $resultId = [guid]::NewGuid().ToString()
        $result = @{ result_id = $resultId; job_id = [guid]::NewGuid().ToString(); status = "completed"; started_at = (Get-Date).ToUniversalTime().ToString("o"); finished_at = (Get-Date).ToUniversalTime().ToString("o"); duration_seconds = 0; exit_code = 0; stdout = ""; stderr = ""; result = @{ e2e = $true }; error_message = "" } | ConvertTo-Json -Depth 8
        $resultHeaders = @{ Authorization = "Bearer $agentToken"; "Idempotency-Key" = $resultId }
        Invoke-RestMethod -Method Post -Uri "$base/api/agent/jobs/result/" -Headers $resultHeaders -Body $result -ContentType "application/json" -TimeoutSec 30 | Out-Null
        Invoke-RestMethod -Method Post -Uri "$base/api/agent/jobs/result/" -Headers $resultHeaders -Body $result -ContentType "application/json" -TimeoutSec 30 | Out-Null
        $status = @{ machine_id = $machineId; agent_version = "0.0.0-e2e"; service_status = "simulated"; pending_result_count = 0; running_job_count = 0 } | ConvertTo-Json -Depth 8
        Invoke-RestMethod -Method Post -Uri "$base/api/agent/status/" -Headers $headers -Body $status -ContentType "application/json" -TimeoutSec 30 | Out-Null
    }
    Add-Warning "INTEGRATION_CLEANUP_MANUAL" "Endpoint descartavel criado; desative/remova pelo painel se necessario." @{ machine_id = $machineId; token = Protect-Secret $EnrollmentToken }
}

function Invoke-WindowsVm {
    if (-not $AllowDestructive) {
        throw "WINDOWSVM_DESTRUCTIVE_REFUSED: use -Mode WindowsVm -AllowDestructive somente em VM descartavel."
    }
    Invoke-Scenario "windowsvm.roteiro" {
        Add-Warning "WINDOWSVM_MANUAL_RUNBOOK" "Execute em VM descartavel: install, enrollment, heartbeat, inventario, ping, collect_disks, update, rollback, reboot durante waiting_health_check, repair, uninstall normal, reinstall e purge." @{}
    }
}

function Write-Reports {
    $completedAt = Get-Date
    $stamp = $completedAt.ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
    $passed = @($script:Results | Where-Object { $_.status -eq "passed" }).Count
    $failed = @($script:Results | Where-Object { $_.status -eq "failed" }).Count
    $skipped = @($script:Results | Where-Object { $_.status -eq "skipped" }).Count
    $report = [ordered]@{
        mode = $Mode
        started_at = $script:StartedAt.ToUniversalTime().ToString("o")
        completed_at = $completedAt.ToUniversalTime().ToString("o")
        duration_ms = [int]($completedAt - $script:StartedAt).TotalMilliseconds
        environment = [ordered]@{
            machine = $env:COMPUTERNAME
            user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
            repo_root = $script:RepoRoot
            backend_url = if ([string]::IsNullOrWhiteSpace($BackendUrl)) { "" } else { $BackendUrl.TrimEnd("/") }
            enrollment_token = if ([string]::IsNullOrWhiteSpace($EnrollmentToken)) { "" } else { Protect-Secret $EnrollmentToken }
        }
        passed = $passed
        failed = $failed
        skipped = $skipped
        scenarios = $script:Results
        warnings = $script:Warnings
        artifacts = $script:Artifacts
    }
    $jsonPath = Join-Path $script:ReportsDir "e2e-$stamp.json"
    $txtPath = Join-Path $script:ReportsDir "e2e-$stamp.txt"
    $report | ConvertTo-Json -Depth 12 | Set-Content -Path $jsonPath -Encoding UTF8
    $lines = @()
    $lines += "NightOwl Agent E2E"
    $lines += "Mode: $Mode"
    $lines += "Passed: $passed"
    $lines += "Failed: $failed"
    $lines += "Skipped: $skipped"
    $lines += "Duration ms: $($report.duration_ms)"
    $lines += ""
    foreach ($scenario in $script:Results) {
        $lines += ("[{0}] {1} {2}ms {3}" -f $scenario.status.ToUpperInvariant(), $scenario.name, $scenario.duration_ms, $scenario.error)
    }
    $lines | Set-Content -Path $txtPath -Encoding UTF8
    Write-Host "Reports:"
    Write-Host $jsonPath
    Write-Host $txtPath
}

try {
    switch ($Mode) {
        "Simulated" { Invoke-Simulated }
        "Integration" { Invoke-Integration }
        "WindowsVm" { Invoke-WindowsVm }
    }
}
catch {
    [void]$script:Results.Add([ordered]@{
        name = "$Mode.startup"
        status = "failed"
        duration_ms = 0
        error = $_.Exception.Message
    })
    if ($Mode -eq "WindowsVm" -and $_.Exception.Message -like "WINDOWSVM_DESTRUCTIVE_REFUSED*") {
        Write-Reports
        exit 5
    }
    Write-Reports
    if ($Mode -eq "Integration") { exit 4 }
    exit 2
}

Write-Reports
$failures = @($script:Results | Where-Object { $_.status -eq "failed" }).Count
if ($failures -gt 0) { exit 1 }
exit 0
