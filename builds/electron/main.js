const { app, BrowserWindow, shell, Menu } = require('electron');
const path  = require('path');
const net   = require('net');
const { spawn } = require('child_process');

const PORT = 7474;
let serverProcess = null;
let mainWindow    = null;

// ── Locate server binary (cross-platform) ─────────────────────────────────────
function serverExePath() {
  const bin = process.platform === 'win32' ? 'server.exe' : 'server';
  if (app.isPackaged) {
    return path.join(process.resourcesPath, bin);
  }
  return path.join(__dirname, 'resources', bin);
}

// ── Wait for TCP port to accept connections ───────────────────────────────────
function waitForPort(port, timeoutMs = 20000) {
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + timeoutMs;
    const attempt  = () => {
      const sock = new net.Socket();
      sock.setTimeout(400);
      sock.on('connect', () => { sock.destroy(); resolve(); });
      sock.on('error',   () => { sock.destroy(); retry(); });
      sock.on('timeout', () => { sock.destroy(); retry(); });
      sock.connect(port, '127.0.0.1');
    };
    const retry = () => {
      if (Date.now() >= deadline) return reject(new Error('Server startup timeout'));
      setTimeout(attempt, 200);
    };
    attempt();
  });
}

// ── Start Python server ───────────────────────────────────────────────────────
function startServer() {
  const exePath = serverExePath();
  serverProcess = spawn(exePath, [], {
    stdio:    'ignore',
    detached: false,
  });
  serverProcess.on('error', err => console.error('Server spawn error:', err));
}

// ── Create main window ────────────────────────────────────────────────────────
async function createWindow() {
  const iconFile  = process.platform === 'win32' ? 'icon.ico' : 'icon.icns';
  const iconPath  = app.isPackaged
    ? path.join(process.resourcesPath, iconFile)
    : path.join(__dirname, iconFile);

  mainWindow = new BrowserWindow({
    width:           580,
    height:          750,
    resizable:       false,
    title:           'Sleng Унікалізатор',
    icon:            iconPath,
    backgroundColor: '#0d1b34',
    show:            false,
    webPreferences: {
      nodeIntegration:  false,
      contextIsolation: true,
    },
  });

  Menu.setApplicationMenu(null);

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });
  mainWindow.webContents.on('will-navigate', (e, url) => {
    if (!url.startsWith(`http://127.0.0.1:${PORT}`)) e.preventDefault();
  });

  mainWindow.once('ready-to-show', () => mainWindow.show());

  await mainWindow.loadURL(`http://127.0.0.1:${PORT}`);
}

// ── App lifecycle ─────────────────────────────────────────────────────────────
app.whenReady().then(async () => {
  startServer();
  try {
    await waitForPort(PORT);
  } catch (e) {
    console.error('Server did not start in time');
  }
  await createWindow();
});

app.on('window-all-closed', () => {
  if (serverProcess) {
    serverProcess.kill();
    serverProcess = null;
  }
  app.quit();
});
