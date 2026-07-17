namespace NightOwl.Agent.Tray;

internal static class Program
{
    private const string MutexName = "NightOwl.Agent.Tray.User";

    [STAThread]
    private static void Main()
    {
        NightOwl.Agent.Shared.NightOwlPaths.Current.Bootstrap("tray", applyAcl: false);
        using Mutex mutex = new(false, MutexName, out bool createdNew);
        if (!createdNew)
        {
            TrayLog.Write("tray.mutex.exists", "Outra instancia do tray ja esta em execucao.");
            return;
        }

        if (!TrayApplicationContext.IsServiceRunning())
        {
            TrayLog.Write("tray.exit.service_not_running", "Servico NightOwlAgentDotNet ausente ou parado.");
            return;
        }

        SetAppUserModelId();
        TrayLog.Write("tray.start", "NightOwl Agent Tray iniciado.");
        ApplicationConfiguration.Initialize();
        using TrayApplicationContext context = new();
        Application.Run(context);
    }

    private static void SetAppUserModelId()
    {
        try
        {
            _ = SetCurrentProcessExplicitAppUserModelID("NightOwl.Agent.Tray");
        }
        catch (Exception ex)
        {
            TrayLog.Write("tray.error", "Nao foi possivel definir AppUserModelID.", new { error = ex.Message });
        }
    }

    [System.Runtime.InteropServices.DllImport("shell32.dll", CharSet = System.Runtime.InteropServices.CharSet.Unicode)]
    private static extern int SetCurrentProcessExplicitAppUserModelID(string appId);
}
