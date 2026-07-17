using System.Diagnostics;
using NightOwl.Agent.Shared;
using System.Globalization;
using System.Net.NetworkInformation;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Text;
using System.Text.Json;
using Microsoft.Win32;
using NightOwl.Agent.Windows.Models;
using NightOwl.Agent.Windows.Services;

namespace NightOwl.Agent.Windows.Collectors;

public sealed class WindowsInventoryCollector
{
    private readonly JsonlLogger _logger;

    public WindowsInventoryCollector(JsonlLogger logger)
    {
        _logger = logger;
    }

    public AgentHeartbeatPayload BuildHeartbeat(AgentConfig config)
    {
        Dictionary<string, object?> system = GetSystem();
        Dictionary<string, object?> hardware = GetHardware();
        Dictionary<string, object?> network = GetNetwork();
        List<string> ips = AsStringList(network.GetValueOrDefault("ips"));
        DateTimeOffset now = DateTimeOffset.UtcNow;
        Dictionary<string, object?> cpu = AsDict(hardware.GetValueOrDefault("cpu"));
        Dictionary<string, object?> os = AsDict(system.GetValueOrDefault("os"));

        return new AgentHeartbeatPayload
        {
            AgentId = config.MachineId,
            MachineId = config.MachineId,
            Hostname = Environment.MachineName,
            Fqdn = system.GetValueOrDefault("fqdn")?.ToString() ?? Environment.MachineName,
            Domain = system.GetValueOrDefault("domain")?.ToString() ?? Environment.UserDomainName,
            LoggedUser = system.GetValueOrDefault("logged_user")?.ToString() ?? Environment.UserName,
            Username = system.GetValueOrDefault("logged_user")?.ToString() ?? Environment.UserName,
            IpAddress = network.GetValueOrDefault("primary_ip")?.ToString() ?? ips.FirstOrDefault() ?? "",
            OsName = os.GetValueOrDefault("name")?.ToString() ?? RuntimeInformation.OSDescription,
            OsVersion = os.GetValueOrDefault("version")?.ToString() ?? Environment.OSVersion.VersionString,
            WindowsBuild = os.GetValueOrDefault("build")?.ToString() ?? Environment.OSVersion.Version.Build.ToString(CultureInfo.InvariantCulture),
            AgentVersion = config.AgentVersion,
            TrayVersion = GetFileVersion(Path.Combine(config.InstallPath, "NightOwl.Agent.Tray.exe")),
            UpdaterVersion = GetFileVersion(Path.Combine(config.InstallPath, "NightOwl.Agent.Updater.exe")),
            AgentMode = "dotnet-service",
            InstallMode = "dotnet-service",
            Ips = ips,
            Os = os,
            Hardware = new Dictionary<string, object?>
            {
                ["cpu"] = cpu.GetValueOrDefault("name")?.ToString() ?? "",
                ["memory_total_bytes"] = hardware.GetValueOrDefault("total_memory_bytes") ?? hardware.GetValueOrDefault("memory_total_bytes"),
                ["manufacturer"] = hardware.GetValueOrDefault("manufacturer")?.ToString() ?? system.GetValueOrDefault("manufacturer")?.ToString() ?? "",
                ["model"] = hardware.GetValueOrDefault("model")?.ToString() ?? system.GetValueOrDefault("model")?.ToString() ?? "",
                ["serial_number"] = hardware.GetValueOrDefault("serial_number")?.ToString() ?? system.GetValueOrDefault("serial_number")?.ToString() ?? ""
            },
            UptimeSeconds = ToLong(system.GetValueOrDefault("uptime_seconds")) ?? Environment.TickCount64 / 1000,
            Agent = BuildAgentMetadata(config),
            HeartbeatAt = now,
            Timestamp = now
        };
    }

    public AgentCollectPayload BuildCollectPayload(AgentConfig config)
    {
        DateTimeOffset collectedAt = DateTimeOffset.UtcNow;
        Dictionary<string, object?> system = CollectSection("system", GetSystem, new Dictionary<string, object?>());
        Dictionary<string, object?> hardware = CollectSection("hardware", GetHardware, new Dictionary<string, object?>());
        Dictionary<string, object?> network = CollectSection("network", GetNetwork, new Dictionary<string, object?> { ["interfaces"] = new List<object>() });
        List<Dictionary<string, object?>> disks = CollectSection("disks", GetDisks, new List<Dictionary<string, object?>>());
        List<Dictionary<string, object?>> software = CollectSection("software", GetSoftware, new List<Dictionary<string, object?>>());
        Dictionary<string, object?> security = CollectSection("security", GetSecurity, new Dictionary<string, object?>());
        Dictionary<string, object?> patches = CollectSection("patches", GetPatchStatus, new Dictionary<string, object?>());

        system["collected_at"] = collectedAt;
        hardware["collected_at"] = collectedAt;
        network["collected_at"] = collectedAt;
        security["collected_at"] = collectedAt;
        patches["collected_at"] = collectedAt;

        return new AgentCollectPayload
        {
            MachineId = config.MachineId,
            AgentVersion = config.AgentVersion,
            AgentMode = "dotnet-service",
            CollectedAt = collectedAt,
            System = system,
            Hardware = hardware,
            Network = network,
            Disks = disks,
            Software = software,
            Security = security,
            Patches = patches
        };
    }

