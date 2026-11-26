using System.Runtime.InteropServices;
using System.ServiceProcess;
using System.Globalization;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace NightOwl.Agent.Windows.Collectors;

internal static class WindowsServicesCollector
{
    private const int MaxServices = 800;
    private const int PageSize = 20;
    private const int PageSizeV3 = 100;
    private const int MinimumPageBytesV3 = 8192;
    private const int MaximumPageBytesV3 = 60 * 1024;
    private const int MaxSnapshots = 4;
    private static readonly TimeSpan SnapshotLifetime = TimeSpan.FromMinutes(10);
    private static readonly object SnapshotLock = new();
    private static readonly Dictionary<string, Snapshot> Snapshots = new(StringComparer.Ordinal);

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

    internal static WindowsServicePage CollectPaged(string afterName, string expectedSnapshotId, int pageSize)
    {
        if (pageSize != PageSize || afterName.Length > 256 || expectedSnapshotId.Length > 64 ||
            string.IsNullOrEmpty(afterName) != string.IsNullOrEmpty(expectedSnapshotId))
            throw new InvalidOperationException("SERVICE_PAGE_INVALID");

        lock (SnapshotLock)
        {
            DateTimeOffset now = DateTimeOffset.UtcNow;
            foreach (string expired in Snapshots.Where(item => now - item.Value.CapturedAt > SnapshotLifetime).Select(item => item.Key).ToArray())
                Snapshots.Remove(expired);

            string activeSnapshotId = expectedSnapshotId;
            if (string.IsNullOrEmpty(expectedSnapshotId))
            {
                IReadOnlyList<WindowsServiceInfo> captured = CaptureSafeRows();
                if (captured.Count > MaxServices)
                    throw new InvalidOperationException("SERVICE_LIMIT_EXCEEDED");
                while (Snapshots.Count >= MaxSnapshots)
                {
                    string oldest = Snapshots.MinBy(item => item.Value.CapturedAt).Key;
                    Snapshots.Remove(oldest);
                }
                string snapshotId = Guid.NewGuid().ToString("N");
                Snapshots[snapshotId] = new Snapshot(snapshotId, now, captured);
                activeSnapshotId = snapshotId;
            }
            if (!string.IsNullOrEmpty(expectedSnapshotId) && !Snapshots.ContainsKey(expectedSnapshotId))
            {
                throw new InvalidOperationException("SERVICE_SNAPSHOT_EXPIRED");
            }

            Snapshot snapshot = Snapshots[activeSnapshotId];
            int start = 0;
            if (!string.IsNullOrEmpty(afterName))
            {
                int previous = -1;
                for (int index = 0; index < snapshot.Rows.Count; index++)
                {
                    if (string.Equals(snapshot.Rows[index].Name, afterName, StringComparison.OrdinalIgnoreCase))
                    {
                        previous = index;
                        break;
                    }
                }
                if (previous < 0) throw new InvalidOperationException("SERVICE_SNAPSHOT_EXPIRED");
                start = previous + 1;
            }

            return BuildPage(snapshot.Rows, snapshot.Id, snapshot.CapturedAt, start, pageSize);
        }
    }

    internal static WindowsServicePage BuildPage(IReadOnlyList<WindowsServiceInfo> rows, string snapshotId,
        DateTimeOffset collectedAt, int start, int pageSize)
    {
        if (pageSize != PageSize || start < 0 || start > rows.Count || rows.Count > MaxServices)
            throw new InvalidOperationException("SERVICE_PAGE_INVALID");
        WindowsServiceInfo[] page = rows.Skip(start).Take(pageSize).ToArray();
        bool hasMore = start + page.Length < rows.Count;
        return new WindowsServicePage(2, true, snapshotId, collectedAt, rows.Count, page,
            hasMore ? page.LastOrDefault()?.Name : null, hasMore);
    }

