$ErrorActionPreference = 'Stop'
[ordered]@{
    utc = [DateTime]::UtcNow.ToString('o', [Globalization.CultureInfo]::InvariantCulture)
    powershell_version = $PSVersionTable.PSVersion.ToString()
} | ConvertTo-Json -Compress
