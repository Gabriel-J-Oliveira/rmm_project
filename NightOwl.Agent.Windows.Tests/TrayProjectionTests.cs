using System.Text.Json;
using NightOwl.Agent.Shared;
using NightOwl.Agent.Windows.Models;
using NightOwl.Agent.Windows.Services;

internal static class TrayProjectionTests
{
    internal static async Task RunAsync()
    {
        string root = Path.Combine(Path.GetTempPath(), "NightOwlPublicTrayTests", Guid.NewGuid().ToString("N"));
        string? previousHome = Environment.GetEnvironmentVariable("NIGHTOWL_HOME");
        Environment.SetEnvironmentVariable("NIGHTOWL_HOME", root);
        try
        {
            AgentConfig config = new() { AgentToken = "fixture-private-token", MachineId = "fixture-machine",
                ServerBaseUrl = "https://user:fixture-password@isolated.invalid/prefix?token=fixture-query#fragment",
                StatePath = Path.Combine(root, "private", "state.json"), AgentVersion = "fixture-version" };
            AgentState state = new() { LastHeartbeatAt = DateTimeOffset.UtcNow };
            string endpointId = Guid.NewGuid().ToString("D");
            state.ExtensionData["endpoint_id"] = JsonSerializer.SerializeToElement(endpointId);
            StateService service = new();
            await service.SaveAsync(config, state, CancellationToken.None);
            string path = TrayRuntimeStatus.PathFor(NightOwlPaths.Current);
            string content = File.ReadAllText(path);
            Require(!content.Contains("fixture-private-token") && !content.Contains("fixture-password") && !content.Contains("fixture-query"), "Public Tray projection exposed credentials.");
            TrayRuntimeStatus snapshot = JsonSerializer.Deserialize<TrayRuntimeStatus>(content, new JsonSerializerOptions(JsonSerializerDefaults.Web))!;
            Require(snapshot.MachineId == config.MachineId && snapshot.EndpointId == endpointId && snapshot.LastHeartbeatAt == state.LastHeartbeatAt
                && snapshot.ServerBaseUrl == "https://isolated.invalid/prefix", "Projection changed authoritative state or server path.");
            DateTime stamp = DateTime.UtcNow.AddDays(-1);
            File.SetLastWriteTimeUtc(path, stamp);
            await service.SaveAsync(config, state, CancellationToken.None);
            Require(File.GetLastWriteTimeUtc(path) == stamp, "Unchanged state caused a redundant projection write.");
            state.LastHeartbeatAt = state.LastHeartbeatAt.Value.AddMinutes(1);
            using (FileStream locked = new(path, FileMode.Open, FileAccess.Read, FileShare.None))
                await service.SaveAsync(config, state, CancellationToken.None);
            Require(File.ReadAllText(config.StatePath).Contains("fixture-machine"), "Projection failure blocked essential state persistence.");
            await service.SaveAsync(config, state, CancellationToken.None);
            snapshot = JsonSerializer.Deserialize<TrayRuntimeStatus>(File.ReadAllText(path), new JsonSerializerOptions(JsonSerializerDefaults.Web))!;
            Require(snapshot.LastHeartbeatAt == state.LastHeartbeatAt, "Projection did not retry after reader released the file.");
            File.Delete(path);
            await service.SaveAsync(config, state, CancellationToken.None);
            Require(File.Exists(path), "Deleted UI projection was not restored.");
            Console.WriteLine("PASS Tray projection: credential isolation, private state independence, changed-only writes, lock/retry and recovery.");
        }
        finally
        {
            Environment.SetEnvironmentVariable("NIGHTOWL_HOME", previousHome);
            string expected = Path.Combine(Path.GetTempPath(), "NightOwlPublicTrayTests") + Path.DirectorySeparatorChar;
            if (!Path.GetFullPath(root).StartsWith(Path.GetFullPath(expected), StringComparison.OrdinalIgnoreCase)) throw new Exception("Unsafe fixture cleanup.");
            if (Directory.Exists(root)) Directory.Delete(root, true);
        }
    }
    private static void Require(bool condition, string message) { if (!condition) throw new Exception(message); }
}
