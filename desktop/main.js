// H.E.R.M.E.S. desktop shell. The window is only a view: every byte of state
// comes from backend.py, which talks to the real Hermes runtime and comms DB.
const { app, BrowserWindow, dialog } = require('electron');
const { spawn, spawnSync } = require('child_process');
const http = require('http');
const path = require('path');

const PORT = Number(process.env.HERMES_DESKTOP_PORT || 8787);
const HOST = '127.0.0.1';
const URL = `http://${HOST}:${PORT}/`;

let backend = null;
let win = null;
let shuttingDown = false;

function pythonCommand() {
  if (process.env.HERMES_DESKTOP_PYTHON) return { cmd: process.env.HERMES_DESKTOP_PYTHON, args: [] };
  for (const cmd of ['python', 'py']) {
    const probe = spawnSync(cmd, ['--version'], { windowsHide: true });
    if (probe.status === 0) return { cmd, args: cmd === 'py' ? ['-3'] : [] };
  }
  return null;
}

function startBackend() {
  const py = pythonCommand();
  if (!py) {
    dialog.showErrorBox('H.E.R.M.E.S', 'Python not found on PATH. Install Python 3 or set HERMES_DESKTOP_PYTHON.');
    app.quit();
    return false;
  }
  const backendPath = path.join(__dirname, 'backend.py');
  backend = spawn(py.cmd, [...py.args, backendPath, '--port', String(PORT)], {
    cwd: path.join(__dirname, '..'),
    windowsHide: true,
    stdio: ['ignore', 'ignore', 'pipe'],
    env: { ...process.env, HERMES_DESKTOP_PORT: String(PORT) },
  });
  let log = '';
  backend.stderr.on('data', chunk => {
    const text = chunk.toString();
    log = (log + text).slice(-4000);
    process.stderr.write(`[hermes-desktop] ${text}`);
  });
  backend.on('exit', (code) => {
    if (!shuttingDown && code !== 0) {
      dialog.showErrorBox('H.E.R.M.E.S backend stopped', log || `exit code ${code}`);
    }
  });
  return true;
}

function healthOnce() {
  return new Promise(resolve => {
    const req = http.get(`http://${HOST}:${PORT}/api/health`, res => {
      let body = '';
      res.on('data', c => { body += c; });
      res.on('end', () => { try { resolve(JSON.parse(body)); } catch { resolve(null); } });
    });
    req.on('error', () => resolve(null));
    req.setTimeout(1500, () => { req.destroy(); resolve(null); });
  });
}

async function waitForBackend() {
  for (let i = 0; i < 60; i++) {
    if (shuttingDown) return false;
    const health = await healthOnce();
    if (health && 'connected' in health) return true;
    if (backend && backend.exitCode !== null) return false;
    await new Promise(r => setTimeout(r, 250));
  }
  return false;
}

function createWindow() {
  win = new BrowserWindow({
    width: 1500, height: 940, minWidth: 980, minHeight: 640,
    show: false, backgroundColor: '#08090c',
    title: 'H.E.R.M.E.S // AGENT CANVAS',
    webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: true },
  });
  win.setMenuBarVisibility(false);
  win.once('ready-to-show', () => win.show());
  win.on('closed', () => { win = null; });
  win.loadURL(URL).catch(err => dialog.showErrorBox('H.E.R.M.E.S', String(err)));
}

function stopBackend() {
  if (!backend || backend.exitCode !== null) return;
  try {
    if (process.platform === 'win32') {
      spawnSync('taskkill', ['/PID', String(backend.pid), '/T', '/F'], { windowsHide: true });
    } else {
      backend.kill('SIGTERM');
    }
  } catch { /* best effort */ }
}

app.whenReady().then(async () => {
  if (!startBackend()) return;
  const ok = await waitForBackend();
  if (!ok) {
    dialog.showErrorBox('H.E.R.M.E.S', 'Backend did not become healthy. Check Python/PyYAML and the terminal output.');
    stopBackend(); app.quit(); return;
  }
  createWindow();
});

app.on('window-all-closed', () => { shuttingDown = true; stopBackend(); app.quit(); });
app.on('before-quit', () => { shuttingDown = true; stopBackend(); });
app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
