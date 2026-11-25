using System;
using System.IO;

namespace NightOwl.Agent.Shared;

public static class UninstallerRunnerPayload
{
    public const string UninstallerExecutableName = "NightOwl.Agent.Uninstaller.exe";

    public static bool SupportsVerifiedRemoval(string installPath)
    {
        string path = Path.Combine(installPath, UninstallerExecutableName);
        if (!File.Exists(path)) return false;
        try
        {
            var version = System.Diagnostics.FileVersionInfo.GetVersionInfo(path);
            return new Version(version.FileMajorPart, version.FileMinorPart, version.FileBuildPart,
                version.FilePrivatePart) >= new Version(0, 1, 0, 8);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or System.ComponentModel.Win32Exception)
        {
            return false;
        }
    }

    public static UninstallerRunnerPayloadResult Prepare(string installPath, string runnerDir, bool verifiedRemoval = false)
    {
        if (string.IsNullOrWhiteSpace(installPath))
        {
            throw new ArgumentException("Install path is required.", nameof(installPath));
        }
        if (string.IsNullOrWhiteSpace(runnerDir))
        {
            throw new ArgumentException("Runner path is required.", nameof(runnerDir));
        }
        if (!Directory.Exists(installPath))
        {
            throw new DirectoryNotFoundException(installPath);
        }

        string sourceRunner = Path.Combine(installPath, UninstallerExecutableName);
        if (!File.Exists(sourceRunner))
        {
            throw new FileNotFoundException("Uninstaller nao encontrado no endpoint.", sourceRunner);
        }

        if (Directory.Exists(runnerDir))
        {
            if (verifiedRemoval)
                throw new InvalidOperationException("REMOVAL_RUNNER_ALREADY_STAGED");
            Directory.Delete(runnerDir, recursive: true);
        }
        Directory.CreateDirectory(runnerDir);

        int filesCopied = 0;
        int reparsePointsSkipped = 0;
        IEnumerable<string> candidates = verifiedRemoval
            ? Directory.EnumerateFiles(installPath).Where(IsVerifiedRunnerFile)
            : Directory.EnumerateFileSystemEntries(installPath, "*", SearchOption.AllDirectories);
        foreach (string source in candidates)
        {
            FileAttributes attributes = File.GetAttributes(source);
            if ((attributes & FileAttributes.ReparsePoint) != 0)
            {
                reparsePointsSkipped++;
                continue;
            }

            string relative = Path.GetRelativePath(installPath, source);
            string destination = Path.Combine(runnerDir, relative);
            if ((attributes & FileAttributes.Directory) != 0)
            {
                Directory.CreateDirectory(destination);
                continue;
            }

            Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
            File.Copy(source, destination, overwrite: true);
            filesCopied++;
        }

        string runnerExecutable = Path.Combine(runnerDir, UninstallerExecutableName);
        if (!File.Exists(runnerExecutable))
        {
            throw new FileNotFoundException("Payload do runner de desinstalacao sem executavel principal.", runnerExecutable);
        }

        return new UninstallerRunnerPayloadResult(runnerExecutable, filesCopied, reparsePointsSkipped);
    }

    private static bool IsVerifiedRunnerFile(string path)
    {
        string name = Path.GetFileName(path);
        if (name.StartsWith("NightOwl.", StringComparison.OrdinalIgnoreCase))
            return name.Equals(UninstallerExecutableName, StringComparison.OrdinalIgnoreCase)
                || name.Equals("NightOwl.Agent.Uninstaller.dll", StringComparison.OrdinalIgnoreCase)
                || name.Equals("NightOwl.Agent.Shared.dll", StringComparison.OrdinalIgnoreCase)
                || name.Equals("NightOwl.Agent.Uninstaller.deps.json", StringComparison.OrdinalIgnoreCase)
                || name.Equals("NightOwl.Agent.Uninstaller.runtimeconfig.json", StringComparison.OrdinalIgnoreCase);
        return Path.GetExtension(name).Equals(".dll", StringComparison.OrdinalIgnoreCase);
    }
}

public sealed record UninstallerRunnerPayloadResult(string RunnerExecutable, int FilesCopied, int ReparsePointsSkipped);
