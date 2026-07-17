using System.Text.Json;
using NightOwl.Agent.Shared;

string root = Path.Combine(Path.GetTempPath(), "NightOwlPathsTests", Guid.NewGuid().ToString("N"));
Directory.CreateDirectory(root);
Environment.SetEnvironmentVariable("NIGHTOWL_HOME", root);

try
{
    NightOwlPaths paths = NightOwlPaths.FromEnvironment();
    Directory.CreateDirectory(paths.InstallDir);
    File.WriteAllText(paths.LegacyConfigPath, """
    {
      "agentToken": "token-test",
      "machineId": "machine-test",
      "serverBaseUrl": "https://nightowl.example",
      "heartbeatUrl": "https://nightowl.example/api/agent/heartbeat/"
    }
    """);
    File.WriteAllText(paths.LegacyStatePath, """
    {
      "machine_id": "machine-test",
      "lastHeartbeatAt": "2026-07-17T10:00:00Z"
    }
    """);

    paths.Bootstrap("test", applyAcl: false);
    Require(File.Exists(paths.ConfigPath), "Config was not migrated.");
    Require(File.Exists(paths.StatePath), "State was not migrated.");
    Require(File.Exists(paths.IdentityPath), "Identity was not prepared.");
    Require(Directory.Exists(paths.PendingResultsDir), "Pending results directory was not created.");

    string firstConfig = File.ReadAllText(paths.ConfigPath);
    string firstState = File.ReadAllText(paths.StatePath);
    string firstIdentity = File.ReadAllText(paths.IdentityPath);
    paths.Bootstrap("test", applyAcl: false);
    Require(firstConfig == File.ReadAllText(paths.ConfigPath), "Second bootstrap changed config.");
    Require(firstState == File.ReadAllText(paths.StatePath), "Second bootstrap changed state.");
    Require(firstIdentity == File.ReadAllText(paths.IdentityPath), "Second bootstrap changed identity.");

    using JsonDocument identity = JsonDocument.Parse(firstIdentity);
    Require(identity.RootElement.GetProperty("machine_id").GetString() == "machine-test", "Identity machine_id was not preserved.");

    Console.WriteLine("NightOwlPaths migration tests passed.");
}
finally
{
    try
    {
        Directory.Delete(root, recursive: true);
    }
    catch
    {
        // Best-effort cleanup.
    }
}

static void Require(bool condition, string message)
{
    if (!condition)
    {
        throw new InvalidOperationException(message);
    }
}
