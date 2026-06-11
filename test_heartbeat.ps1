$token = "rmm_live_NV9vWm0GsgiiHFrCG-ORZpoAJ1XKc3TyWtZtemYvn_0"
$baseUrl = "http://127.0.0.1:8000"

$body = @{
    schema_version = 1
    hostname = "PC-TESTE"
    domain = "control.local"
    logged_user = "CONTROL\usuario"
    ips = @("192.168.10.50")
    os = @{
        name = "Microsoft Windows 11 Pro"
        version = "10.0.22631"
        build = "22631"
    }
    hardware = @{
        cpu = "Intel(R) Core(TM) i5"
        memory_total_bytes = 17179869184
        manufacturer = "Dell Inc."
        model = "OptiPlex"
        serial_number = "TEST123"
    }
    disks = @(
        @{
            name = "C:"
            size_bytes = 512110190592
            free_bytes = 210453397504
        }
    )
    uptime_seconds = 3600
    installed_software = @(
        @{
            name = "Google Chrome"
            version = "125.0.0.0"
            publisher = "Google LLC"
        }
    )
    defender_status = @{
        enabled = $true
        real_time_protection_enabled = $true
    }
    heartbeat_at = (Get-Date).ToUniversalTime().ToString("o")
} | ConvertTo-Json -Depth 10

Invoke-RestMethod `
    -Uri "$baseUrl/api/agent/heartbeat/" `
    -Method Post `
    -Headers @{ Authorization = "Bearer $token" } `
    -ContentType "application/json" `
    -Body $body
