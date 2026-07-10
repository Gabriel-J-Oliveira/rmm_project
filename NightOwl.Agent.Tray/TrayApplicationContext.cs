using System.ComponentModel;
using System.Diagnostics;
using System.Drawing.Drawing2D;
using System.Runtime.InteropServices;
using System.ServiceProcess;

namespace NightOwl.Agent.Tray;

internal enum AgentTrayStatus
{
    Online,
    Warning,
    Offline
}

internal sealed class TrayApplicationContext : ApplicationContext
{
    private readonly NotifyIcon _notifyIcon;
    private readonly System.Windows.Forms.Timer _timer;
    private AgentLocalState _state = AgentLocalState.Load();
    private AgentTrayStatus _status = AgentTrayStatus.Offline;
    private Icon? _currentIcon;

    public TrayApplicationContext()
    {
        ContextMenuStrip menu = BuildMenu();
        _notifyIcon = new NotifyIcon
        {
            ContextMenuStrip = menu,
            Text = "NightOwl Agent",
            Visible = true
        };
        _notifyIcon.DoubleClick += (_, _) => ShowStatusWindow();

        _timer = new System.Windows.Forms.Timer
        {
            Interval = 30000
        };
        _timer.Tick += (_, _) => RefreshStatus();
        RefreshStatus();
        _timer.Start();
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            _timer.Stop();
            _timer.Dispose();
            _notifyIcon.Visible = false;
            _notifyIcon.Dispose();
            _currentIcon?.Dispose();
        }

