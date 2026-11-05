using System.Runtime.InteropServices;
using System.ServiceProcess;

namespace NightOwl.Agent.Windows.Collectors;

internal static class WindowsServicesCollector
{
    internal static object Collect(string[] names, int limit, bool details)
    {
        var services=ServiceController.GetServices();
        List<object> rows=new();
        bool truncated=false;
        try
        {
            foreach(var service in services.OrderBy(item=>item.ServiceName,StringComparer.OrdinalIgnoreCase))
            {
                if(names.Length>0 && !names.Contains(service.ServiceName,StringComparer.OrdinalIgnoreCase)) continue;
                if(rows.Count==limit) { truncated=true; break; }
                try
                {
                    var config=details ? Configuration(service.ServiceName) : (null,null);
                    rows.Add(new { name=service.ServiceName, display_name=service.DisplayName,
                        status=service.Status.ToString(), startup_type=service.StartType.ToString(),
                        account=config.Item1, executable=config.Item2, configuration_status=details && config.Item2 is null ? "unknown" : "available" });
                }
                catch(Exception) { rows.Add(new { name=service.ServiceName, status="unknown", error_code="SERVICE_READ_DENIED" }); }
            }
        }
        finally { foreach(var service in services) service.Dispose(); }
        return new {schema_version=1,services=rows,output_truncated=truncated,read_only=true};
    }
    private static (string?,string?) Configuration(string name)
    {
        nint scm=OpenSCManager(null,null,1);
        if(scm==0) return (null,null);
        nint handle=0,buffer=0;
        try
        {
            handle=OpenService(scm,name,1);
            if(handle==0) return (null,null);
            QueryServiceConfig(handle,0,0,out uint needed);
            if(needed==0 || needed>32768) return (null,null);
            buffer=Marshal.AllocHGlobal((int)needed);
            if(!QueryServiceConfig(handle,buffer,needed,out _)) return (null,null);
            Config value=Marshal.PtrToStructure<Config>(buffer);
            string path=Marshal.PtrToStringUni(value.BinaryPath) ?? "";
            // Never expose arbitrary service command-line arguments (they may contain secrets).
            string? executable=path.StartsWith('"') ? path.Split('"').ElementAtOrDefault(1) :
                path.EndsWith(".exe",StringComparison.OrdinalIgnoreCase) && !path.Contains(" -") ? path : null;
            return (Marshal.PtrToStringUni(value.Account),executable);
        }
        finally { if(buffer!=0) Marshal.FreeHGlobal(buffer); if(handle!=0) CloseServiceHandle(handle); CloseServiceHandle(scm); }
    }
    [StructLayout(LayoutKind.Sequential)] private struct Config
    { public uint Type,Start,Error; public nint BinaryPath,Group; public uint Tag; public nint Dependencies,Account,Display; }
    [DllImport("advapi32.dll",CharSet=CharSet.Unicode)] private static extern nint OpenSCManager(string? machine,string? database,uint access);
    [DllImport("advapi32.dll",CharSet=CharSet.Unicode)] private static extern nint OpenService(nint manager,string name,uint access);
    [DllImport("advapi32.dll",CharSet=CharSet.Unicode,EntryPoint="QueryServiceConfigW")] [return:MarshalAs(UnmanagedType.Bool)] private static extern bool QueryServiceConfig(nint service,nint config,uint bytes,out uint needed);
    [DllImport("advapi32.dll")] [return:MarshalAs(UnmanagedType.Bool)] private static extern bool CloseServiceHandle(nint handle);
}
