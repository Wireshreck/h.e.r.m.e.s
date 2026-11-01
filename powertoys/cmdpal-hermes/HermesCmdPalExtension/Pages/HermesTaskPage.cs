// The integrated task page: type anywhere on the page, press Enter on the
// "Give task to HERMES: ..." item to queue it with the board's decomposer.

using System;
using System.Collections.Generic;
using Microsoft.CommandPalette.Extensions;
using Microsoft.CommandPalette.Extensions.Toolkit;

namespace HermesCmdPalExtension.Pages;

internal sealed partial class HermesTaskPage : DynamicListPage
{
    private string _search = string.Empty;

    public HermesTaskPage()
    {
        Icon = IconHelpers.FromRelativePath("Assets\\StoreLogo.png");
        Title = "H.E.R.M.E.S";
        Name = "Open";
    }

    public override void UpdateSearchText(string oldSearch, string newSearch)
    {
        _search = newSearch ?? string.Empty;
        RaiseItemsChanged();
    }

    public override IListItem[] GetItems()
    {
        var text = _search.Trim();
        var items = new List<IListItem>();

        if (text.Length > 0)
        {
            items.Add(new ListItem(new QueueTaskCommand(text))
            {
                Title = $"Give task to HERMES: {text}",
                Subtitle = "Queue a kanban card in triage - the dispatcher picks the right specialist bot",
                Icon = new IconInfo("\uE945"),
            });
        }
        else
        {
            items.Add(new ListItem(new NoOpCommand())
            {
                Title = "Type a task, then press Enter to give it to HERMES",
                Subtitle = "e.g. 'research competitor pricing and write a brief' - it lands in triage and the fleet self-assigns",
            });
        }

        items.Add(new ListItem(new OpenUrlCommand("http://localhost:9119/"))
        {
            Title = "Open the Kanban dashboard",
            Subtitle = "localhost:9119 - watch the fleet work in the browser",
            Icon = new IconInfo("\uE774"),
        });
        items.Add(new ListItem(new ShowStatusCommand())
        {
            Title = "Board status",
            Subtitle = "Run 'hermes kanban stats' and show the result",
            Icon = new IconInfo("\uE9D2"),
        });
        items.Add(new ListItem(new OpenWorkspaceCommand())
        {
            Title = "Open the fleet workspace folder",
            Subtitle = HermesCli.FleetDir,
            Icon = new IconInfo("\uE838"),
        });
        items.Add(new ListItem(new OpenUrlCommand("https://github.com/Wireshreck/h.e.r.m.e.s"))
        {
            Title = "H.E.R.M.E.S on GitHub",
            Subtitle = "Fleet roster, souls, and bootstrap scripts",
            Icon = new IconInfo("\uE8A5"),
        });

        return [.. items];
    }
}