    public List<Dictionary<string, object?>> GetDisks()
    {
        Dictionary<string, string> bitlocker = GetBitLockerMap();
        Dictionary<string, string> volumeHealth = GetVolumeHealthMap();
        string systemDrive = (Environment.GetEnvironmentVariable("SystemDrive") ?? "C:").TrimEnd('\\');
        DateTimeOffset now = DateTimeOffset.UtcNow;

        return DriveInfo.GetDrives()
            .Where(d => d.IsReady)
            .Select(d =>
            {
                long total = d.TotalSize;
                long free = d.AvailableFreeSpace;
                long used = Math.Max(total - free, 0);
                string letter = d.Name.TrimEnd('\\');
                double usedPercent = total <= 0 ? 0 : Math.Round(((double)used / total) * 100, 2);
                string key = letter.TrimEnd(':').ToUpperInvariant();
                return new Dictionary<string, object?>
                {
                    ["name"] = letter,
                    ["letter"] = letter,
                    ["label"] = d.VolumeLabel,
                    ["volume_name"] = d.VolumeLabel,
                    ["filesystem"] = d.DriveFormat,
                    ["drive_type"] = d.DriveType.ToString(),
                    ["size_bytes"] = total,
                    ["total_bytes"] = total,
                    ["free_bytes"] = free,
                    ["used_bytes"] = used,
                    ["used_percent"] = usedPercent,
                    ["is_system_drive"] = string.Equals(letter, systemDrive, StringComparison.OrdinalIgnoreCase),
                    ["bitlocker_status"] = bitlocker.GetValueOrDefault(key) ?? "unknown",
                    ["health_status"] = volumeHealth.GetValueOrDefault(key) ?? "unknown",
                    ["collected_at"] = now
                };
            })
            .ToList();
    }

    public List<Dictionary<string, object?>> GetSoftware()
    {
        List<Dictionary<string, object?>> rows = new();
        ReadUninstallKey(rows, RegistryView.Registry64, "x64", "HKLM64");
        ReadUninstallKey(rows, RegistryView.Registry32, "x86", "HKLM32");
        return rows
            .Where(r => !string.IsNullOrWhiteSpace(r.GetValueOrDefault("display_name")?.ToString()))
            .GroupBy(r => $"{NormalizeKey(r.GetValueOrDefault("display_name"))}|{NormalizeKey(r.GetValueOrDefault("display_version"))}|{NormalizeKey(r.GetValueOrDefault("publisher"))}|{r.GetValueOrDefault("architecture")}")
            .Select(g => g.First())
            .OrderBy(r => r.GetValueOrDefault("display_name")?.ToString(), StringComparer.OrdinalIgnoreCase)
            .ToList();
    }

    public Dictionary<string, object?> GetSecurity()
    {
        Dictionary<string, object?> defender = GetDefenderStatus();
        Dictionary<string, object?> firewall = GetFirewallStatus();
        List<Dictionary<string, object?>> antivirusProducts = GetAntivirusProducts();
        Dictionary<string, object?> bitlocker = GetBitLockerSummary();
        List<string> localAdmins = GetLocalAdmins();
        List<Dictionary<string, object?>> software = GetSoftware();
        string[] remoteTools = { "AnyDesk", "TeamViewer", "UltraVNC", "TightVNC", "RealVNC", "RustDesk", "Chrome Remote Desktop" };
        List<string> detectedRemoteTools = software
            .Where(s => remoteTools.Any(tool => (s.GetValueOrDefault("display_name")?.ToString() ?? "").Contains(tool, StringComparison.OrdinalIgnoreCase)))
            .Select(s => s.GetValueOrDefault("display_name")?.ToString() ?? "")
            .Where(name => !string.IsNullOrWhiteSpace(name))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .OrderBy(name => name)
            .ToList();

        bool defenderPresent = ToBool(defender.GetValueOrDefault("defender_present")) == true;
        bool defenderEnabled = ToBool(defender.GetValueOrDefault("defender_enabled")) == true;
        bool realtimeEnabled = ToBool(defender.GetValueOrDefault("realtime_protection_enabled")) == true;
        bool hasThirdPartyAv = antivirusProducts.Count > 0;
        bool firewallWarning = firewall.Values.Any(value => value is bool enabled && !enabled);
        bool rdpEnabled = IsRdpEnabled();
        string overall = "ok";
        if ((defenderPresent && (!defenderEnabled || !realtimeEnabled) && !hasThirdPartyAv) || (!defenderPresent && !hasThirdPartyAv))
        {
            overall = "critical";
        }
        else if (firewallWarning || rdpEnabled || detectedRemoteTools.Count > 0)
        {
            overall = "warning";
        }

        return new Dictionary<string, object?>
        {
            ["overall_status"] = overall,
            ["defender_present"] = defenderPresent,
            ["defender_enabled"] = defenderEnabled,
            ["realtime_protection_enabled"] = realtimeEnabled,
            ["antivirus_signature_version"] = defender.GetValueOrDefault("antivirus_signature_version"),
            ["antivirus_signature_last_updated"] = defender.GetValueOrDefault("antivirus_signature_last_updated"),
            ["antispyware_signature_version"] = defender.GetValueOrDefault("antispyware_signature_version"),
            ["last_quick_scan"] = defender.GetValueOrDefault("last_quick_scan"),
            ["last_full_scan"] = defender.GetValueOrDefault("last_full_scan"),
            ["defender"] = new Dictionary<string, object?>
            {
                ["defender_present"] = defenderPresent,
                ["defender_enabled"] = defenderEnabled,
                ["antivirus_enabled"] = defenderEnabled,
                ["realtime_protection_enabled"] = realtimeEnabled,
                ["real_time_protection_enabled"] = realtimeEnabled,
                ["antivirus_signature_version"] = defender.GetValueOrDefault("antivirus_signature_version"),
                ["antivirus_signature_last_updated"] = defender.GetValueOrDefault("antivirus_signature_last_updated"),
                ["antispyware_signature_version"] = defender.GetValueOrDefault("antispyware_signature_version"),
                ["last_quick_scan"] = defender.GetValueOrDefault("last_quick_scan"),
                ["last_full_scan"] = defender.GetValueOrDefault("last_full_scan"),
                ["engine_version"] = defender.GetValueOrDefault("engine_version"),
                ["product_version"] = defender.GetValueOrDefault("product_version"),
                ["raw"] = defender
            },
            ["firewall"] = firewall,
            ["firewall_domain_enabled"] = firewall.GetValueOrDefault("domain_enabled"),
            ["firewall_private_enabled"] = firewall.GetValueOrDefault("private_enabled"),
            ["firewall_public_enabled"] = firewall.GetValueOrDefault("public_enabled"),
            ["detected_antivirus_products"] = antivirusProducts,
            ["antivirus_products"] = antivirusProducts,
            ["local_admins"] = localAdmins,
            ["local_administrators"] = localAdmins.Select(name => new Dictionary<string, object?> { ["name"] = name }).ToList(),
            ["rdp_enabled"] = rdpEnabled,
            ["uac_enabled"] = IsUacEnabled(),
            ["bitlocker"] = bitlocker,
            ["bitlocker_summary"] = bitlocker,
            ["remote_access_tools"] = detectedRemoteTools,
            ["collected_at"] = DateTimeOffset.UtcNow
        };
    }

