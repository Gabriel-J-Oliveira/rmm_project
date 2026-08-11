using NightOwl.Agent.Windows.Models;
using NightOwl.Agent.Windows.Services;

try
{
    TestNewConfigContainsTrustedReleaseKeys();
    TestLegacyDefaultConfigReceivesTrustedReleaseKeys();
    TestMigrationIsIdempotent();
    TestMigrationDoesNotDuplicateJobTypes();
    TestPreservesIdentityAndEndpoints();
    TestPreservesIntervals();
    TestPreservesCustomAllowedJobTypes();
    TestMigratedConfigDoesNotRestoreExplicitRemoval();
    TestMigrationResultDoesNotExposeSecrets();

    Console.WriteLine("NightOwl agent config migration tests passed.");
}
catch (Exception ex)
{
    Console.Error.WriteLine(ex);
    Environment.Exit(1);
}

static AgentConfig LegacyConfig()
{
    return new AgentConfig
    {
        ConfigMigrationVersion = 1,
        AgentToken = "super-secret-token",
        MachineId = "machine-taxcel",
        ServerBaseUrl = "https://nightowl.controlsul.com.br",
        HeartbeatUrl = "https://nightowl.controlsul.com.br/api/agent/heartbeat/",
        CollectUrl = "https://nightowl.controlsul.com.br/api/agent/collect/",
        JobsPullUrl = "https://nightowl.controlsul.com.br/api/agent/jobs/pull/",
        JobsResultUrl = "https://nightowl.controlsul.com.br/api/agent/jobs/result/",
        Intervals = new AgentIntervals
        {
            HeartbeatSeconds = 123,
            CollectSeconds = 456,
            JobsSeconds = 78
        },
        AllowedJobTypes = new List<string>
        {
            "ping",
            "collect_logs",
            "collect_disks",
            "collect_software",
            "collect_security",
            "windows_update_scan",
            "force_inventory",
            "update_agent",
            "restart_agent"
        }
    };
}

static void TestNewConfigContainsTrustedReleaseKeys()
{
    AgentConfig config = new();
    ConfigService.ApplyConfigMigrations(config);

    Require(config.AllowedJobTypes.Contains("update_trusted_release_keys", StringComparer.OrdinalIgnoreCase), "New config should allow update_trusted_release_keys.");
    Require(config.ConfigMigrationVersion == ConfigService.CurrentConfigMigrationVersion, "New config should persist current migration version.");
}

static void TestLegacyDefaultConfigReceivesTrustedReleaseKeys()
{
    AgentConfig config = LegacyConfig();

    ConfigMigrationResult result = ConfigService.ApplyConfigMigrations(config);

    Require(result.Applied, "Legacy config should receive migration.");
    Require(result.FromVersion == 1, "Legacy migration should start at v1.");
    Require(result.ToVersion == ConfigService.CurrentConfigMigrationVersion, "Legacy migration should finish at current version.");
    Require(result.AddedAllowedJobTypes.Contains("update_trusted_release_keys"), "Migration result should report added job type.");
    Require(config.AllowedJobTypes.Contains("update_trusted_release_keys", StringComparer.OrdinalIgnoreCase), "Legacy config should allow update_trusted_release_keys.");
}

static void TestMigrationIsIdempotent()
{
    AgentConfig config = LegacyConfig();
    ConfigService.ApplyConfigMigrations(config);
    List<string> afterFirst = config.AllowedJobTypes.ToList();

    ConfigMigrationResult second = ConfigService.ApplyConfigMigrations(config);

    Require(!second.AddedAllowedJobTypes.Any(), "Second migration should not add job types.");
    Require(afterFirst.SequenceEqual(config.AllowedJobTypes), "Second migration should not alter allowed job types.");
}

static void TestMigrationDoesNotDuplicateJobTypes()
{
    AgentConfig config = LegacyConfig();
    config.AllowedJobTypes.Add("update_trusted_release_keys");
    config.AllowedJobTypes.Add("UPDATE_TRUSTED_RELEASE_KEYS");

    ConfigService.ApplyConfigMigrations(config);

    Require(config.AllowedJobTypes.Count(job => job.Equals("update_trusted_release_keys", StringComparison.OrdinalIgnoreCase)) == 1, "Migration should not duplicate update_trusted_release_keys.");
}

static void TestPreservesIdentityAndEndpoints()
{
    AgentConfig config = LegacyConfig();

    ConfigService.ApplyConfigMigrations(config);

    Require(config.MachineId == "machine-taxcel", "Migration should preserve machineId.");
    Require(config.AgentToken == "super-secret-token", "Migration should preserve agentToken.");
    Require(config.ServerBaseUrl == "https://nightowl.controlsul.com.br", "Migration should preserve serverBaseUrl.");
    Require(config.HeartbeatUrl.EndsWith("/api/agent/heartbeat/"), "Migration should preserve heartbeatUrl.");
    Require(config.JobsPullUrl.EndsWith("/api/agent/jobs/pull/"), "Migration should preserve jobsPullUrl.");
}

static void TestPreservesIntervals()
{
    AgentConfig config = LegacyConfig();

    ConfigService.ApplyConfigMigrations(config);

    Require(config.Intervals.HeartbeatSeconds == 123, "Migration should preserve heartbeat interval.");
    Require(config.Intervals.CollectSeconds == 456, "Migration should preserve collect interval.");
    Require(config.Intervals.JobsSeconds == 78, "Migration should preserve jobs interval.");
}

static void TestPreservesCustomAllowedJobTypes()
{
    AgentConfig config = LegacyConfig();
    config.AllowedJobTypes.Remove("collect_logs");
    config.AllowedJobTypes.Add("custom_local_job");

    ConfigService.ApplyConfigMigrations(config);

    Require(!config.AllowedJobTypes.Contains("collect_logs", StringComparer.OrdinalIgnoreCase), "Migration should preserve explicit legacy removal of unrelated job types.");
    Require(config.AllowedJobTypes.Contains("custom_local_job", StringComparer.OrdinalIgnoreCase), "Migration should preserve custom allowed job types.");
    Require(config.AllowedJobTypes.Contains("update_trusted_release_keys", StringComparer.OrdinalIgnoreCase), "Migration should add the new known default to legacy configs.");
}

static void TestMigratedConfigDoesNotRestoreExplicitRemoval()
{
    AgentConfig config = LegacyConfig();
    config.ConfigMigrationVersion = ConfigService.CurrentConfigMigrationVersion;
    config.AllowedJobTypes.Remove("update_trusted_release_keys");

    ConfigMigrationResult result = ConfigService.ApplyConfigMigrations(config);

    Require(!result.AddedAllowedJobTypes.Any(), "Already migrated config should not add removed job types.");
    Require(!config.AllowedJobTypes.Contains("update_trusted_release_keys", StringComparer.OrdinalIgnoreCase), "Already migrated config should preserve explicit removal.");
}

static void TestMigrationResultDoesNotExposeSecrets()
{
    AgentConfig config = LegacyConfig();

    ConfigMigrationResult result = ConfigService.ApplyConfigMigrations(config);
    string text = string.Join("|", result.AddedAllowedJobTypes) + "|" + result.FromVersion + "|" + result.ToVersion;

    Require(!text.Contains(config.AgentToken, StringComparison.OrdinalIgnoreCase), "Migration result should not expose agent token.");
    Require(!text.Contains(config.MachineId, StringComparison.OrdinalIgnoreCase), "Migration result should not expose machine id.");
}

static void Require(bool condition, string message)
{
    if (!condition)
    {
        throw new InvalidOperationException(message);
    }
}
