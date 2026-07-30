using UpdaterProgram = NightOwl.Agent.Updater.Program;

try
{
    Require(
        UpdaterProgram.CompareVersions("0.1.1.0-rc3", "0.1.1.0-rc2") > 0,
        "RC3 should compare newer than RC2.");
    Require(
        UpdaterProgram.DecideVersionAction("0.1.1.0-rc2", "0.1.1.0-rc3", force: false) == UpdaterProgram.VersionUpdateAction.UpdateAllowed,
        "RC2 to RC3 should continue to update.");
    Require(
        UpdaterProgram.DecideVersionAction("0.1.1.0-rc3", "0.1.1.0-rc3", force: false) == UpdaterProgram.VersionUpdateAction.AlreadyCurrent,
        "Same installed and target version should be already_current.");
    Require(
        UpdaterProgram.DecideVersionAction("0.1.1.0-rc3", "0.1.1.0-rc2", force: false) == UpdaterProgram.VersionUpdateAction.DowngradeBlocked,
        "Downgrade without force should be blocked.");
    Require(
        UpdaterProgram.DecideVersionAction("0.1.1.0-rc3", "0.1.1.0-rc2", force: true) == UpdaterProgram.VersionUpdateAction.UpdateAllowed,
        "Downgrade with force should be allowed by updater version decision.");

    Console.WriteLine("NightOwl updater version decision tests passed.");
}
catch (Exception ex)
{
    Console.Error.WriteLine(ex.Message);
    Environment.Exit(1);
}

static void Require(bool condition, string message)
{
    if (!condition)
    {
        throw new InvalidOperationException(message);
    }
}
