$ErrorActionPreference = 'Stop'

# GitHub's hosted runner is elevated. The real assisted ConPTY contract requires
# an explicitly authenticated interactive standard user, not a reduced test token.
$user = 'nw-e2e-' + [guid]::NewGuid().ToString('N').Substring(0, 10)
$password = ConvertTo-SecureString ('Nw!' + [guid]::NewGuid().ToString('N') + '9a') -AsPlainText -Force
$credential = [pscredential]::new(".\$user", $password)
$work = Join-Path $env:GITHUB_WORKSPACE '.windows-e2e'
$created = $false
try {
    New-LocalUser -Name $user -Password $password -AccountNeverExpires | Out-Null
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
    $python = (Get-Command python -CommandType Application | Select-Object -First 1).Source
    $out = Join-Path $work 'stdout.txt'
    $err = Join-Path $work 'stderr.txt'
    $dotnet = (Get-Command dotnet -CommandType Application | Select-Object -First 1).Source
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
    if ($created) { Remove-LocalUser -Name $user -ErrorAction Continue }
    if (Test-Path $work) { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction Continue }
}
