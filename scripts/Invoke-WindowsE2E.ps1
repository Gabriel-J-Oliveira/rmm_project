param([switch]$BrowserOnly)
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
$profileRoot = $null
try {
    $account = New-LocalUser -Name $user -Password $password -AccountNeverExpires
    $created = $true
    New-Item -ItemType Directory -Path $work -Force | Out-Null
    $acl = Get-Acl $env:GITHUB_WORKSPACE
    $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
        "$env:COMPUTERNAME\$user", 'Modify', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
    $acl.AddAccessRule($rule)
    Set-Acl $env:GITHUB_WORKSPACE $acl
    $env:DOTNET_CLI_HOME = $work
    $python = (Get-Command python -CommandType Application | Select-Object -First 1).Source
    $out = Join-Path $work 'stdout.txt'
    $err = Join-Path $work 'stderr.txt'
    $dotnet = (Get-Command dotnet -CommandType Application | Select-Object -First 1).Source
    # Materialize this uniquely-created account's real Windows profile first.
    $facts = Start-Process -FilePath $dotnet -ArgumentList @('NightOwl.Agent.Windows.Tests/bin/Release/net8.0-windows/NightOwl.Agent.Windows.Tests.dll', '--ci-terminal-facts') `
        -Credential $credential -LoadUserProfile -WorkingDirectory $env:GITHUB_WORKSPACE -RedirectStandardOutput $out -RedirectStandardError $err -Wait -PassThru
    Get-Content $out
    if ($facts.ExitCode -ne 0) { throw "Windows identity probe failed with exit code $($facts.ExitCode)" }
    $record = Get-CimInstance Win32_UserProfile -Filter "SID='$($account.SID.Value)'" -ErrorAction Stop
    if ($null -eq $record -or -not $record.LocalPath) { throw 'Temporary Windows profile unavailable' }
    $candidate = [IO.Path]::GetFullPath($record.LocalPath).TrimEnd('\')
    $profileBase = [Environment]::ExpandEnvironmentVariables((Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList').ProfilesDirectory)
    $profileBase = [IO.Path]::GetFullPath($profileBase).TrimEnd('\')
    if ([IO.Path]::GetDirectoryName($candidate) -ine $profileBase -or
        -not [IO.Path]::GetFileName($candidate).StartsWith($user, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Temporary Windows profile path rejected'
    }
    $profileItem = Get-Item -LiteralPath $candidate -Force
    if (($profileItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Temporary Windows profile is a reparse point' }
    $profileRoot = $candidate
    # Credentialed children otherwise inherit runneradmin's environment. Keep
    # every profile variable consistent with the loaded temporary user's token.
    $env:USERPROFILE = $profileRoot
    $env:APPDATA = Join-Path $profileRoot 'AppData\Roaming'
    $env:LOCALAPPDATA = Join-Path $profileRoot 'AppData\Local'
    $env:TEMP = Join-Path $env:LOCALAPPDATA 'Temp'
    $env:TMP = $env:TEMP
    New-Item -ItemType Directory -Path $env:APPDATA, $env:LOCALAPPDATA, $env:TEMP -Force | Out-Null
    if ($BrowserOnly) {
        $env:NIGHTOWL_TEST_BROWSER_E2E = '1'
        $arguments = @('manage.py', 'test', 'agents.test_terminal_browser.TerminalBrowserSmokeTests.test_real_browser_real_pty_simulated_local_consent_click',
            '--testrunner', 'agents.ci_runner.WindowsBrowserE2ERunner', '--noinput', '--verbosity', '2')
    } else {
        $arguments = @('manage.py', 'test', 'agents', '--tag', 'windows_e2e', '--testrunner',
            'agents.ci_runner.WindowsE2ERunner', '--noinput', '--verbosity', '2')
    }
    $process = Start-Process -FilePath $python -ArgumentList $arguments -Credential $credential -LoadUserProfile `
        -WorkingDirectory $env:GITHUB_WORKSPACE -RedirectStandardOutput $out -RedirectStandardError $err -Wait -PassThru
    Get-Content $out
    Get-Content $err
    $e2eExitCode = $process.ExitCode
    if ($BrowserOnly -and $e2eExitCode -eq 0) {
        # Exercise the central execution-policy UI in its own isolated Django
        # test database and synthetic endpoint records, using the same pinned
        # Chrome and standard Windows test identity as the terminal smoke.
        $automationArguments = @('manage.py', 'test',
            'agents.test_automation.AutomationBrowserTests.test_real_ui_and_persisted_automations',
            '--testrunner', 'agents.ci_runner.WindowsBrowserE2ERunner', '--noinput', '--verbosity', '2')
        $automationProcess = Start-Process -FilePath $python -ArgumentList $automationArguments -Credential $credential -LoadUserProfile `
            -WorkingDirectory $env:GITHUB_WORKSPACE -RedirectStandardOutput $out -RedirectStandardError $err -Wait -PassThru
        Get-Content $out
        Get-Content $err
        if ($automationProcess.ExitCode -ne 0) { throw "Execution policies browser E2E failed with exit code $($automationProcess.ExitCode)" }

        # Endpoint policy actions run in a separate synthetic database and must
        # prove the browser submits a single-endpoint invocation, not a fleet run.
        $endpointPolicyArguments = @('manage.py', 'test',
            'agents.test_automation.AutomationBrowserTests.test_endpoint_policy_run_is_scoped_and_confirmed_in_real_ui',
            '--testrunner', 'agents.ci_runner.WindowsBrowserE2ERunner', '--noinput', '--verbosity', '2')
        $endpointPolicyProcess = Start-Process -FilePath $python -ArgumentList $endpointPolicyArguments -Credential $credential -LoadUserProfile `
            -WorkingDirectory $env:GITHUB_WORKSPACE -RedirectStandardOutput $out -RedirectStandardError $err -Wait -PassThru
        Get-Content $out
        Get-Content $err
        if ($endpointPolicyProcess.ExitCode -ne 0) { throw "Endpoint policy browser E2E failed with exit code $($endpointPolicyProcess.ExitCode)" }
    }

    # The independent probe runs AFTER the full E2E, even on failure: it cannot
    # warm Chrome before the smoke or turn an E2E failure into success.
    $node = (Get-Command node -CommandType Application | Select-Object -First 1).Source
    $probeArgs = @('tests/frontend/test_chrome_cdp_isolated.cjs')
    $negative = Start-Process -FilePath $node -ArgumentList ($probeArgs + '--missing-executable') -Credential $credential -LoadUserProfile `
        -WorkingDirectory $env:GITHUB_WORKSPACE -RedirectStandardOutput $out -RedirectStandardError $err -Wait -PassThru
    $negativeLines = @(Get-Content $out)
    $negativeLines
    $negativeData = @($negativeLines | Where-Object { $_ -like 'CHROME_CDP_ISOLATED=*' })
    $negativeValid = $false
    if ($negativeData.Count -eq 1) {
        $negativeResult = $negativeData[0].Substring('CHROME_CDP_ISOLATED='.Length) | ConvertFrom-Json
        $negativeValid = $negative.ExitCode -ne 0 -and $negativeResult.error -eq 'chrome_not_found' -and
            $negativeResult.process_exited -and $negativeResult.profile_removed -and $negativeResult.remaining_profile_processes -eq 0
    }
    $configuredChrome = $env:NIGHTOWL_TEST_CHROME
    try {
        Remove-Item Env:NIGHTOWL_TEST_CHROME -ErrorAction SilentlyContinue
        $unconfigured = Start-Process -FilePath $node -ArgumentList $probeArgs -Credential $credential -LoadUserProfile `
            -WorkingDirectory $env:GITHUB_WORKSPACE -RedirectStandardOutput $out -RedirectStandardError $err -Wait -PassThru
        $unconfiguredLines = @(Get-Content $out)
        $unconfiguredLines
        $unconfiguredData = @($unconfiguredLines | Where-Object { $_ -like 'CHROME_CDP_ISOLATED=*' })
        if ($unconfiguredData.Count -ne 1) { throw 'Missing configuration control did not return a result' }
        $unconfiguredResult = $unconfiguredData[0].Substring('CHROME_CDP_ISOLATED='.Length) | ConvertFrom-Json
        if ($unconfigured.ExitCode -eq 0 -or $unconfiguredResult.error -ne 'chrome_not_configured' -or
            -not $unconfiguredResult.process_exited -or -not $unconfiguredResult.profile_removed -or
            $unconfiguredResult.remaining_profile_processes -ne 0) { throw 'Missing Chrome configuration was not rejected cleanly' }
    } finally { $env:NIGHTOWL_TEST_CHROME = $configuredChrome }
    $probe = Start-Process -FilePath $node -ArgumentList $probeArgs -Credential $credential -LoadUserProfile `
        -WorkingDirectory $env:GITHUB_WORKSPACE -RedirectStandardOutput $out -RedirectStandardError $err -Wait -PassThru
    Get-Content $out
    Get-Content $err
    if (-not $negativeValid) { throw 'Chrome CDP negative control did not reject the missing executable cleanly' }
    if ($probe.ExitCode -ne 0) { throw "Isolated Chrome CDP probe failed with exit code $($probe.ExitCode)" }
    if ($e2eExitCode -ne 0) { throw "Windows E2E failed with exit code $e2eExitCode" }
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
        if ($profileRoot) {
            $record = Get-CimInstance Win32_UserProfile -Filter "SID='$testSid'" -ErrorAction Stop
            if ($null -ne $record) {
                if ([IO.Path]::GetFullPath($record.LocalPath).TrimEnd('\') -ine $profileRoot) { throw 'Temporary profile changed before cleanup' }
                $record | Remove-CimInstance -ErrorAction Stop
            }
        }
    }
    if ($created) { Remove-LocalUser -Name $user -ErrorAction Continue }
    if (Test-Path -LiteralPath $work) {
        $workItem = Get-Item -LiteralPath $work -Force
        if (($workItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Test workspace is a reparse point' }
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction Continue
    }
}
