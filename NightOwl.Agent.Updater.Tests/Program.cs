using UpdaterProgram = NightOwl.Agent.Updater.Program;
using System.Security.Cryptography;
using System.Text;

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

    using RSA signingKey = RSA.Create(2048);
    string publicXml = signingKey.ToXmlString(false);
    byte[] manifestBytes = Encoding.UTF8.GetBytes("{\"channel\":\"development\",\"key_id\":\"nightowl-test\",\"version\":\"0.1.1.0-rc6\"}");
    byte[] signature = signingKey.SignData(
        manifestBytes,
        HashAlgorithmName.SHA256,
        RSASignaturePadding.Pss);
    Require(
        UpdaterProgram.VerifyReleaseManifestSignatureForTest(manifestBytes, signature, publicXml),
        "Updater should accept a valid RSA-PSS/SHA-256 release manifest signature.");

    byte[] tamperedManifest = (byte[])manifestBytes.Clone();
    tamperedManifest[0] ^= 0x01;
    Require(
        !UpdaterProgram.VerifyReleaseManifestSignatureForTest(tamperedManifest, signature, publicXml),
        "Updater should reject a tampered release manifest.");

    using RSA differentKey = RSA.Create(2048);
    Require(
        !UpdaterProgram.VerifyReleaseManifestSignatureForTest(manifestBytes, signature, differentKey.ToXmlString(false)),
        "Updater should reject a release manifest signature verified with a different key.");

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