        base.Dispose(disposing);
    }

    private ContextMenuStrip BuildMenu()
    {
        ContextMenuStrip menu = new();
        menu.Items.Add("Abrir NightOwl", null, (_, _) => OpenNightOwl());
        menu.Items.Add("Status do agente", null, (_, _) => ShowStatusWindow());
        menu.Items.Add("Forçar inventário", null, (_, _) => ForceInventory());
        menu.Items.Add("Reiniciar agente", null, (_, _) => RestartAgentService());
        menu.Items.Add("Ver logs", null, (_, _) => OpenLogs());
        menu.Items.Add("Copiar ID da máquina", null, (_, _) => CopyMachineId());
        menu.Items.Add("Sobre", null, (_, _) => ShowAbout());
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add("Sair da bandeja", null, (_, _) => ExitTray());
        return menu;
    }

    private void RefreshStatus()
    {
        _state = AgentLocalState.Load();
        bool serviceInstalled = TryGetService(out ServiceController? service);
        bool serviceRunning = serviceInstalled && service?.Status == ServiceControllerStatus.Running;
        DateTimeOffset? heartbeat = _state.LastHeartbeatAt;
        bool heartbeatRecent = heartbeat is not null && DateTimeOffset.UtcNow - heartbeat.Value.ToUniversalTime() <= TimeSpan.FromMinutes(10);

        AgentTrayStatus next = serviceRunning && heartbeatRecent
            ? AgentTrayStatus.Online
            : serviceRunning
                ? AgentTrayStatus.Warning
                : AgentTrayStatus.Offline;

        _status = next;
        _notifyIcon.Text = TrimTooltip($"NightOwl Agent | Status: {StatusLabel(next)} | Heartbeat: {FormatDate(heartbeat)}");
        SetIcon(next);

        service?.Dispose();
    }

    private void SetIcon(AgentTrayStatus status)
    {
        Icon icon = BuildStatusIcon(status);
        Icon? old = _currentIcon;
        _currentIcon = icon;
        _notifyIcon.Icon = icon;
        old?.Dispose();
    }

    private static Icon BuildStatusIcon(AgentTrayStatus status)
    {
        string iconPath = Path.Combine(AppContext.BaseDirectory, "NightOwl.ico");
        if (!File.Exists(iconPath))
        {
            iconPath = Path.Combine(AppContext.BaseDirectory, "assets", "NightOwl.ico");
        }

        using Bitmap bitmap = new(32, 32);
        using Graphics graphics = Graphics.FromImage(bitmap);
        graphics.SmoothingMode = SmoothingMode.AntiAlias;
        graphics.Clear(Color.Transparent);

        if (File.Exists(iconPath))
        {
            using Icon baseIcon = new(iconPath, 32, 32);
            graphics.DrawIcon(baseIcon, new Rectangle(0, 0, 32, 32));
        }
        else
        {
            using LinearGradientBrush brush = new(new Rectangle(0, 0, 32, 32), Color.FromArgb(124, 58, 237), Color.FromArgb(38, 214, 126), 45);
            graphics.FillEllipse(brush, 2, 2, 28, 28);
            using Font font = new("Segoe UI", 13, FontStyle.Bold);
            TextRenderer.DrawText(graphics, "N", font, new Rectangle(0, 3, 32, 26), Color.White, TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter);
        }

        Color dot = status switch
        {
            AgentTrayStatus.Online => Color.FromArgb(38, 214, 126),
            AgentTrayStatus.Warning => Color.FromArgb(245, 158, 11),
            _ => Color.FromArgb(239, 68, 68)
        };
        using SolidBrush dotBrush = new(dot);
        using Pen border = new(Color.FromArgb(8, 12, 18), 2);
        graphics.FillEllipse(dotBrush, 20, 20, 10, 10);
        graphics.DrawEllipse(border, 20, 20, 10, 10);

        IntPtr handle = bitmap.GetHicon();
        try
        {
            using Icon temp = Icon.FromHandle(handle);
            return (Icon)temp.Clone();
        }
        finally
        {
            DestroyIcon(handle);
        }
    }

    private bool TryGetService(out ServiceController? service)
    {
        try
        {
            service = new ServiceController(_state.ServiceName);
            _ = service.Status;
            return true;
        }
        catch
        {
            service = null;
            return false;
        }
    }

    private void OpenNightOwl()
    {
        string url = string.IsNullOrWhiteSpace(_state.ServerBaseUrl)
            ? "https://nightowl.controlsul.com.br"
            : _state.ServerBaseUrl;
        OpenPath(url);
    }

    private void ShowStatusWindow()
    {
        bool installed = TryGetService(out ServiceController? service);
        bool running = installed && service?.Status == ServiceControllerStatus.Running;
        string status = StatusLabel(_status);
        string heartbeat = FormatDate(_state.LastHeartbeatAt);
        service?.Dispose();

        using Form form = new()
        {
            Text = "NightOwl Agent - Status",
            Width = 560,
            Height = 420,
            StartPosition = FormStartPosition.CenterScreen,
            FormBorderStyle = FormBorderStyle.FixedDialog,
            MaximizeBox = false,
            MinimizeBox = false
        };

        TableLayoutPanel table = new()
        {
            Dock = DockStyle.Fill,
            Padding = new Padding(18),
            ColumnCount = 2,
            RowCount = 10,
            AutoSize = true
        };
        table.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 180));
        table.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));

        AddRow(table, "Status", status);
        AddRow(table, "Serviço instalado", installed ? "Sim" : "Não");
        AddRow(table, "Serviço em execução", running ? "Sim" : "Não");
        AddRow(table, "Servidor", _state.ServerBaseUrl);
        AddRow(table, "Machine ID", Safe(_state.MachineId));
        AddRow(table, "Endpoint ID", Safe(_state.EndpointId));
        AddRow(table, "Último heartbeat", heartbeat);
        AddRow(table, "Versão do agente", Safe(_state.AgentVersion));
        AddRow(table, "Instalação", _state.InstallPath);
        AddRow(table, "Log", _state.LogPath);

        Button close = new()
        {
            Text = "Fechar",
            Dock = DockStyle.Bottom,
            Height = 34
        };
        close.Click += (_, _) => form.Close();

        form.Controls.Add(table);
        form.Controls.Add(close);
        form.ShowDialog();
    }

    private static void AddRow(TableLayoutPanel table, string label, string value)
    {
        Label labelControl = new()
        {
            Text = label,
            Dock = DockStyle.Fill,
            Font = new Font("Segoe UI", 9, FontStyle.Bold),
            TextAlign = ContentAlignment.MiddleLeft
        };
        Label valueControl = new()
        {
            Text = value,
            Dock = DockStyle.Fill,
            AutoEllipsis = true,
            TextAlign = ContentAlignment.MiddleLeft
        };
        table.Controls.Add(labelControl);
        table.Controls.Add(valueControl);
    }

    private static void ForceInventory()
    {
        MessageBox.Show(
            "A ação local de forçar inventário será implementada quando o agente expuser um canal local seguro. Por enquanto, use o Endpoint Detail no NightOwl para criar o job force_inventory.",
            "NightOwl Agent",
            MessageBoxButtons.OK,
            MessageBoxIcon.Information);
    }

    private void RestartAgentService()
    {
        try
        {
            using ServiceController service = new(_state.ServiceName);
            if (service.Status != ServiceControllerStatus.Stopped && service.Status != ServiceControllerStatus.StopPending)
            {
                service.Stop();
                service.WaitForStatus(ServiceControllerStatus.Stopped, TimeSpan.FromSeconds(30));
            }

            service.Start();
            service.WaitForStatus(ServiceControllerStatus.Running, TimeSpan.FromSeconds(30));
            RefreshStatus();
            MessageBox.Show("Serviço NightOwl reiniciado.", "NightOwl Agent", MessageBoxButtons.OK, MessageBoxIcon.Information);
        }
        catch (Win32Exception)
        {
            MessageBox.Show("Não foi possível reiniciar o serviço. Execute a bandeja ou a ação como administrador.", "NightOwl Agent", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        }
        catch (UnauthorizedAccessException)
        {
            MessageBox.Show("Permissão insuficiente para reiniciar o serviço. Execute como administrador.", "NightOwl Agent", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        }
        catch (Exception ex)
        {
            MessageBox.Show("Falha ao reiniciar o serviço: " + ex.Message, "NightOwl Agent", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        }
    }

    private void OpenLogs()
    {
        if (!File.Exists(_state.LogPath))
        {
            MessageBox.Show("Arquivo de log ainda não existe: " + _state.LogPath, "NightOwl Agent", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }

        Process.Start(new ProcessStartInfo
        {
            FileName = "notepad.exe",
            Arguments = "\"" + _state.LogPath + "\"",
            UseShellExecute = true
        });
    }

    private void CopyMachineId()
    {
        if (string.IsNullOrWhiteSpace(_state.MachineId))
        {
            MessageBox.Show("Machine ID ainda não disponível.", "NightOwl Agent", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }

        Clipboard.SetText(_state.MachineId);
    }

    private void ShowAbout()
    {
        MessageBox.Show(
            $"NightOwl Agent Tray\n\nVersão do agente: {Safe(_state.AgentVersion)}\nInstalação: {_state.InstallPath}\nServidor: {_state.ServerBaseUrl}",
            "Sobre o NightOwl Agent",
            MessageBoxButtons.OK,
            MessageBoxIcon.Information);
    }

    private void ExitTray()
    {
        _notifyIcon.Visible = false;
        ExitThread();
    }

    private static void OpenPath(string path)
    {
        Process.Start(new ProcessStartInfo
        {
            FileName = path,
            UseShellExecute = true
        });
    }

    private static string StatusLabel(AgentTrayStatus status) => status switch
    {
        AgentTrayStatus.Online => "Online",
        AgentTrayStatus.Warning => "Atenção",
        _ => "Offline"
    };

    private static string FormatDate(DateTimeOffset? value)
    {
        if (value is null)
        {
            return "não encontrado";
        }

        return value.Value.ToLocalTime().ToString("dd/MM/yyyy HH:mm:ss");
    }

    private static string Safe(string value) => string.IsNullOrWhiteSpace(value) ? "-" : value;

    private static string TrimTooltip(string value)
    {
        return value.Length <= 63 ? value : value[..60] + "...";
    }

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool DestroyIcon(IntPtr hIcon);
}
