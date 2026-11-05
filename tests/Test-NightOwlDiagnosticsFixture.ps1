$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path ([IO.Path]::GetTempPath()) ('NightOwlDiagnosticsTest-' + [guid]::NewGuid().ToString('N'))))
$previousHome = $env:NIGHTOWL_HOME
try {
    foreach ($folder in @('Config','Identity','State','Logs')) {
        New-Item -ItemType Directory -Force -Path (Join-Path $root $folder) | Out-Null
    }
    $sentinel = 'fixture-secret-token-never-print'
    @{serverBaseUrl='https://isolated.invalid'; agentToken=$sentinel; machineId='synthetic-fixture'} |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $root 'Config\agent.config.json') -Encoding UTF8
    @{machine_id='synthetic-fixture'} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $root 'Identity\agent.identity.json') -Encoding UTF8
    "Authorization: Bearer $sentinel`nagentToken=$sentinel`nhttps://isolated.invalid/a?token=$sentinel" |
        Set-Content -LiteralPath (Join-Path $root 'Logs\agent-dotnet.jsonl') -Encoding UTF8
    $env:NIGHTOWL_HOME=$root
    $dll=Join-Path $PSScriptRoot '../NightOwl.Agent.Diagnostics/bin/Release/net8.0-windows/NightOwl.Agent.Diagnostics.dll'
    if (-not (Test-Path -LiteralPath $dll)) { throw 'Build Diagnostics Release before running this fixture.' }
    $out=Join-Path $root 'out'
    & dotnet $dll collect -NoNetworkTests -OutputPath $out -NonInteractive | Out-Null
    if ($LASTEXITCODE -notin @(0,1)) { throw 'Diagnostics fixture process failed.' }
    $archives=@(Get-ChildItem -LiteralPath $out -Filter '*.zip')
    if ($archives.Count -ne 1) { throw 'Expected exactly one diagnostic archive.' }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $extract=Join-Path $root 'extract'
    [IO.Compression.ZipFile]::ExtractToDirectory($archives[0].FullName,$extract)
    foreach ($file in @('manifest.json','summary.json','config-summary.json','identity-summary.json','logs/agent-dotnet.jsonl')) {
        if (-not (Test-Path -LiteralPath (Join-Path $extract $file))) { throw 'Diagnostic fixture is missing a required sanitized file.' }
    }
    $leak=Get-ChildItem -LiteralPath $extract -Recurse -File | Select-String -SimpleMatch -Pattern $sentinel,'Authorization: Bearer','?token='
    if ($null -ne $leak) { throw 'Sanitization assertion failed; sensitive test output withheld.' }
    $manifest=Get-Content -LiteralPath (Join-Path $extract 'manifest.json') -Raw | ConvertFrom-Json
    foreach ($entry in $manifest.files) {
        $hash=(Get-FileHash -LiteralPath (Join-Path $extract $entry.path) -Algorithm SHA256).Hash
        if ($hash -ne $entry.sha256) { throw 'Sanitized manifest checksum mismatch.' }
    }
    Write-Output ('PASS_DIAGNOSTICS_FIXTURE=YES files={0} archive_bytes={1} network_tests=DISABLED' -f @($manifest.files).Count,$archives[0].Length)
}
finally {
    $env:NIGHTOWL_HOME=$previousHome
    $prefix=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not $root.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe test cleanup path.' }
    if (Test-Path -LiteralPath $root) { Remove-Item -LiteralPath $root -Recurse -Force }
}