    internal static WindowsServicePageV3 BuildPageV3(IReadOnlyList<WindowsServiceInfo> rows, string snapshotId,
        DateTimeOffset collectedAt, int start, int requestedPageSize, int maxPageBytes, int pageIndex, int rowsCollected)
    {
        if (requestedPageSize != PageSizeV3 || maxPageBytes is < MinimumPageBytesV3 or > MaximumPageBytesV3 ||
            start < 0 || start > rows.Count || rows.Count > MaxServices || pageIndex is < 1 or > MaxServices ||
            rowsCollected != start || rowsCollected < 0 || rowsCollected > MaxServices)
            throw new InvalidOperationException("SERVICE_PAGE_INVALID");

        int count = Math.Min(requestedPageSize, rows.Count - start);
        while (count >= 0)
        {
            WindowsServiceInfo[] page = rows.Skip(start).Take(count).ToArray();
            bool hasMore = start + page.Length < rows.Count;
            var result = new WindowsServicePageV3(3, true, snapshotId, collectedAt, rows.Count, page,
                hasMore ? page[^1].Name : null, hasMore, pageIndex, rowsCollected + page.Length, requestedPageSize);
            int serializedBytes = JsonSerializer.SerializeToUtf8Bytes(result,
                new JsonSerializerOptions(JsonSerializerDefaults.Web)).Length;
            if (serializedBytes <= maxPageBytes)
            {
                if (hasMore && page.Length == 0) throw new InvalidOperationException("SERVICE_PAGE_INVALID");
                return result;
            }
            if (count <= 1) throw new InvalidOperationException("SERVICE_ROW_TOO_LARGE");
            count--;
        }
        throw new InvalidOperationException("SERVICE_ROW_TOO_LARGE");
    }

    internal static WindowsServicePageV3 CollectPagedV3(string afterName, string expectedSnapshotId,
        int pageSize, int maxPageBytes, int pageIndex, int rowsCollected, int expectedTotalServices,
        string expectedCollectedAt)
    {
        if (pageSize != PageSizeV3 || maxPageBytes is < MinimumPageBytesV3 or > MaximumPageBytesV3 ||
            afterName.Length > 256 || expectedSnapshotId.Length > 64 || expectedCollectedAt.Length > 40 ||
            string.IsNullOrEmpty(afterName) != string.IsNullOrEmpty(expectedSnapshotId) ||
            pageIndex is < 1 or > MaxServices || rowsCollected is < 0 or >= MaxServices ||
            expectedTotalServices is < 0 or > MaxServices ||
            (pageIndex == 1 && (rowsCollected != 0 || expectedTotalServices != 0 || expectedCollectedAt.Length != 0)) ||
            (pageIndex > 1 && (expectedTotalServices < 1 || rowsCollected >= expectedTotalServices || expectedCollectedAt.Length == 0)))
            throw new InvalidOperationException("SERVICE_PAGE_INVALID");

        lock (SnapshotLock)
        {
            DateTimeOffset now = DateTimeOffset.UtcNow;
            foreach (string expired in Snapshots.Where(item => now - item.Value.CapturedAt > SnapshotLifetime).Select(item => item.Key).ToArray())
                Snapshots.Remove(expired);

            string activeSnapshotId = expectedSnapshotId;
            if (string.IsNullOrEmpty(expectedSnapshotId))
            {
                IReadOnlyList<WindowsServiceInfo> captured = CaptureSafeRows(ordinalOrder: true);
                if (captured.Count > MaxServices) throw new InvalidOperationException("SERVICE_LIMIT_EXCEEDED");
                while (Snapshots.Count >= MaxSnapshots)
                {
                    string oldest = Snapshots.MinBy(item => item.Value.CapturedAt).Key;
                    Snapshots.Remove(oldest);
                }
                activeSnapshotId = Guid.NewGuid().ToString("N");
                Snapshots[activeSnapshotId] = new Snapshot(activeSnapshotId, now, captured);
            }
            if (!Snapshots.TryGetValue(activeSnapshotId, out Snapshot? snapshot))
                throw new InvalidOperationException("SERVICE_SNAPSHOT_EXPIRED");

            int start = 0;
            if (!string.IsNullOrEmpty(afterName))
            {
                int previous = -1;
                for (int index = 0; index < snapshot.Rows.Count; index++)
                {
                    if (string.Equals(snapshot.Rows[index].Name, afterName, StringComparison.Ordinal))
                    {
                        previous = index;
                        break;
                    }
                }
                if (previous < 0) throw new InvalidOperationException("SERVICE_SNAPSHOT_EXPIRED");
                start = previous + 1;
            }
            if (rowsCollected != start || (expectedTotalServices != 0 && expectedTotalServices != snapshot.Rows.Count) ||
                (expectedCollectedAt.Length > 0 && (!DateTimeOffset.TryParse(expectedCollectedAt, CultureInfo.InvariantCulture,
                    DateTimeStyles.RoundtripKind, out DateTimeOffset expectedAt) ||
                    expectedAt != snapshot.CapturedAt)))
                throw new InvalidOperationException("SERVICE_SNAPSHOT_EXPIRED");

            return BuildPageV3(snapshot.Rows, snapshot.Id, snapshot.CapturedAt, start, pageSize, maxPageBytes,
                pageIndex, rowsCollected);
        }
    }

