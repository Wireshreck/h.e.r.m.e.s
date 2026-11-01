// H.E.R.M.E.S extension root: implements IExtension.
// The [Guid] must match the CLSID in Package.appxmanifest (com:Class Id and
// CreateInstance ClassId) — currently 19266C9C-8FD4-4F92-A430-9244631734D3.

using System;
using System.Runtime.InteropServices;
using System.Threading;
using Microsoft.CommandPalette.Extensions;

namespace HermesCmdPalExtension;

[Guid("19266C9C-8FD4-4F92-A430-9244631734D3")]
public sealed partial class HermesExtension : IExtension, IDisposable
{
    private readonly ManualResetEvent _extensionDisposedEvent;

    private readonly HermesCommandsProvider _provider = new();

    public HermesExtension(ManualResetEvent extensionDisposedEvent)
    {
        _extensionDisposedEvent = extensionDisposedEvent;
    }

    public object? GetProvider(ProviderType providerType)
    {
        return providerType switch
        {
            ProviderType.Commands => _provider,
            _ => null,
        };
    }

    public void Dispose() => _extensionDisposedEvent.Set();
}
