# Local inspection artifact only. This is NOT the release builder and cannot publish/sign/install.
param([string]$SourceRoot = '')
$ErrorActionPreference = 'Stop'
$toolRepo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$repo = if ([string]::IsNullOrWhiteSpace($SourceRoot)) { $toolRepo } else { (Resolve-Path -LiteralPath $SourceRoot).Path }
$id = [datetime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [guid]::NewGuid().ToString('N')
$output = Join-Path $toolRepo ('artifacts/rc46-development/' + $id)
$publish = Join-Path $output 'build-output'
$payload = Join-Path $output 'payload'
[IO.Directory]::CreateDirectory($payload) | Out-Null
[IO.Directory]::CreateDirectory($publish) | Out-Null
$version = '0.1.1.0-rc46-cleanup-dev'
foreach ($name in @('Windows','Tray','Updater','Uninstaller','Diagnostics')) {
    $project = Join-Path $repo ("NightOwl.Agent.$name/NightOwl.Agent.$name.csproj")
    & dotnet publish $project -c Release -r win-x64 --self-contained true -o $publish --nologo "-p:Version=$version" '-p:AssemblyVersion=0.1.1.0' '-p:FileVersion=0.1.1.0' '-p:InformationalVersion=0.1.1.0-rc46-cleanup-dev'
    if ($LASTEXITCODE -ne 0) { throw "Development publish failed: $name" }
}
foreach ($file in Get-ChildItem -LiteralPath $publish -Recurse -File) {
    $relative = $file.FullName.Substring($publish.Length + 1)
    if ($file.Extension -in @('.pdb','.ps1','.log','.tmp','.lock') -or
        $file.Name -in @('agent.config.json','agent.identity.json','agent.state.json','agent-dotnet.state.json',
            'update-state.json','release-trust-roots.json','release-public-keys.json','release-manifest.json',
            'agent.version.json','version.json','checksums.json')) { continue }
    $destination = Join-Path $payload $relative
    [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($destination)) | Out-Null
    Copy-Item -LiteralPath $file.FullName -Destination $destination
}
foreach ($name in @('Windows','Tray','Updater','Uninstaller','Diagnostics')) {
    if (-not (Test-Path -LiteralPath (Join-Path $payload "NightOwl.Agent.$name.exe"))) { throw "Missing executable: $name" }
}
[IO.File]::WriteAllText((Join-Path $payload 'NOT-FOR-INSTALL.txt'),
    "UNSIGNED DEVELOPMENT ARTIFACT. NOT PUBLISHABLE. NOT FOR INSTALLATION.`nNo trusted roots, release manifest or signature. Use the official release builder in the authorized environment for Pilot.`n")
$files = @(Get-ChildItem -LiteralPath $payload -Recurse -File | Sort-Object FullName | ForEach-Object {
    [ordered]@{path=$_.FullName.Substring($payload.Length + 1).Replace('\','/'); size=$_.Length;
        sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()}
})
$sourceFiles = @(& git -C $repo -c core.quotepath=false ls-files --cached --others --exclude-standard)
$inputs = @($sourceFiles | Sort-Object -Unique | Where-Object { $_ -match '\.(cs|csproj|ps1|ico)$' } | ForEach-Object {
    [ordered]@{path=$_; sha256=(Get-FileHash -LiteralPath (Join-Path $repo $_) -Algorithm SHA256).Hash.ToLowerInvariant()}
})
$manifest = [ordered]@{
    schema_version=1; artifact_type='UNSIGNED_DEVELOPMENT_NOT_FOR_INSTALL'; publishable=$false; signed=$false
    version=$version; runtime='win-x64'; sdk=(& dotnet --version); received_commit=(& git -C $repo rev-parse HEAD)
    source_branch=(& git -C $repo branch --show-current); dirty_worktree=([bool](& git -C $repo status --porcelain))
    files=$files; source_inputs=$inputs; file_count=$files.Count; payload_bytes=($files | ForEach-Object { [long]$_.size } | Measure-Object -Sum).Sum
    missing_release_requirements=@('authorized trust roots','release signing key','signed Pilot validation','VM lifecycle/Tray gates')
}
[IO.File]::WriteAllText((Join-Path $output 'development-manifest.json'), ($manifest | ConvertTo-Json -Depth 8), (New-Object Text.UTF8Encoding($false)))
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zipPath = Join-Path $output 'NightOwl-RC46-development-UNSIGNED-NOT-FOR-INSTALL.zip'
$writer = [IO.Compression.ZipFile]::Open($zipPath, [IO.Compression.ZipArchiveMode]::Create)
try {
    foreach ($file in $files) {
        # Explicit forward-slash entry names also work under Windows PowerShell/.NET Framework.
        [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($writer, (Join-Path $payload $file.path),
            $file.path, [IO.Compression.CompressionLevel]::Optimal) | Out-Null
    }
}
finally { $writer.Dispose() }
$zip = [IO.Compression.ZipFile]::OpenRead($zipPath)
try {
    if ($zip.Entries.Count -ne $files.Count) { throw 'Development archive count mismatch' }
    foreach ($file in $files) {
        $entry = $zip.GetEntry($file.path)
        if ($null -eq $entry -or $entry.Length -ne $file.size) { throw 'Development archive missing file/size mismatch' }
        $stream = $entry.Open(); $sha = [Security.Cryptography.SHA256]::Create()
        try { $hash = ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-','').ToLowerInvariant() }
        finally { $sha.Dispose(); $stream.Dispose() }
        if ($hash -ne $file.sha256) { throw 'Development archive hash mismatch' }
    }
}
finally { $zip.Dispose() }
[ordered]@{path=$zipPath; sha256=(Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant();
    size=(Get-Item -LiteralPath $zipPath).Length; file_count=$files.Count; payload_bytes=$manifest.payload_bytes;
    publishable=$false; zip_verified=$true} | ConvertTo-Json |
    Set-Content -LiteralPath (Join-Path $output 'artifact-verification.json') -Encoding UTF8
Write-Output "UNSIGNED DEVELOPMENT ARTIFACT VERIFIED: $output"