    private static IReadOnlyList<WindowsServiceInfo> CaptureSafeRows(bool ordinalOrder = false)
    {
        ServiceController[] services = ServiceController.GetServices();
        if (services.Length > MaxServices)
        {
            foreach (ServiceController service in services) service.Dispose();
            throw new InvalidOperationException("SERVICE_LIMIT_EXCEEDED");
        }
        List<WindowsServiceInfo> rows = new(services.Length);
        try
        {
            IOrderedEnumerable<ServiceController> ordered = ordinalOrder
                ? services.OrderBy(item => item.ServiceName, StringComparer.Ordinal)
                : services.OrderBy(item => item.ServiceName, StringComparer.OrdinalIgnoreCase);
            foreach (ServiceController service in ordered)
            {
                string status = "unknown";
                string startup = "unknown";
                string display = service.DisplayName ?? "";
                try { status = service.Status.ToString(); } catch (Exception) { }
                try { startup = service.StartType.ToString(); } catch (Exception) { }
                rows.Add(new WindowsServiceInfo(service.ServiceName, display, status, startup));
            }
        }
        finally { foreach (ServiceController service in services) service.Dispose(); }
        return rows;
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

    private sealed record Snapshot(string Id, DateTimeOffset CapturedAt, IReadOnlyList<WindowsServiceInfo> Rows);
}

internal sealed record WindowsServiceInfo(
    [property: JsonPropertyName("name")] string Name,
    [property: JsonPropertyName("display_name")] string DisplayName,
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("startup_type")] string StartupType);
internal sealed record WindowsServicePage(
    [property: JsonPropertyName("schema_version")] int SchemaVersion,
    [property: JsonPropertyName("read_only")] bool ReadOnly,
    [property: JsonPropertyName("snapshot_id")] string SnapshotId,
    [property: JsonPropertyName("collected_at")] DateTimeOffset CollectedAt,
    [property: JsonPropertyName("total_services")] int TotalServices,
    [property: JsonPropertyName("services")] IReadOnlyList<WindowsServiceInfo> Services,
    [property: JsonPropertyName("next_after_name")] string? NextAfterName,
    [property: JsonPropertyName("has_more")] bool HasMore);
internal sealed record WindowsServicePageV3(
    [property: JsonPropertyName("schema_version")] int SchemaVersion,
    [property: JsonPropertyName("read_only")] bool ReadOnly,
    [property: JsonPropertyName("snapshot_id")] string SnapshotId,
    [property: JsonPropertyName("collected_at")] DateTimeOffset CollectedAt,
    [property: JsonPropertyName("total_services")] int TotalServices,
    [property: JsonPropertyName("services")] IReadOnlyList<WindowsServiceInfo> Services,
    [property: JsonPropertyName("next_after_name")] string? NextAfterName,
    [property: JsonPropertyName("has_more")] bool HasMore,
    [property: JsonPropertyName("page_index")] int PageIndex,
    [property: JsonPropertyName("rows_collected")] int RowsCollected,
    [property: JsonPropertyName("page_size")] int PageSize);
