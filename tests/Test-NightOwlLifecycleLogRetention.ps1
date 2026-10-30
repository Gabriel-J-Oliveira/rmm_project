$ErrorActionPreference = 'Stop'
$root = Join-Path ([IO.Path]::GetTempPath()) ('NightOwlLogTest-' + [guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($root) | Out-Null
try {
    foreach ($name in @('Install-NightOwlAgentDotNet.ps1', 'Uninstall-NightOwlAgentDotNet.ps1')) {
        $file = Join-Path $PSScriptRoot ('../NightOwl.Agent.Windows/scripts/' + $name)
        $errors = $null
        $ast = [Management.Automation.Language.Parser]::ParseFile((Resolve-Path $file), [ref]$null, [ref]$errors)
        if ($errors.Count) { throw 'Lifecycle script parse failed' }
        $function = $ast.Find({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Write-NightOwlBoundedLog' }, $true)
        if ($null -eq $function) { throw 'Bounded logger missing' }
        # Load only the logger. Never invoke the installer/uninstaller entrypoint.
        . ([scriptblock]::Create($function.Extent.Text))
        $path = Join-Path $root ($name + '.jsonl')
        foreach ($i in 1..60) {
            Write-NightOwlBoundedLog -Path $path -Json (@{event_type='test'; number=$i; text=('x'*70)} | ConvertTo-Json -Compress) -MaxBytes 1024 -Archives 3 -MaxRecordBytes 512
        }
        $owned = @(Get-ChildItem -LiteralPath $root -File | Where-Object { $_.Name.StartsWith($name + '.jsonl') -and $_.Extension -ne '.lock' })
        if ($owned.Count -ne 4 -or @($owned | Where-Object Length -gt 1024).Count) { throw 'Size/count log quota failed' }
        foreach ($item in $owned) { foreach ($line in [IO.File]::ReadAllLines($item.FullName)) { $null = $line | ConvertFrom-Json } }
        [IO.File]::SetLastWriteTimeUtc(($path + '.3'), [datetime]::UtcNow.AddDays(-40))
        Write-NightOwlBoundedLog -Path $path -Json '{"event_type":"age"}' -MaxBytes 1024 -Archives 3 -MaxRecordBytes 512
        if ([IO.File]::Exists(($path + '.3')) -and [IO.File]::GetLastWriteTimeUtc(($path + '.3')) -lt [datetime]::UtcNow.AddDays(-30)) { throw 'Age retention failed' }
        $secretPath = $path + '.sanitized'
        Write-NightOwlBoundedLog -Path $secretPath -Json '{"agentToken":"fixture-secret-token","message":"Bearer abcdefghijklmnopqrstuvwxyz","url":"https://isolated.invalid/a?token=fixture-secret-token"}'
        $text = [IO.File]::ReadAllText($secretPath)
        if ($text.Contains('fixture-secret-token') -or $text.Contains('abcdefghijklmnopqrstuvwxyz') -or $text.Contains('?token=')) { throw 'Lifecycle log redaction failed' }
        $locked = $path + '.locked'
        [IO.File]::WriteAllText($locked, ('x'*1020))
        $reader = [IO.File]::Open($locked, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
        $failed = $false
        try { Write-NightOwlBoundedLog -Path $locked -Json '{"event_type":"blocked"}' -MaxBytes 1024 -MaxRecordBytes 512 }
        catch { $failed = $true }
        finally { $reader.Dispose() }
        if (-not $failed -or (Get-Item -LiteralPath $locked).Length -ne 1020) { throw 'Denied rotation must preserve active evidence and bound growth' }
        Write-NightOwlBoundedLog -Path $locked -Json '{"event_type":"recovered"}' -MaxBytes 1024 -MaxRecordBytes 512
        if (-not [IO.File]::ReadAllText($locked).Contains('recovered')) { throw 'Rotation did not recover' }
        $partial = $path + '.partial'
        [IO.File]::WriteAllText($partial, '{"unfinished":')
        Write-NightOwlBoundedLog -Path $partial -Json '{"event_type":"after_partial","message":"token=fixture-secret-never-print"}'
        $lines = [IO.File]::ReadAllLines($partial)
        if ($lines.Count -ne 2 -or ($lines[1] | ConvertFrom-Json).event_type -ne 'after_partial' -or $lines[1].Contains('fixture-secret-never-print')) { throw 'Partial record recovery/redaction failed' }
    }
    Write-Output 'PASS lifecycle log retention: standalone helpers, quotas/age, redaction, blocked reader and recovery; no installation.'
}
finally {
    $resolvedRoot = [IO.Path]::GetFullPath($root)
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not $resolvedRoot.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -or
        -not [IO.Path]::GetFileName($resolvedRoot).StartsWith('NightOwlLogTest-')) { throw 'Unsafe fixture cleanup' }
    if (Test-Path -LiteralPath $resolvedRoot) { Remove-Item -LiteralPath $resolvedRoot -Recurse -Force }
}
