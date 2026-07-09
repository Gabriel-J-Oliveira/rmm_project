using NightOwl.Agent.Windows;
using NightOwl.Agent.Windows.Collectors;
using NightOwl.Agent.Windows.Jobs;
using NightOwl.Agent.Windows.Services;
using Microsoft.Extensions.Hosting.WindowsServices;

HostApplicationBuilder builder = Host.CreateApplicationBuilder(args);

builder.Services.AddWindowsService(options =>
{
    options.ServiceName = "NightOwlAgentDotNet";
});

builder.Services.AddSingleton<ConfigService>();
builder.Services.AddSingleton<StateService>();
builder.Services.AddSingleton<JsonlLogger>();
builder.Services.AddSingleton<AgentApiClient>();
builder.Services.AddSingleton<WindowsInventoryCollector>();
builder.Services.AddSingleton<JobExecutor>();
builder.Services.AddHostedService<Worker>();
builder.Services.AddHttpClient();

IHost host = builder.Build();
host.Run();
