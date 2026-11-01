using Microsoft.CommandPalette.Extensions;
using Microsoft.CommandPalette.Extensions.Toolkit;

namespace HermesCmdPalExtension;

/// <summary>
/// Fallback item shown while typing in the palette's top-level search:
/// "Give task to HERMES: &lt;typed text&gt;" - Enter queues the card in triage.
/// Pattern from the official PowerToys ext (mutates the item in UpdateQuery).
/// </summary>
internal sealed partial class HermesFallbackItem : FallbackCommandItem
{
    private readonly QueueTaskCommand _command;
    private readonly string _subtitle;

    public HermesFallbackItem(string displayTitle, string subtitle)
        : base(new QueueTaskCommand(string.Empty), displayTitle, "hermes.task")
    {
        _command = (QueueTaskCommand)Command;
        _subtitle = subtitle;
        Title = string.Empty;   // hidden until the user types something
        Subtitle = string.Empty;
        FallbackHandler = this; // route UpdateQuery here
    }

    public override void UpdateQuery(string query)
    {
        var text = query?.Trim() ?? string.Empty;
        _command.TaskText = text;
        Title = text.Length > 0 ? $"Give task to HERMES: {text}" : string.Empty;
        Subtitle = text.Length > 0 ? _subtitle : string.Empty;
    }
}
