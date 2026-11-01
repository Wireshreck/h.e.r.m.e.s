using System.Diagnostics;
using Microsoft.CommandPalette.Extensions;
using Microsoft.CommandPalette.Extensions.Toolkit;

namespace HermesCmdPalExtension;

/// <summary>Queue the typed text as a kanban card in triage (for the task page).</summary>
internal sealed partial class QueueTaskCommand : InvokableCommand
{
    public QueueTaskCommand(string task) => TaskText = task;

    /// <summary>The current task text (the fallback item re-points this on every keystroke).</summary>
    public string TaskText { get; set; } = string.Empty;

    public override string Name => "Give to HERMES";

    public override IconInfo Icon => new("\uE945");

    public override CommandResult Invoke() => CommandResult.ShowToast(HermesCli.QueueTask(TaskText));
}

/// <summary>Run `hermes kanban stats` and toast the summary.</summary>
internal sealed partial class ShowStatusCommand : InvokableCommand
{
    public override string Name => "Board status";

    public override IconInfo Icon => new("\uE9D2");

    public override CommandResult Invoke() => CommandResult.ShowToast(HermesCli.BoardStatus());
}

/// <summary>Open the fleet workspace folder in Explorer.</summary>
internal sealed partial class OpenWorkspaceCommand : InvokableCommand
{
    public override string Name => "Open workspace";

    public override IconInfo Icon => new("\uE838");

    public override CommandResult Invoke()
    {
        Process.Start(new ProcessStartInfo
        {
            FileName = "explorer.exe",
            Arguments = $"\"{HermesCli.FleetDir}\"",
            UseShellExecute = true,
        });
        return CommandResult.Dismiss();
    }
}
