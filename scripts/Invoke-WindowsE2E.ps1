$ErrorActionPreference = 'Stop'

# GitHub's hosted runner is elevated. The real assisted ConPTY contract requires
# an explicitly authenticated interactive standard user, not a reduced test token.
$user = 'nw-e2e-' + [guid]::NewGuid().ToString('N').Substring(0, 10)
$password = ConvertTo-SecureString ('Nw!' + [guid]::NewGuid().ToString('N') + '9a') -AsPlainText -Force
$credential = [pscredential]::new(".\$user", $password)
$workspace = [IO.Path]::GetFullPath($env:GITHUB_WORKSPACE).TrimEnd('\')
$work = [IO.Path]::GetFullPath((Join-Path $workspace '.windows-e2e'))
if ([IO.Path]::GetDirectoryName($work) -ine $workspace) { throw 'Invalid test workspace path' }
$created = $false
try {
    $account = New-LocalUser -Name $user -Password $password -AccountNeverExpires
    $created = $true
    New-Item -ItemType Directory -Path $work -Force | Out-Null
    $acl = Get-Acl $env:GITHUB_WORKSPACE
    $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
        "$env:COMPUTERNAME\$user", 'Modify', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
    $acl.AddAccessRule($rule)
    Set-Acl $env:GITHUB_WORKSPACE $acl
    $env:TEMP = $work
    $env:TMP = $work
    $env:DOTNET_CLI_HOME = $work
    # Start-Process -Credential inherits this runner's environment. Do not pass
    # runneradmin's inaccessible profile directories to the standard user.
    $env:USERPROFILE = $work
    $env:APPDATA = Join-Path $work 'AppData\Roaming'
    $env:LOCALAPPDATA = Join-Path $work 'AppData\Local'
    New-Item -ItemType Directory -Path $env:APPDATA, $env:LOCALAPPDATA -Force | Out-Null
    $python = (Get-Command python -CommandType Application | Select-Object -First 1).Source
    $out = Join-Path $work 'stdout.txt'
    $err = Join-Path $work 'stderr.txt'
    $dotnet = (Get-Command dotnet -CommandType Application | Select-Object -First 1).Source
    $chromeVersion = (Get-Item -LiteralPath 'C:\Program Files\Google\Chrome\Application\chrome.exe').VersionInfo.ProductVersion
    Write-Output ("CI_CHROME_VERSION=" + $chromeVersion)
    $facts = Start-Process -FilePath $dotnet -ArgumentList @('NightOwl.Agent.Windows.Tests/bin/Release/net8.0-windows/NightOwl.Agent.Windows.Tests.dll', '--ci-terminal-facts') `
        -Credential $credential -WorkingDirectory $env:GITHUB_WORKSPACE -RedirectStandardOutput $out -RedirectStandardError $err -Wait -PassThru
    Get-Content $out
    if ($facts.ExitCode -ne 0) { throw "Windows identity probe failed with exit code $($facts.ExitCode)" }
    $arguments = @('manage.py', 'test', 'agents', '--tag', 'windows_e2e', '--testrunner',
        'agents.ci_runner.WindowsE2ERunner', '--noinput', '--verbosity', '2')
    $process = Start-Process -FilePath $python -ArgumentList $arguments -Credential $credential `
        -WorkingDirectory $env:GITHUB_WORKSPACE -RedirectStandardOutput $out -RedirectStandardError $err -Wait -PassThru
    Get-Content $out
    Get-Content $err
    if ($process.ExitCode -ne 0) { throw "Windows E2E failed with exit code $($process.ExitCode)" }
}
finally {
    if ($created) {
        # Only processes owned by the uniquely created test identity are eligible.
        $testSid = $account.SID.Value
        $stopped = 0
        foreach ($candidate in @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)) {
            try {
                $owner = Invoke-CimMethod -InputObject $candidate -MethodName GetOwnerSid -ErrorAction Stop
                if ($owner.ReturnValue -eq 0 -and $owner.Sid -eq $testSid) {
                    Stop-Process -Id $candidate.ProcessId -Force -ErrorAction Stop
                    Wait-Process -Id $candidate.ProcessId -Timeout 5 -ErrorAction SilentlyContinue
                    $stopped++
                }
            } catch { }
        }
        Write-Output ("CI_TEST_IDENTITY_CLEANUP=" + (@{ stopped = $stopped } | ConvertTo-Json -Compress))
    }
    if ($created) { Remove-LocalUser -Name $user -ErrorAction Continue }
    if (Test-Path -LiteralPath $work) {
        $workItem = Get-Item -LiteralPath $work -Force
        if (($workItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Test workspace is a reparse point' }
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction Continue
    }
}
