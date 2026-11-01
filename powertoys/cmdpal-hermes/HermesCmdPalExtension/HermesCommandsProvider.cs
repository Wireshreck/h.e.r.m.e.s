// Command provider for the H.E.R.M.E.S extension.
// Top-level: one command that opens the task page. Fallback: typing anything
// in the palette offers "Give task to HERMES: <typed text>".

using Microsoft.CommandPalette.Extensions;
using Microsoft.CommandPalette.Extensions.Toolkit;

namespace HermesCmdPalExtension;

public partial class HermesCommandsProvider : CommandProvider
{
    private readonly Pages.HermesTaskPage _page = new();
    private readonly HermesFallbackItem _fallbackItem;

    public HermesCommandsProvider()
    {
        DisplayName = "H.E.R.M.E.S";
        Icon = IconHelpers.FromRelativePath("Assets\\StoreLogo.png");
        _fallbackItem = new HermesFallbackItem("Give task to HERMES:", "Queue the typed text as a kanban card in triage - the dispatcher picks the right specialist bot");
    }

    public override ICommandItem[] TopLevelCommands() =>
    [
        new CommandItem(_page)
        {
            Title = "H.E.R.M.E.S",
            Subtitle = "Tell the fleet what to do - it routes to the right specialist bots",
        },
    ];

    public override IFallbackCommandItem[] FallbackCommands() => [_fallbackItem];
}
