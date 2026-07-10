namespace NightOwl.Agent.Tray;

internal static class Program
{
    [STAThread]
    private static void Main()
    {
        ApplicationConfiguration.Initialize();
        using TrayApplicationContext context = new();
        Application.Run(context);
    }
}