    public Dictionary<string, object?> GetPatchStatus()
    {
        List<string> rebootReasons = GetRebootPendingReasons();
        Dictionary<string, object?> updateInfo = GetWindowsUpdateInfo();
        List<Dictionary<string, object?>> hotfixes = GetInstalledHotfixes();

        return new Dictionary<string, object?>
        {
            ["status"] = "partial",
            ["reboot_pending"] = rebootReasons.Count > 0,
            ["reboot_pending_reasons"] = rebootReasons,
            ["last_windows_update_check"] = updateInfo.GetValueOrDefault("last_windows_update_check"),
            ["last_windows_update_install"] = updateInfo.GetValueOrDefault("last_windows_update_install"),
            ["pending_updates_count"] = updateInfo.GetValueOrDefault("pending_updates_count"),
            ["pending_updates_error"] = updateInfo.GetValueOrDefault("pending_updates_error"),
            ["installed_hotfixes"] = hotfixes.Take(80).ToList(),
            ["installed_hotfix_count"] = hotfixes.Count,
            ["windows_build"] = Environment.OSVersion.Version.Build.ToString(CultureInfo.InvariantCulture),
            ["servicing_stack_info"] = updateInfo.GetValueOrDefault("servicing_stack_info"),
            ["collected_at"] = DateTimeOffset.UtcNow
        };
    }

    public Dictionary<string, object?> GetSystem()
    {
        Dictionary<string, object?> ps = RunPowerShellObject("""
            $cs = Get-CimInstance Win32_ComputerSystem -ErrorAction SilentlyContinue
            $os = Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue
            $bios = Get-CimInstance Win32_BIOS -ErrorAction SilentlyContinue
            $boot = if ($os.LastBootUpTime) { ([DateTime]$os.LastBootUpTime).ToUniversalTime().ToString("o") } else { $null }
            $install = if ($os.InstallDate) { ([DateTime]$os.InstallDate).ToUniversalTime().ToString("o") } else { $null }
            $machineType = if ($os.ProductType -and $os.ProductType -ne 1) { "server" } elseif ($cs.PCSystemTypeEx -eq 2) { "notebook" } else { "workstation" }
            [pscustomobject]@{
              hostname = $env:COMPUTERNAME
              fqdn = ([System.Net.Dns]::GetHostEntry($env:COMPUTERNAME).HostName)
              domain = if ($cs.PartOfDomain) { $cs.Domain } else { "" }
              workgroup = if (-not $cs.PartOfDomain) { $cs.Workgroup } else { "" }
              logged_user = $cs.UserName
              os_name = $os.Caption
              os_version = $os.Version
              os_build = $os.BuildNumber
              os_architecture = $os.OSArchitecture
              install_date = $install
              last_boot_time = $boot
              uptime_seconds = if ($os.LastBootUpTime) { [int64]((Get-Date) - ([DateTime]$os.LastBootUpTime)).TotalSeconds } else { $null }
              timezone = [TimeZoneInfo]::Local.Id
              locale = [System.Globalization.CultureInfo]::CurrentCulture.Name
              machine_type = $machineType
              manufacturer = $cs.Manufacturer
              model = $cs.Model
              serial_number = $bios.SerialNumber
            }
        """, timeoutSeconds: 12);

        Dictionary<string, object?> os = new()
        {
            ["name"] = ps.GetValueOrDefault("os_name")?.ToString() ?? RuntimeInformation.OSDescription,
            ["version"] = ps.GetValueOrDefault("os_version")?.ToString() ?? Environment.OSVersion.VersionString,
            ["build"] = ps.GetValueOrDefault("os_build")?.ToString() ?? Environment.OSVersion.Version.Build.ToString(CultureInfo.InvariantCulture),
            ["architecture"] = ps.GetValueOrDefault("os_architecture")?.ToString() ?? RuntimeInformation.OSArchitecture.ToString()
        };

        return new Dictionary<string, object?>
        {
            ["hostname"] = ps.GetValueOrDefault("hostname")?.ToString() ?? Environment.MachineName,
            ["fqdn"] = ps.GetValueOrDefault("fqdn")?.ToString() ?? TryGetFqdn(),
            ["domain"] = ps.GetValueOrDefault("domain")?.ToString() ?? Environment.UserDomainName,
            ["workgroup"] = ps.GetValueOrDefault("workgroup")?.ToString() ?? "",
            ["logged_user"] = ps.GetValueOrDefault("logged_user")?.ToString() ?? WindowsIdentity.GetCurrent().Name,
            ["os_name"] = os["name"],
            ["os_version"] = os["version"],
            ["os_build"] = os["build"],
            ["os_architecture"] = os["architecture"],
            ["os"] = os,
            ["install_date"] = ps.GetValueOrDefault("install_date"),
            ["last_boot_time"] = ps.GetValueOrDefault("last_boot_time"),
            ["uptime_seconds"] = ToLong(ps.GetValueOrDefault("uptime_seconds")) ?? Environment.TickCount64 / 1000,
            ["timezone"] = ps.GetValueOrDefault("timezone")?.ToString() ?? TimeZoneInfo.Local.Id,
            ["locale"] = ps.GetValueOrDefault("locale")?.ToString() ?? CultureInfo.CurrentCulture.Name,
            ["language"] = ps.GetValueOrDefault("locale")?.ToString() ?? CultureInfo.CurrentUICulture.Name,
            ["machine_type"] = ps.GetValueOrDefault("machine_type")?.ToString() ?? "workstation",
            ["manufacturer"] = ps.GetValueOrDefault("manufacturer")?.ToString() ?? "",
            ["model"] = ps.GetValueOrDefault("model")?.ToString() ?? "",
            ["serial_number"] = ps.GetValueOrDefault("serial_number")?.ToString() ?? "",
            ["collected_at"] = DateTimeOffset.UtcNow
        };
    }

