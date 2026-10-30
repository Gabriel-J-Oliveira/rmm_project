using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text.Json;
using NightOwl.Agent.Shared;
using NightOwl.Agent.Windows.Models;

namespace NightOwl.Agent.Windows.Jobs;

internal sealed record ApprovedSoftware(string Id,string Url,string Sha256,long Size,string ProductCode,string Version,string SignerSha256);

internal sealed class SoftwareInstallation
{
    private readonly Func<ApprovedSoftware,CancellationToken,Task<byte[]>> _download;
    private readonly Func<string,string,bool> _authentic;
    private readonly Func<string,string,bool> _installed;
    private readonly Func<string,string,CancellationToken,Task<int>> _run;
    internal SoftwareInstallation(Func<ApprovedSoftware,CancellationToken,Task<byte[]>>? download=null,
        Func<string,string,bool>? authentic=null,Func<string,string,bool>? installed=null,
        Func<string,string,CancellationToken,Task<int>>? run=null)
    {
        _download=download ?? Download; _authentic=authentic ?? Authenticode;
        _installed=installed ?? Installed; _run=run ?? OwnedProcess.RunAsync;
    }
    internal async Task<object> InstallAsync(AgentConfig config,string packageId,CancellationToken ct)
    {
        if(!config.SoftwareInstallEnabled) throw new InvalidOperationException("SOFTWARE_INSTALL_DISABLED");
        string root=Path.GetDirectoryName(config.StatePath)!;
        string catalog=Path.Combine(root,"software-catalog.json");
        byte[] bytes=await File.ReadAllBytesAsync(catalog,ct);
        if(bytes.Length>256*1024 || !Hash(bytes,config.SoftwareCatalogSha256)) throw new InvalidDataException("SOFTWARE_CATALOG_UNTRUSTED");
        ApprovedSoftware[] entries=JsonSerializer.Deserialize<ApprovedSoftware[]>(bytes,new JsonSerializerOptions(JsonSerializerDefaults.Web)) ?? Array.Empty<ApprovedSoftware>();
        ApprovedSoftware[] matches=entries.Where(item=>item.Id==packageId).ToArray();
        if(matches.Length!=1) throw new InvalidDataException("SOFTWARE_PACKAGE_NOT_APPROVED");
        ApprovedSoftware package=matches[0];
        if(!Uri.TryCreate(package.Url,UriKind.Absolute,out Uri? uri) || uri.Scheme!="https" || uri.UserInfo!="" || uri.Query!="" || uri.Fragment!="" ||
            !Guid.TryParse(package.ProductCode,out Guid product) || package.Size is < 1 or > 100*1024*1024 ||
            package.Sha256.Length!=64 || package.SignerSha256.Length!=64 || string.IsNullOrWhiteSpace(package.Version))
            throw new InvalidDataException("SOFTWARE_POLICY_INVALID");
        string productCode=product.ToString("B").ToUpperInvariant();
        string journal=Path.Combine(root,"software-install-"+product.ToString("N")+".json");
        if(_installed(productCode,package.Version)) return new {installed=true,verified=true,already_present=true,package_id=packageId,scope="machine"};
        if(File.Exists(journal)) throw new InvalidOperationException("SOFTWARE_PREVIOUS_OUTCOME_REQUIRES_REVIEW");
        string directory=Path.Combine(root,"software-staging",Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        string file=Path.Combine(directory,"package.msi");
        try
        {
            byte[] payload=await _download(package,ct);
            if(payload.LongLength!=package.Size || !Hash(payload,package.Sha256)) throw new InvalidDataException("SOFTWARE_HASH_INVALID");
            await NightOwlFileStore.WriteAllBytesAsync(file,payload,ct);
            if(!_authentic(file,package.SignerSha256)) throw new InvalidDataException("SOFTWARE_SIGNATURE_INVALID");
            // Local admin-owned catalog only. No arbitrary arguments, user context or secrets.
            NightOwlFileStore.WriteAllTextDurable(journal,JsonSerializer.Serialize(new {package_id=packageId,product_code=productCode,state="outcome_pending",started_at=DateTimeOffset.UtcNow}));
            int code=await _run(Path.Combine(Environment.SystemDirectory,"msiexec.exe"),"/i \""+file+"\" /qn /norestart ALLUSERS=1",ct);
            bool verified=(code==0 || code==3010) && _installed(productCode,package.Version);
            if(!verified) throw new InvalidOperationException("SOFTWARE_INSTALLATION_UNCONFIRMED");
            NightOwlFileStore.WriteAllTextDurable(journal,JsonSerializer.Serialize(new {package_id=packageId,product_code=productCode,state="verified",exit_code=code}));
            return new {installed=true,verified=true,package_id=packageId,exit_code=code,reboot_required=code==3010,scope="machine"};
        }
        finally { if(File.Exists(file)) File.Delete(file); if(Directory.Exists(directory)) Directory.Delete(directory); }
    }
    private static bool Hash(byte[] data,string expected) => expected.Length==64 &&
        CryptographicOperations.FixedTimeEquals(System.Text.Encoding.ASCII.GetBytes(Convert.ToHexString(SHA256.HashData(data))),System.Text.Encoding.ASCII.GetBytes(expected.ToUpperInvariant()));
    private static async Task<byte[]> Download(ApprovedSoftware package,CancellationToken ct)
    {
        using HttpClient client=new(new HttpClientHandler {AllowAutoRedirect=false});
        using HttpResponseMessage response=await client.GetAsync(package.Url,HttpCompletionOption.ResponseHeadersRead,ct);
        response.EnsureSuccessStatusCode();
        if((int)response.StatusCode!=200 || response.Content.Headers.ContentLength!=package.Size) throw new InvalidDataException("SOFTWARE_DOWNLOAD_SIZE_INVALID");
        using Stream source=await response.Content.ReadAsStreamAsync(ct);
        using MemoryStream buffer=new();
        byte[] chunk=new byte[65536];
        int count;
        while((count=await source.ReadAsync(chunk,ct))>0)
        { if(buffer.Length+count>package.Size) throw new InvalidDataException("SOFTWARE_DOWNLOAD_TOO_LARGE"); await buffer.WriteAsync(chunk.AsMemory(0,count),ct); }
        return buffer.ToArray();
    }
    private static bool Installed(string product,string version)
    {
        if(MsiQueryProductState(product)!=5) return false;
        uint length=0;
        MsiGetProductInfo(product,"VersionString",null,ref length);
        var result=new System.Text.StringBuilder((int)length+1);
        length++;
        return MsiGetProductInfo(product,"VersionString",result,ref length)==0 && result.ToString()==version;
    }
    private static bool Authenticode(string file,string signer)
    {
        nint pointer=Marshal.AllocHGlobal(Marshal.SizeOf<TrustFile>());
        TrustFile info=new() {Size=(uint)Marshal.SizeOf<TrustFile>(),Path=file};
        Marshal.StructureToPtr(info,pointer,false);
        TrustData data=new() {Size=(uint)Marshal.SizeOf<TrustData>(),Ui=2,Revocation=1,Choice=1,File=pointer,StateAction=1};
        Guid action=new("00AAC56B-CD44-11d0-8CC2-00C04FC295EE");
        try
        {
            if(WinVerifyTrust(new nint(-1),ref action,ref data)!=0) return false;
            using X509Certificate cert=X509Certificate.CreateFromSignedFile(file);
            return Hash(cert.GetRawCertData(),signer);
        }
        finally { data.StateAction=2; WinVerifyTrust(new nint(-1),ref action,ref data); Marshal.DestroyStructure<TrustFile>(pointer); Marshal.FreeHGlobal(pointer); }
    }
    [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)] private struct TrustFile {public uint Size; [MarshalAs(UnmanagedType.LPWStr)] public string Path; public nint Handle,KnownSubject;}
    [StructLayout(LayoutKind.Sequential)] private struct TrustData {public uint Size;public nint Policy,Sip;public uint Ui,Revocation,Choice;public nint File;public uint StateAction;public nint State,Url;public uint Flags,Context;public nint Signature;}
    [DllImport("wintrust.dll",ExactSpelling=true)] private static extern int WinVerifyTrust(nint window,ref Guid action,ref TrustData data);
    [DllImport("msi.dll",CharSet=CharSet.Unicode,EntryPoint="MsiQueryProductStateW")] private static extern int MsiQueryProductState(string product);
    [DllImport("msi.dll",CharSet=CharSet.Unicode,EntryPoint="MsiGetProductInfoW")] private static extern uint MsiGetProductInfo(string product,string property,System.Text.StringBuilder? value,ref uint length);
}
