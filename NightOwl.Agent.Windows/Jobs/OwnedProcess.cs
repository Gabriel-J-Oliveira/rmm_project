using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;

namespace NightOwl.Agent.Windows.Jobs;

internal static class OwnedProcess
{
    // Suspended creation closes the race between child creation and job assignment.
    internal static async Task<int> RunAsync(string executable, string arguments, CancellationToken ct)
    {
        nint job=CreateJobObject(0,null);
        if(job==0) throw new Win32Exception(Marshal.GetLastWin32Error());
        ProcessInfo process=default;
        try
        {
            ExtendedLimits limits=new() { Basic=new BasicLimits { LimitFlags=0x2000 } };
            if(!SetInformationJobObject(job,9,ref limits,(uint)Marshal.SizeOf<ExtendedLimits>())) throw new Win32Exception();
            Startup startup=new() { Size=Marshal.SizeOf<Startup>() };
            if(!CreateProcess(executable,new StringBuilder('"'+executable+'"'+' '+arguments),0,0,false,0x08000004,0,null,ref startup,out process))
                throw new Win32Exception(Marshal.GetLastWin32Error());
            if(!AssignProcessToJobObject(job,process.Process)) { TerminateProcess(process.Process,1); throw new Win32Exception(); }
            if(ResumeThread(process.Thread)==uint.MaxValue) throw new Win32Exception();
            bool cancelled=false;
            while(WaitForSingleObject(process.Process,0)==0x102)
            {
                if(ct.IsCancellationRequested && !cancelled) { TerminateJobObject(job,1); cancelled=true; }
                await Task.Delay(25,CancellationToken.None);
            }
            // A parent exit is not proof its children exited. Terminate remaining owned children.
            if(!QueryInformationJobObject(job,1,out Accounting info,(uint)Marshal.SizeOf<Accounting>(),0)) throw new Win32Exception();
            if(info.ActiveProcesses>0) TerminateJobObject(job,1);
            do
            {
                if(!QueryInformationJobObject(job,1,out info,(uint)Marshal.SizeOf<Accounting>(),0)) throw new Win32Exception();
                if(info.ActiveProcesses>0) await Task.Delay(25,CancellationToken.None);
            } while(info.ActiveProcesses>0);
            ct.ThrowIfCancellationRequested();
            if(!GetExitCodeProcess(process.Process,out uint code)) throw new Win32Exception();
            return checked((int)code);
        }
        finally
        {
            TerminateJobObject(job,1);
            if(process.Thread!=0) CloseHandle(process.Thread);
            if(process.Process!=0) { WaitForSingleObject(process.Process,5000); CloseHandle(process.Process); }
            CloseHandle(job);
        }
    }
    [StructLayout(LayoutKind.Sequential)] private struct Startup
    { public int Size; public nint Reserved,Desktop,Title; public uint X,Y,XSize,YSize,XChars,YChars,Fill,Flags; public ushort Show,ReservedBytes; public nint ReservedPointer,Input,Output,Error; }
    [StructLayout(LayoutKind.Sequential)] private struct ProcessInfo { public nint Process,Thread; public uint ProcessId,ThreadId; }
    [StructLayout(LayoutKind.Sequential)] private struct BasicLimits
    { public long ProcessTime,JobTime; public uint LimitFlags; public nuint MinWorking,MaxWorking; public uint ActiveLimit; public nuint Affinity; public uint Priority,Scheduling; }
    [StructLayout(LayoutKind.Sequential)] private struct IoCounters { public ulong A,B,C,D,E,F; }
    [StructLayout(LayoutKind.Sequential)] private struct ExtendedLimits { public BasicLimits Basic; public IoCounters Io; public nuint ProcessMemory,JobMemory,PeakProcess,PeakJob; }
    [StructLayout(LayoutKind.Sequential)] private struct Accounting { public long A,B,C,D; public uint Faults,TotalProcesses,ActiveProcesses,TerminatedProcesses; }
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] private static extern nint CreateJobObject(nint attributes,string? name);
    [DllImport("kernel32.dll",SetLastError=true)] [return:MarshalAs(UnmanagedType.Bool)] private static extern bool SetInformationJobObject(nint job,int info,ref ExtendedLimits limits,uint size);
    [DllImport("kernel32.dll",SetLastError=true)] [return:MarshalAs(UnmanagedType.Bool)] private static extern bool QueryInformationJobObject(nint job,int info,out Accounting data,uint size,nint returned);
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,EntryPoint="CreateProcessW",SetLastError=true)] [return:MarshalAs(UnmanagedType.Bool)] private static extern bool CreateProcess(string application,StringBuilder command,nint pa,nint ta,bool inherit,uint flags,nint environment,string? directory,ref Startup startup,out ProcessInfo process);
    [DllImport("kernel32.dll")] [return:MarshalAs(UnmanagedType.Bool)] private static extern bool AssignProcessToJobObject(nint job,nint process);
    [DllImport("kernel32.dll")] private static extern uint ResumeThread(nint thread);
    [DllImport("kernel32.dll")] private static extern uint WaitForSingleObject(nint handle,uint milliseconds);
    [DllImport("kernel32.dll")] [return:MarshalAs(UnmanagedType.Bool)] private static extern bool GetExitCodeProcess(nint process,out uint code);
    [DllImport("kernel32.dll")] [return:MarshalAs(UnmanagedType.Bool)] private static extern bool TerminateJobObject(nint job,uint code);
    [DllImport("kernel32.dll")] [return:MarshalAs(UnmanagedType.Bool)] private static extern bool TerminateProcess(nint process,uint code);
    [DllImport("kernel32.dll")] [return:MarshalAs(UnmanagedType.Bool)] private static extern bool CloseHandle(nint handle);
}
