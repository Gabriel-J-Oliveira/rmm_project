using NightOwl.Agent.Windows.Models;

namespace NightOwl.Agent.Windows.Collectors;

internal readonly record struct InventoryPlan(bool Basic, bool Detailed, bool Volumes, bool Network, bool Ancillary = false)
{
    internal bool Any => Basic || Detailed || Volumes || Network || Ancillary;
    internal static InventoryPlan Due(AgentState state, DateTimeOffset now, int? legacyCollectSeconds = null) => new(
        DueAt(state.LastBasicInventoryAt, now, TimeSpan.FromDays(1)),
        DueAt(state.LastDetailedHardwareAt, now, TimeSpan.FromDays(7)),
        DueAt(state.LastVolumesAt, now, TimeSpan.FromHours(1)),
        DueAt(state.LastNetworkInventoryAt, now, TimeSpan.FromDays(1)),
        legacyCollectSeconds is int interval && DueAt(state.LastAncillaryInventoryAt, now, TimeSpan.FromSeconds(Math.Max(5, interval))));

    private static bool DueAt(DateTimeOffset? last, DateTimeOffset now, TimeSpan interval) =>
        last is null || last > now || now - last >= interval;
}
