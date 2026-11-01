// H.E.R.M.E.S Command Palette extension — COM server bootstrap.
// Pattern fixed by the Command Palette Extension SDK: do not rename the flag.

using Shmuelie.WinRTServer;
using Shmuelie.WinRTServer.CsWinRT;
using System;
using System.Threading;

namespace HermesCmdPalExtension;

public class Program
{
    [MTAThread]
    public static void Main(string[] args)
    {
        if (args.Length > 0 && args[0] == "-RegisterProcessAsComServer")
        {
            ComServer server = new();

            ManualResetEvent extensionDisposedEvent = new(false);

            // A single extension instance that is returned every time the host
            // asks for the IExtension object.
            HermesExtension extensionInstance = new(extensionDisposedEvent);
            server.RegisterClass<HermesExtension, Microsoft.CommandPalette.Extensions.IExtension>(() => extensionInstance);
            server.Start();

            // Exit as soon as the extension is disposed (signalled by the host).
            extensionDisposedEvent.WaitOne();
            server.Stop();
            server.UnsafeDispose();
        }
        else
        {
            Console.WriteLine("Not being launched as a Command Palette extension... exiting.");
        }
    }
}
