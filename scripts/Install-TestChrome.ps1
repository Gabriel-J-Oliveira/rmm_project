$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$root = [IO.Path]::GetFullPath($env:GITHUB_WORKSPACE)
$pin = Get-Content -Raw -LiteralPath (Join-Path $root 'tests/frontend/chrome_for_testing.json') | ConvertFrom-Json
if ($pin.version -notmatch '^\d+\.\d+\.\d+\.\d+$' -or $pin.sha256 -notmatch '^[a-f0-9]{64}$') { throw 'Invalid Chrome for Testing pin' }
$expectedUrl = "https://storage.googleapis.com/chrome-for-testing-public/$($pin.version)/win64/chrome-win64.zip"
if ($pin.url -cne $expectedUrl) { throw 'Chrome for Testing URL is not the pinned official artifact' }
$dir = Join-Path $root '.ci-tools/chrome-for-testing'
New-Item -ItemType Directory -Path $dir -Force | Out-Null
$archive = Join-Path $dir 'chrome-win64.zip'
Invoke-WebRequest -Uri $pin.url -OutFile $archive
$actual = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -cne $pin.sha256) { Remove-Item -LiteralPath $archive -Force; throw 'Chrome for Testing SHA-256 mismatch' }
Expand-Archive -LiteralPath $archive -DestinationPath $dir -Force
Remove-Item -LiteralPath $archive -Force
$chrome = [IO.Path]::GetFullPath((Join-Path $dir 'chrome-win64/chrome.exe'))
if (-not (Test-Path -LiteralPath $chrome -PathType Leaf)) { throw 'Verified Chrome executable missing' }
"NIGHTOWL_TEST_CHROME=$chrome" | Out-File -FilePath $env:GITHUB_ENV -Encoding utf8 -Append
Write-Output "CHROME_FOR_TESTING version=$($pin.version) sha256=$actual"
