using System.ComponentModel;
using System.Diagnostics;
using System.Drawing.Drawing2D;
using System.Reflection;
using System.ServiceProcess;

namespace NightOwl.Agent.Tray;

internal sealed class TrayApplicationContext : ApplicationContext
{
    private readonly NotifyIcon _notifyIcon;
    private readonly System.Windows.Forms.Timer _timer;
    private readonly Icon _icon;
    private AgentLocalState _state = AgentLocalState.Load();
    private string _serviceStatus = "Running";

    public TrayApplicationContext()
    {
        _icon = LoadNightOwlIcon();
        _notifyIcon = new NotifyIcon
        {
            ContextMenuStrip = BuildMenu(),
            Icon = _icon,
            Text = BuildTooltip("Running"),
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

    public static bool IsServiceRunning()
    {
        try
        {
            using ServiceController service = new("NightOwlAgentDotNet");
            return service.Status == ServiceControllerStatus.Running;
        }
        catch
        {
            return false;
        }
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            _timer.Stop();
            _timer.Dispose();
            _notifyIcon.Visible = false;
            _notifyIcon.Dispose();
            _icon.Dispose();
        }

        base.Dispose(disposing);
    }

    private ContextMenuStrip BuildMenu()
    {
        ContextMenuStrip menu = new();
        menu.Items.Add("Abrir NightOwl", null, (_, _) => OpenNightOwl());
        menu.Items.Add("Status do agente", null, (_, _) => ShowStatusWindow());
        menu.Items.Add("Atualizar agente", null, (_, _) => UpdateAgent());
        menu.Items.Add("Reiniciar agente", null, (_, _) => RestartAgentService());
        menu.Items.Add("Sobre", null, (_, _) => ShowAbout());
        return menu;
    }

    private void RefreshStatus()
    {
        _state = AgentLocalState.Load();
        if (!TryGetService(out ServiceController? service))
        {
            TrayLog.Write("tray.exit.service_not_running", "Servico NightOwlAgentDotNet nao encontrado durante refresh.");
            ExitTray();
            return;
        }

        using (ServiceController currentService = service!)
        {
            _serviceStatus = currentService.Status.ToString();
            if (currentService.Status != ServiceControllerStatus.Running)
            {
                TrayLog.Write("tray.service.stopped", "Servico NightOwlAgentDotNet nao esta em execucao.", new { status = _serviceStatus });
                ExitTray();
                return;
            }
        }

        TrayLog.Write("tray.service.running", "Servico NightOwlAgentDotNet em execucao.");
        _notifyIcon.Text = BuildTooltip(_serviceStatus);
    }

    private string BuildTooltip(string serviceStatus)
    {
        string server = FormatServer(_state.ServerBaseUrl);
        string version = string.IsNullOrWhiteSpace(_state.AgentVersion) ? "-" : _state.AgentVersion;
        return TrimTooltip($"NightOwl Agent | Servico: {serviceStatus} | Servidor: {server} | Versao: {version}");
    }

    private Icon LoadNightOwlIcon()
    {
        string installIcon = Path.Combine(@"C:\ProgramData\NightOwl\AgentDotNet", "assets", "icons", "NightOwl.ico");
        string appIcon = Path.Combine(AppContext.BaseDirectory, "assets", "icons", "NightOwl.ico");

        foreach (string path in new[] { installIcon, appIcon })
        {
            try
            {
                if (File.Exists(path))
                {
                    TrayLog.Write("tray.icon.loaded", "Icone NightOwl carregado do arquivo.", new { path });
                    return new Icon(path);
                }
            }
            catch (Exception ex)
            {
                TrayLog.Write("tray.error", "Falha ao carregar icone do arquivo.", new { path, error = ex.Message });
            }
        }

        try
        {
            Stream? stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("NightOwl.ico");
            if (stream is not null)
            {
                TrayLog.Write("tray.icon.loaded", "Icone NightOwl carregado do recurso embutido.");
                return new Icon(stream);
            }
        }
        catch (Exception ex)
        {
            TrayLog.Write("tray.error", "Falha ao carregar icone embutido.", new { error = ex.Message });
        }

        TrayLog.Write("tray.icon.fallback", "Usando icone fallback gerado em runtime.");
        return BuildFallbackIcon();
    }

    private static Icon BuildFallbackIcon()
    {
        using Bitmap bitmap = new(32, 32);
        using Graphics graphics = Graphics.FromImage(bitmap);
        graphics.SmoothingMode = SmoothingMode.AntiAlias;
        graphics.Clear(Color.Transparent);

        using GraphicsPath bg = RoundedRect(new RectangleF(1, 1, 30, 30), 7);
        using LinearGradientBrush bgBrush = new(new RectangleF(1, 1, 30, 30), Color.FromArgb(7, 10, 20), Color.FromArgb(37, 18, 82), 45);
        using Pen border = new(Color.FromArgb(139, 92, 246), 2);
        graphics.FillPath(bgBrush, bg);
        graphics.DrawPath(border, bg);

        using SolidBrush face = new(Color.FromArgb(124, 58, 237));
        graphics.FillEllipse(face, 6, 6, 20, 21);
        using SolidBrush eye = new(Color.FromArgb(240, 253, 244));
        using SolidBrush pupil = new(Color.FromArgb(11, 18, 32));
        graphics.FillEllipse(eye, 7, 11, 8, 8);
        graphics.FillEllipse(eye, 17, 11, 8, 8);
        graphics.FillEllipse(pupil, 10, 14, 3, 3);
        graphics.FillEllipse(pupil, 20, 14, 3, 3);
        using SolidBrush beak = new(Color.FromArgb(250, 204, 21));
        graphics.FillPolygon(beak, new[] { new Point(16, 18), new Point(12, 23), new Point(20, 23) });

        IntPtr handle = bitmap.GetHicon();
        try
        {
            using Icon temp = Icon.FromHandle(handle);
            return (Icon)temp.Clone();
        }
        finally
        {
            _ = DestroyIcon(handle);
        }
    }

    private static GraphicsPath RoundedRect(RectangleF bounds, float radius)
    {
        float diameter = radius * 2;
        GraphicsPath path = new();
        path.AddArc(bounds.X, bounds.Y, diameter, diameter, 180, 90);
        path.AddArc(bounds.Right - diameter, bounds.Y, diameter, diameter, 270, 90);
        path.AddArc(bounds.Right - diameter, bounds.Bottom - diameter, diameter, diameter, 0, 90);
        path.AddArc(bounds.X, bounds.Bottom - diameter, diameter, diameter, 90, 90);
        path.CloseFigure();
        return path;
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
        TrayLog.Write("tray.menu.open_nightowl", "Abrindo NightOwl pelo menu da bandeja.");
        string url = string.IsNullOrWhiteSpace(_state.ServerBaseUrl)
            ? "https://nightowl.controlsul.com.br"
            : _state.ServerBaseUrl;
        OpenPath(url);
    }

    private void ShowStatusWindow()
    {
        bool installed = TryGetService(out ServiceController? service);
        string status = installed ? service!.Status.ToString() : "Nao instalado";
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
            MinimizeBox = false,
            Icon = _icon
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

        AddRow(table, "Servico instalado", installed ? "Sim" : "Nao");
        AddRow(table, "Servico em execucao", status == "Running" ? "Sim" : "Nao");
        AddRow(table, "Status do servico", status);
        AddRow(table, "Servidor", Safe(_state.ServerBaseUrl));
        AddRow(table, "Machine ID", Safe(_state.MachineId));
        AddRow(table, "Endpoint ID", Safe(_state.EndpointId));
        AddRow(table, "Ultimo heartbeat", heartbeat);
        AddRow(table, "Versao do agente", Safe(_state.AgentVersion));
        AddRow(table, "Instalacao", _state.InstallPath);
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

    private void UpdateAgent()
    {
        TrayLog.Write("tray.menu.update_agent", "Usuario acionou atualizacao local pelo Tray.");
        DialogResult answer = MessageBox.Show(
            "O NightOwl Agent vai verificar atualizacoes e pode reiniciar o servico durante o processo.\n\nDeseja continuar?",
            "NightOwl Agent - Atualizar agente",
            MessageBoxButtons.YesNo,
            MessageBoxIcon.Information);
        if (answer != DialogResult.Yes)
        {
            return;
        }

        try
        {
            string updater = Path.Combine(AppContext.BaseDirectory, "NightOwl.Agent.Updater.exe");
            if (!File.Exists(updater))
            {
                TrayLog.Write("tray.error", "Updater nao encontrado para atualizacao local.", new { updater });
                MessageBox.Show("NightOwl.Agent.Updater.exe nao foi encontrado neste endpoint.", "NightOwl Agent", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }

            MessageBox.Show(
                "Verificando atualizacao...\n\nA janela do Windows pode solicitar permissao de administrador. O progresso detalhado sera registrado em agent-updater.jsonl.",
                "NightOwl Agent",
                MessageBoxButtons.OK,
                MessageBoxIcon.Information);

            Process.Start(new ProcessStartInfo
            {
                FileName = updater,
                Arguments = "update --source tray --interactive",
                WorkingDirectory = AppContext.BaseDirectory,
                UseShellExecute = true,
                Verb = "runas"
            });
            TrayLog.Write("tray.menu.update_agent.started", "Updater iniciado pelo Tray com UAC.", new { updater });
        }
        catch (Win32Exception ex)
        {
            TrayLog.Write("tray.menu.update_agent.cancelled", "Atualizacao local cancelada ou sem permissao.", new { error = ex.Message });
            MessageBox.Show("Atualizacao cancelada ou sem permissao administrativa.", "NightOwl Agent", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        }
        catch (Exception ex)
        {
            TrayLog.Write("tray.error", "Falha ao iniciar atualizacao local.", new { error = ex.Message });
            MessageBox.Show("Nao foi possivel iniciar a atualizacao: " + ex.Message, "NightOwl Agent", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        }
    }

    private void RestartAgentService()
    {
        TrayLog.Write("tray.menu.restart_agent", "Usuario acionou reinicio do servico.");
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
            MessageBox.Show("Servico NightOwl reiniciado.", "NightOwl Agent", MessageBoxButtons.OK, MessageBoxIcon.Information);
        }
        catch (Win32Exception)
        {
            MessageBox.Show("Nao foi possivel reiniciar o servico. Execute a bandeja ou a acao como administrador.", "NightOwl Agent", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        }
        catch (UnauthorizedAccessException)
        {
            MessageBox.Show("Permissao insuficiente para reiniciar o servico. Execute como administrador.", "NightOwl Agent", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        }
        catch (Exception ex)
        {
            TrayLog.Write("tray.error", "Falha ao reiniciar o servico.", new { error = ex.Message });
            MessageBox.Show("Falha ao reiniciar o servico: " + ex.Message, "NightOwl Agent", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        }
    }

    private void ShowAbout()
    {
        MessageBox.Show(
            $"NightOwl Agent Tray\n\nVersao do agente: {Safe(_state.AgentVersion)}\nInstalacao: {_state.InstallPath}\nServidor: {_state.ServerBaseUrl}",
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

    private static string FormatDate(DateTimeOffset? value)
    {
        if (value is null)
        {
            return "nao encontrado";
        }

        return value.Value.ToLocalTime().ToString("dd/MM/yyyy HH:mm:ss");
    }

    private static string FormatServer(string value)
    {
        if (Uri.TryCreate(value, UriKind.Absolute, out Uri? uri))
        {
            return uri.Host;
        }

        return string.IsNullOrWhiteSpace(value) ? "-" : value;
    }

    private static string Safe(string value) => string.IsNullOrWhiteSpace(value) ? "-" : value;

    private static string TrimTooltip(string value)
    {
        return value.Length <= 63 ? value : value[..60] + "...";
    }

    [System.Runtime.InteropServices.DllImport("user32.dll", SetLastError = true)]
    private static extern bool DestroyIcon(IntPtr hIcon);
}