    public Dictionary<string, object?> GetHardware()
    {
        Dictionary<string, object?> ps = RunPowerShellObject("""
            $cpu = Get-CimInstance Win32_Processor -ErrorAction SilentlyContinue | Select-Object -First 1
            $cs = Get-CimInstance Win32_ComputerSystem -ErrorAction SilentlyContinue
            $os = Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue
            $bios = Get-CimInstance Win32_BIOS -ErrorAction SilentlyContinue
            $board = Get-CimInstance Win32_BaseBoard -ErrorAction SilentlyContinue | Select-Object -First 1
            $battery = Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue | Select-Object -First 1
            $tpm = $null
            try { $tpm = Get-CimInstance -Namespace "root\cimv2\Security\MicrosoftTpm" -ClassName Win32_Tpm -ErrorAction Stop | Select-Object -First 1 } catch {}
            [pscustomobject]@{
              manufacturer = $cs.Manufacturer
              model = $cs.Model
              serial_number = $bios.SerialNumber
              bios_version = (($bios.SMBIOSBIOSVersion, $bios.Version) | Where-Object { $_ } | Select-Object -First 1)
              bios_release_date = if ($bios.ReleaseDate) { ([DateTime]$bios.ReleaseDate).ToUniversalTime().ToString("o") } else { $null }
              motherboard = if ($board) { "$($board.Manufacturer) $($board.Product)" } else { $null }
              cpu_name = $cpu.Name
              cpu_manufacturer = $cpu.Manufacturer
              physical_cores = $cpu.NumberOfCores
              logical_processors = $cpu.NumberOfLogicalProcessors
              total_memory_bytes = if ($cs.TotalPhysicalMemory) { [int64]$cs.TotalPhysicalMemory } else { $null }
              available_memory_bytes = if ($os.FreePhysicalMemory) { [int64]$os.FreePhysicalMemory * 1024 } else { $null }
              tpm_present = [bool]$tpm
              tpm_enabled = if ($tpm) { [bool]$tpm.IsEnabled_InitialValue } else { $null }
              battery_present = [bool]$battery
              battery_status = if ($battery) { $battery.BatteryStatus } else { $null }
            }
        """, timeoutSeconds: 12);

        Dictionary<string, object?> cpu = new()
        {
            ["name"] = ps.GetValueOrDefault("cpu_name")?.ToString() ?? RuntimeInformation.ProcessArchitecture.ToString(),
            ["manufacturer"] = ps.GetValueOrDefault("cpu_manufacturer")?.ToString() ?? "",
            ["physical_cores"] = ToLong(ps.GetValueOrDefault("physical_cores")),
            ["logical_processors"] = ToLong(ps.GetValueOrDefault("logical_processors")) ?? Environment.ProcessorCount
        };

        return new Dictionary<string, object?>
        {
            ["manufacturer"] = ps.GetValueOrDefault("manufacturer")?.ToString() ?? "",
            ["model"] = ps.GetValueOrDefault("model")?.ToString() ?? "",
            ["serial_number"] = ps.GetValueOrDefault("serial_number")?.ToString() ?? "",
            ["bios_version"] = ps.GetValueOrDefault("bios_version")?.ToString() ?? "",
            ["bios_release_date"] = ps.GetValueOrDefault("bios_release_date"),
            ["motherboard"] = ps.GetValueOrDefault("motherboard")?.ToString() ?? "",
            ["cpu_name"] = cpu["name"],
            ["cpu_manufacturer"] = cpu["manufacturer"],
            ["physical_cores"] = cpu["physical_cores"],
            ["logical_processors"] = cpu["logical_processors"],
            ["cpu"] = cpu,
            ["memory_total_bytes"] = ps.GetValueOrDefault("total_memory_bytes"),
            ["total_memory_bytes"] = ps.GetValueOrDefault("total_memory_bytes"),
            ["available_memory_bytes"] = ps.GetValueOrDefault("available_memory_bytes"),
            ["bios"] = new Dictionary<string, object?>
            {
                ["version"] = ps.GetValueOrDefault("bios_version"),
                ["release_date"] = ps.GetValueOrDefault("bios_release_date"),
                ["serial_number"] = ps.GetValueOrDefault("serial_number")
            },
            ["tpm_present"] = ToBool(ps.GetValueOrDefault("tpm_present")),
            ["tpm_enabled"] = ToBool(ps.GetValueOrDefault("tpm_enabled")),
            ["tpm"] = new Dictionary<string, object?>
            {
                ["present"] = ToBool(ps.GetValueOrDefault("tpm_present")),
                ["enabled"] = ToBool(ps.GetValueOrDefault("tpm_enabled"))
            },
            ["battery_present"] = ToBool(ps.GetValueOrDefault("battery_present")),
            ["battery_status"] = ps.GetValueOrDefault("battery_status"),
            ["collected_at"] = DateTimeOffset.UtcNow
        };
    }

