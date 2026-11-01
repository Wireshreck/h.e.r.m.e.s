// Thin bridge from the palette to the `hermes` CLI.
// Only invoked from command handlers (never from GetItems, which runs often).

using System;
using System.Diagnostics;
using System.Text.RegularExpressions;

namespace HermesCmdPalExtension;

internal static class HermesCli
{
    // TODO: make this configurable for portable/shared installs.
    internal const string FleetDir = @"C:\Users\User\h.e.r.m.e.s";

    /// <summary>Queue a plain-language goal in triage; the dispatcher fans it out to specialists.</summary>
    internal static string QueueTask(string task)
    {
        var clean = (task ?? string.Empty).Trim().Replace('"', '\'');
        if (clean.Length == 0)
        {
            return "Type a task for the fleet first.";
        }

        var psi = new ProcessStartInfo
        {
            FileName = "cmd.exe",
            Arguments = $"/c hermes kanban create \"{clean}\" --triage --json",
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            WorkingDirectory = FleetDir,
        };

        try
        {
            using var p = Process.Start(psi);
            if (p is null)
            {
                return "Could not start the hermes CLI.";
            }

            var stdout = p.StandardOutput.ReadToEnd();
            var stderr = p.StandardError.ReadToEnd();
            if (!p.WaitForExit(30_000) && !p.HasExited)
            {
                p.Kill();
                return "hermes took too long queueing the task - check the board manually.";
            }

            var m = Regex.Match(stdout + stderr, @"""id""\s*:\s*""(t_[0-9a-f]+)""");
            return m.Success
                ? $"Queued card {m.Groups[1].Value} in triage - HERMES assigns the right specialist bots now."
                : $"Could not queue the task: {OneLine(stderr.Length > 0 ? stderr : stdout)}";
        }
        catch (Exception ex)
        {
            return $"Failed to run hermes: {ex.Message}";
        }
    }

    /// <summary>Compact one-line-ish board status for a toast.</summary>
    internal static string BoardStatus()
    {
        var psi = new ProcessStartInfo
        {
            FileName = "cmd.exe",
            Arguments = "/c hermes kanban stats",
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            WorkingDirectory = FleetDir,
        };

        try
        {
            using var p = Process.Start(psi);
            if (p is null)
            {
                return "Could not start the hermes CLI.";
            }

            var stdout = p.StandardOutput.ReadToEnd();
            var stderr = p.StandardError.ReadToEnd();
            if (!p.WaitForExit(30_000) && !p.HasExited)
            {
                p.Kill();
                return "hermes took too long reading board stats.";
            }

            var text = OneLine(stdout.Length > 0 ? stdout : stderr);
            return text.Length > 0 ? $"Kanban board: {text}" : "hermes returned no output - is the kanban board initialized?";
        }
        catch (Exception ex)
        {
            return $"Failed to run hermes: {ex.Message}";
        }
    }

    private static string OneLine(string s)
    {
        var joined = string.Join(" | ", s.Split('\n', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries));
        return joined.Length > 400 ? joined[..397] + "..." : joined.Trim();
    }
}
