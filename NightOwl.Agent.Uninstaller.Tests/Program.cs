using NightOwl.Agent.Shared;

try
{
    string dir = Path.Combine(Path.GetTempPath(), "NightOwlUninstallerTests", Guid.NewGuid().ToString("N"));
    Directory.CreateDirectory(dir);
    try
    {
        string path = Path.Combine(dir, "receipt.json");
        NightOwlFileStore.WriteAllText(path, "{\"status\":\"started\"}");
        NightOwlFileStore.WriteAllText(path, "{\"status\":\"completed\"}");
        Require(File.ReadAllText(path).Contains("completed", StringComparison.Ordinal), "Safe overwrite should persist final receipt.");
        Require(Directory.GetFiles(dir, "*.tmp").Length == 0, "Safe overwrite should not leave temp files.");
    }
    finally
    {
        try { Directory.Delete(dir, recursive: true); } catch { }
    }

    Console.WriteLine("NightOwl uninstaller tests passed.");
}
catch (Exception ex)
{
    Console.Error.WriteLine(ex);
    Environment.Exit(1);
}

static void Require(bool condition, string message)
{
    if (!condition)
    {
        throw new InvalidOperationException(message);
    }
}