    private Dictionary<string, object?> GetNetwork()
    {
        var adapters = NetworkInterface.GetAllNetworkInterfaces()
            .Where(n => n.OperationalStatus == OperationalStatus.Up)
            .Select(n =>
            {
                IPInterfaceProperties props = n.GetIPProperties();
                List<string> ipv4 = props.UnicastAddresses
                    .Where(a => a.Address.AddressFamily == AddressFamily.InterNetwork && !IPAddressIsLoopback(a.Address.ToString()))
                    .Select(a => a.Address.ToString())
                    .Distinct()
                    .ToList();
                List<string> ipv6 = props.UnicastAddresses
                    .Where(a => a.Address.AddressFamily == AddressFamily.InterNetworkV6)
                    .Select(a => a.Address.ToString())
                    .Distinct()
                    .ToList();
                return new Dictionary<string, object?>
                {
                    ["name"] = n.Name,
                    ["description"] = n.Description,
                    ["status"] = n.OperationalStatus.ToString(),
                    ["mac_address"] = FormatMac(n.GetPhysicalAddress().ToString()),
                    ["mac"] = FormatMac(n.GetPhysicalAddress().ToString()),
                    ["ipv4_addresses"] = ipv4,
                    ["ipv6_addresses"] = ipv6,
                    ["ips"] = ipv4,
                    ["gateway"] = props.GatewayAddresses.FirstOrDefault()?.Address.ToString(),
                    ["dns_servers"] = props.DnsAddresses.Select(a => a.ToString()).Distinct().ToList(),
                    ["dns"] = props.DnsAddresses.Select(a => a.ToString()).Distinct().ToList(),
                    ["link_speed"] = n.Speed > 0 ? n.Speed : null,
                    ["adapter_type"] = n.NetworkInterfaceType.ToString(),
                    ["type"] = n.NetworkInterfaceType.ToString()
                };
            })
            .Where(a => AsStringList(a.GetValueOrDefault("ipv4_addresses")).Count > 0)
            .ToList();

        List<string> ips = adapters.SelectMany(a => AsStringList(a.GetValueOrDefault("ipv4_addresses"))).Distinct().ToList();
        Dictionary<string, object?>? primaryAdapter = adapters.FirstOrDefault(a => !string.IsNullOrWhiteSpace(a.GetValueOrDefault("gateway")?.ToString())) ?? adapters.FirstOrDefault();

        return new Dictionary<string, object?>
        {
            ["ips"] = ips,
            ["primary_ip"] = ips.FirstOrDefault(),
            ["primary_mac"] = primaryAdapter?.GetValueOrDefault("mac_address"),
            ["default_gateway"] = primaryAdapter?.GetValueOrDefault("gateway"),
            ["dns_servers"] = adapters.SelectMany(a => AsStringList(a.GetValueOrDefault("dns_servers"))).Distinct().ToList(),
            ["mac_addresses"] = adapters.Select(a => a.GetValueOrDefault("mac_address")?.ToString()).Where(v => !string.IsNullOrWhiteSpace(v)).Distinct().ToList(),
            ["adapters"] = adapters,
            ["interfaces"] = adapters,
            ["collected_at"] = DateTimeOffset.UtcNow
        };
    }

    private T CollectSection<T>(string section, Func<T> collect, T fallback)
    {
        LogSection($"collection.{section}.started", $"{section} collection started.", null);
        try
        {
            T result = collect();
            LogSection($"collection.{section}.completed", $"{section} collection completed.", DescribeSectionResult(result));
            return result;
        }
        catch (Exception ex)
        {
            LogSection($"collection.{section}.failed", ex.Message, new { exception = ex.ToString() }, "error");
            return fallback;
        }
    }

    private void LogSection(string eventType, string message, object? data = null, string level = "info")
    {
        try
        {
            _logger.LogAsync(eventType, message, data, CancellationToken.None, level).GetAwaiter().GetResult();
        }
        catch
        {
            // Collection logging must never stop the agent.
        }
    }

    private static object DescribeSectionResult(object? result)
    {
        return result switch
        {
            null => new { count = 0 },
            System.Collections.ICollection collection => new { count = collection.Count },
            IDictionary<string, object?> dict => new { keys = dict.Keys.ToArray() },
            _ => new { type = result.GetType().Name }
        };
    }

    private static Dictionary<string, object?> BuildAgentMetadata(AgentConfig config)
    {
        return new Dictionary<string, object?>
        {
            ["version"] = config.AgentVersion,
            ["tray_version"] = GetFileVersion(Path.Combine(config.InstallPath, "NightOwl.Agent.Tray.exe")),
            ["updater_version"] = GetFileVersion(Path.Combine(config.InstallPath, "NightOwl.Agent.Updater.exe")),
            ["mode"] = "dotnet-service",
            ["install_mode"] = "dotnet-service",
            ["install_path"] = config.InstallPath,
            ["config_path"] = Environment.GetEnvironmentVariable("NIGHTOWL_AGENT_CONFIG") ?? NightOwlPaths.Current.ConfigPath,
            ["log_path"] = Path.GetDirectoryName(config.LogPath),
            ["log_file"] = config.LogPath,
            ["service_name"] = NightOwlPaths.ServiceName,
            ["service_status"] = "Running",
            ["service_start_type"] = "Automatic",
            ["service_account"] = "LocalSystem",
            ["heartbeat_url"] = config.HeartbeatUrl,
            ["jobs_pull_url"] = config.JobsPullUrl,
            ["jobs_result_url"] = config.JobsResultUrl,
            ["collection_endpoints"] = new Dictionary<string, object?>
            {
                ["collectUrl"] = config.CollectUrl
            },
            ["runtime"] = ".NET",
            ["runtime_version"] = Environment.Version.ToString()
        };
    }

    private static string GetFileVersion(string path)
    {
        try
        {
            if (!File.Exists(path))
            {
                return "";
            }
            FileVersionInfo info = FileVersionInfo.GetVersionInfo(path);
            return info.ProductVersion ?? info.FileVersion ?? "";
        }
        catch
        {
            return "";
        }
    }

    private static void ReadUninstallKey(List<Dictionary<string, object?>> rows, RegistryView view, string architecture, string source)
    {
        using RegistryKey baseKey = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, view);
        using RegistryKey? uninstall = baseKey.OpenSubKey(@"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall");
        if (uninstall is null)
        {
            return;
        }

        foreach (string subKeyName in uninstall.GetSubKeyNames())
        {
            using RegistryKey? app = uninstall.OpenSubKey(subKeyName);
            if (app is null)
            {
                continue;
            }

            string name = app.GetValue("DisplayName")?.ToString() ?? "";
            if (string.IsNullOrWhiteSpace(name))
            {
                continue;
            }

            string? installDate = NormalizeInstallDate(app.GetValue("InstallDate")?.ToString());
            rows.Add(new Dictionary<string, object?>
            {
                ["display_name"] = name,
                ["name"] = name,
                ["display_version"] = app.GetValue("DisplayVersion")?.ToString() ?? "",
                ["version"] = app.GetValue("DisplayVersion")?.ToString() ?? "",
                ["publisher"] = app.GetValue("Publisher")?.ToString() ?? "",
                ["install_date"] = installDate,
                ["install_location"] = app.GetValue("InstallLocation")?.ToString() ?? "",
                ["uninstall_string"] = app.GetValue("UninstallString")?.ToString() ?? "",
                ["quiet_uninstall_string"] = app.GetValue("QuietUninstallString")?.ToString() ?? "",
                ["estimated_size"] = app.GetValue("EstimatedSize"),
                ["estimated_size_kb"] = app.GetValue("EstimatedSize"),
                ["registry_key"] = subKeyName,
                ["registry_hive"] = source,
                ["source"] = source,
                ["architecture"] = architecture,
                ["product_code"] = Guid.TryParse(subKeyName.Trim('{', '}'), out _) ? subKeyName : "",
                ["detected_at"] = DateTimeOffset.UtcNow
            });
        }
    }

    private static Dictionary<string, object?> GetDefenderStatus()
    {
        Dictionary<string, object?> result = RunPowerShellObject("""
            try {
              $mp = Get-MpComputerStatus -ErrorAction Stop
              [pscustomobject]@{
                defender_present = $true
                defender_enabled = [bool]$mp.AntivirusEnabled
                realtime_protection_enabled = [bool]$mp.RealTimeProtectionEnabled
                antivirus_signature_version = $mp.AntivirusSignatureVersion
                antivirus_signature_last_updated = if ($mp.AntivirusSignatureLastUpdated) { ([DateTime]$mp.AntivirusSignatureLastUpdated).ToUniversalTime().ToString("o") } else { $null }
                antispyware_signature_version = $mp.AntispywareSignatureVersion
                last_quick_scan = if ($mp.QuickScanEndTime) { ([DateTime]$mp.QuickScanEndTime).ToUniversalTime().ToString("o") } else { $null }
                last_full_scan = if ($mp.FullScanEndTime) { ([DateTime]$mp.FullScanEndTime).ToUniversalTime().ToString("o") } else { $null }
                engine_version = $mp.AMEngineVersion
                product_version = $mp.AMProductVersion
              }
            } catch {
              [pscustomobject]@{ defender_present = $false; error = $_.Exception.Message }
            }
        """, timeoutSeconds: 10);
        return result;
    }

    private static Dictionary<string, object?> GetFirewallStatus()
    {
        Dictionary<string, object?> result = RunPowerShellObject("""
            $profiles = Get-NetFirewallProfile -ErrorAction SilentlyContinue
            [pscustomobject]@{
              domain_enabled = [bool](($profiles | Where-Object Name -eq "Domain" | Select-Object -First 1).Enabled)
              private_enabled = [bool](($profiles | Where-Object Name -eq "Private" | Select-Object -First 1).Enabled)
              public_enabled = [bool](($profiles | Where-Object Name -eq "Public" | Select-Object -First 1).Enabled)
            }
        """, timeoutSeconds: 8);
        return result.Count == 0
            ? new Dictionary<string, object?> { ["domain_enabled"] = null, ["private_enabled"] = null, ["public_enabled"] = null }
            : result;
    }

    private static List<Dictionary<string, object?>> GetAntivirusProducts()
    {
        List<Dictionary<string, object?>> products = RunPowerShellList("""
            try {
              Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntivirusProduct -ErrorAction Stop |
                ForEach-Object {
                  [pscustomobject]@{
                    name = $_.displayName
                    instance_guid = $_.instanceGuid
                    path_to_signed_product_exe = $_.pathToSignedProductExe
                    product_state = $_.productState
                  }
                }
            } catch { @() }
        """, timeoutSeconds: 8);
        return products;
    }

    private static Dictionary<string, object?> GetBitLockerSummary()
    {
        List<Dictionary<string, object?>> volumes = RunPowerShellList("""
            try {
              Get-BitLockerVolume -ErrorAction Stop | ForEach-Object {
                [pscustomobject]@{
                  mount_point = $_.MountPoint
                  protection_status = $_.ProtectionStatus.ToString()
                  volume_status = $_.VolumeStatus.ToString()
                  encryption_percentage = $_.EncryptionPercentage
                }
              }
            } catch { @() }
        """, timeoutSeconds: 8);
        int protectedCount = volumes.Count(v => string.Equals(v.GetValueOrDefault("protection_status")?.ToString(), "On", StringComparison.OrdinalIgnoreCase));
        return new Dictionary<string, object?>
        {
            ["volumes"] = volumes,
            ["protected_count"] = protectedCount,
            ["total_count"] = volumes.Count,
            ["status"] = volumes.Count == 0 ? "unknown" : protectedCount == volumes.Count ? "protected" : "attention"
        };
    }

    private static Dictionary<string, string> GetBitLockerMap()
    {
        Dictionary<string, string> result = new(StringComparer.OrdinalIgnoreCase);
        foreach (Dictionary<string, object?> row in AsDictList(GetBitLockerSummary().GetValueOrDefault("volumes")))
        {
            string mount = row.GetValueOrDefault("mount_point")?.ToString() ?? "";
            string key = mount.TrimEnd('\\', ':').ToUpperInvariant();
            if (!string.IsNullOrWhiteSpace(key))
            {
                result[key] = row.GetValueOrDefault("protection_status")?.ToString() ?? "unknown";
            }
        }
        return result;
    }

    private static Dictionary<string, string> GetVolumeHealthMap()
    {
        Dictionary<string, string> result = new(StringComparer.OrdinalIgnoreCase);
        foreach (Dictionary<string, object?> row in RunPowerShellList("""
            try {
              Get-Volume -ErrorAction Stop | Where-Object DriveLetter | ForEach-Object {
                [pscustomobject]@{ drive_letter = $_.DriveLetter; health_status = $_.HealthStatus.ToString() }
              }
            } catch { @() }
        """, timeoutSeconds: 8))
        {
            string key = row.GetValueOrDefault("drive_letter")?.ToString() ?? "";
            if (!string.IsNullOrWhiteSpace(key))
            {
                result[key] = row.GetValueOrDefault("health_status")?.ToString() ?? "unknown";
            }
        }
        return result;
    }

    private static List<string> GetLocalAdmins()
    {
        List<Dictionary<string, object?>> rows = RunPowerShellList("""
            try {
              Get-LocalGroupMember -Group Administrators -ErrorAction Stop |
                ForEach-Object { [pscustomobject]@{ name = $_.Name; object_class = $_.ObjectClass; principal_source = $_.PrincipalSource.ToString() } }
            } catch { @() }
        """, timeoutSeconds: 8);
        return rows
            .Select(row => row.GetValueOrDefault("name")?.ToString() ?? "")
            .Where(name => !string.IsNullOrWhiteSpace(name))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .OrderBy(name => name)
            .ToList();
    }

    private static bool IsRdpEnabled()
    {
        try
        {
            using RegistryKey? key = Registry.LocalMachine.OpenSubKey(@"SYSTEM\CurrentControlSet\Control\Terminal Server");
            return Convert.ToInt32(key?.GetValue("fDenyTSConnections") ?? 1, CultureInfo.InvariantCulture) == 0;
        }
        catch
        {
            return false;
        }
    }

    private static bool? IsUacEnabled()
    {
        try
        {
            using RegistryKey? key = Registry.LocalMachine.OpenSubKey(@"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System");
            return Convert.ToInt32(key?.GetValue("EnableLUA") ?? 0, CultureInfo.InvariantCulture) == 1;
        }
        catch
        {
            return null;
        }
    }

    private static List<string> GetRebootPendingReasons()
    {
        string[] keys =
        {
            @"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending",
            @"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired",
            @"SYSTEM\CurrentControlSet\Control\Session Manager"
        };
        List<string> reasons = new();
        foreach (string keyName in keys)
        {
            try
            {
                using RegistryKey? key = Registry.LocalMachine.OpenSubKey(keyName);
                if (key is null)
                {
                    continue;
                }

                if (keyName.EndsWith("Session Manager", StringComparison.OrdinalIgnoreCase))
                {
                    if (key.GetValue("PendingFileRenameOperations") is not null)
                    {
                        reasons.Add("pending_file_rename_operations");
                    }
                }
                else
                {
                    reasons.Add(keyName.Split('\\').Last());
                }
            }
            catch
            {
                // Ignore registry read errors for reboot hints.
            }
        }
        return reasons.Distinct().ToList();
    }

    private static Dictionary<string, object?> GetWindowsUpdateInfo()
    {
        Dictionary<string, object?> info = RunPowerShellObject("""
            $pending = $null
            $pendingError = $null
            try {
              $session = New-Object -ComObject Microsoft.Update.Session
              $searcher = $session.CreateUpdateSearcher()
              $result = $searcher.Search("IsInstalled=0 and IsHidden=0")
              $pending = $result.Updates.Count
            } catch {
              $pendingError = $_.Exception.Message
            }
            $ux = Get-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings" -ErrorAction SilentlyContinue
            [pscustomobject]@{
              last_windows_update_check = $ux.LastSuccessfulScanTime
              last_windows_update_install = $ux.LastSuccessfulInstallTime
              pending_updates_count = $pending
              pending_updates_error = $pendingError
              servicing_stack_info = $null
            }
        """, timeoutSeconds: 15);
        return info;
    }

    private static List<Dictionary<string, object?>> GetInstalledHotfixes()
    {
        return RunPowerShellList("""
            try {
              Get-HotFix -ErrorAction Stop |
                Sort-Object InstalledOn -Descending |
                Select-Object -First 150 |
                ForEach-Object {
                  [pscustomobject]@{
                    hotfix_id = $_.HotFixID
                    description = $_.Description
                    installed_by = $_.InstalledBy
                    installed_on = if ($_.InstalledOn) { ([DateTime]$_.InstalledOn).ToUniversalTime().ToString("o") } else { $null }
                  }
                }
            } catch { @() }
        """, timeoutSeconds: 12);
    }

    private static Dictionary<string, object?> RunPowerShellObject(string script, int timeoutSeconds)
    {
        object? value = RunPowerShellJson(script, timeoutSeconds);
        if (value is Dictionary<string, object?> dict)
        {
            return dict;
        }
        return new Dictionary<string, object?>();
    }

    private static List<Dictionary<string, object?>> RunPowerShellList(string script, int timeoutSeconds)
    {
        object? value = RunPowerShellJson(script, timeoutSeconds);
        return AsDictList(value);
    }

    private static object? RunPowerShellJson(string script, int timeoutSeconds)
    {
        string command = "$ErrorActionPreference='SilentlyContinue'; " + script + " | ConvertTo-Json -Depth 8 -Compress";
        string encodedCommand = Convert.ToBase64String(Encoding.Unicode.GetBytes(command));
        using Process process = new()
        {
            StartInfo = new ProcessStartInfo
            {
                FileName = "powershell.exe",
                Arguments = "-NoProfile -ExecutionPolicy Bypass -EncodedCommand " + encodedCommand,
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true
            }
        };

        process.Start();
        if (!process.WaitForExit(timeoutSeconds * 1000))
        {
            try
            {
                process.Kill(entireProcessTree: true);
            }
            catch
            {
                // Best effort.
            }
            return null;
        }

        string output = process.StandardOutput.ReadToEnd();
        if (string.IsNullOrWhiteSpace(output))
        {
            return null;
        }

        try
        {
            using JsonDocument doc = JsonDocument.Parse(output);
            return ConvertJsonElement(doc.RootElement);
        }
        catch
        {
            return null;
        }
    }

    private static object? ConvertJsonElement(JsonElement element)
    {
        switch (element.ValueKind)
        {
            case JsonValueKind.Object:
                Dictionary<string, object?> dict = new(StringComparer.OrdinalIgnoreCase);
                foreach (JsonProperty property in element.EnumerateObject())
                {
                    dict[ToSnakeCase(property.Name)] = ConvertJsonElement(property.Value);
                }
                return dict;
            case JsonValueKind.Array:
                return element.EnumerateArray().Select(ConvertJsonElement).ToList();
            case JsonValueKind.String:
                return element.GetString();
            case JsonValueKind.Number:
                if (element.TryGetInt64(out long longValue))
                {
                    return longValue;
                }
                if (element.TryGetDouble(out double doubleValue))
                {
                    return doubleValue;
                }
                return element.GetRawText();
            case JsonValueKind.True:
                return true;
            case JsonValueKind.False:
                return false;
            default:
                return null;
        }
    }

    private static string ToSnakeCase(string value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return value;
        }

        List<char> chars = new(value.Length + 8);
        for (int i = 0; i < value.Length; i++)
        {
            char c = value[i];
            if (char.IsUpper(c) && i > 0 && value[i - 1] != '_')
            {
                chars.Add('_');
            }
            chars.Add(char.ToLowerInvariant(c));
        }
        return new string(chars.ToArray());
    }

    private static List<string> AsStringList(object? value)
    {
        if (value is List<string> strings)
        {
            return strings;
        }
        if (value is IEnumerable<string> enumerable)
        {
            return enumerable.ToList();
        }
        if (value is IEnumerable<object?> objects)
        {
            return objects.Select(item => item?.ToString() ?? "").Where(item => !string.IsNullOrWhiteSpace(item)).ToList();
        }
        return new List<string>();
    }

    private static List<Dictionary<string, object?>> AsDictList(object? value)
    {
        if (value is List<Dictionary<string, object?>> dicts)
        {
            return dicts;
        }
        if (value is Dictionary<string, object?> dict)
        {
            return new List<Dictionary<string, object?>> { dict };
        }
        if (value is IEnumerable<object?> objects)
        {
            return objects
                .Select(AsDict)
                .Where(dict => dict.Count > 0)
                .ToList();
        }
        return new List<Dictionary<string, object?>>();
    }

    private static Dictionary<string, object?> AsDict(object? value)
    {
        if (value is Dictionary<string, object?> dict)
        {
            return dict;
        }
        return new Dictionary<string, object?>();
    }

    private static long? ToLong(object? value)
    {
        if (value is null)
        {
            return null;
        }
        if (value is long longValue)
        {
            return longValue;
        }
        if (long.TryParse(value.ToString(), NumberStyles.Any, CultureInfo.InvariantCulture, out long parsed))
        {
            return parsed;
        }
        return null;
    }

    private static bool? ToBool(object? value)
    {
        if (value is null)
        {
            return null;
        }
        if (value is bool boolValue)
        {
            return boolValue;
        }
        if (bool.TryParse(value.ToString(), out bool parsed))
        {
            return parsed;
        }
        return null;
    }

    private static bool IPAddressIsLoopback(string address)
    {
        return address.StartsWith("127.", StringComparison.Ordinal);
    }

    private static string TryGetFqdn()
    {
        try
        {
            return System.Net.Dns.GetHostEntry(Environment.MachineName).HostName;
        }
        catch
        {
            return Environment.MachineName;
        }
    }

    private static string FormatMac(string value)
    {
        if (string.IsNullOrWhiteSpace(value) || value.Length < 12)
        {
            return value;
        }
        return string.Join(":", Enumerable.Range(0, value.Length / 2).Select(i => value.Substring(i * 2, 2)));
    }

    private static string NormalizeKey(object? value)
    {
        return (value?.ToString() ?? "").Trim().ToLowerInvariant();
    }

    private static string? NormalizeInstallDate(string? value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return null;
        }
        if (DateTime.TryParseExact(value, "yyyyMMdd", CultureInfo.InvariantCulture, DateTimeStyles.AssumeLocal, out DateTime parsed))
        {
            return parsed.ToUniversalTime().ToString("o", CultureInfo.InvariantCulture);
        }
        return value;
    }
}
