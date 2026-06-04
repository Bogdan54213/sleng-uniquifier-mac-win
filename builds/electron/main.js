const { app, BrowserWindow, shell, Menu, dialog, clipboard } = require('electron');
const path  = require('path');
const fs    = require('fs');
const os    = require('os');
const net   = require('net');
const { spawn } = require('child_process');
const updater = require('./updater');

const PORT = 7474;
let serverProcess  = null;
let serverStderr   = '';     // зібраний stderr якщо процес впав
let serverExitCode = null;   // exit code якщо процес закінчився передчасно
let mainWindow     = null;

// ── Шлях до server.exe (cross-platform) ──────────────────────────────────────
function serverExePath() {
  const bin = process.platform === 'win32' ? 'server.exe' : 'server';
  if (app.isPackaged) {
    return path.join(process.resourcesPath, bin);
  }
  return path.join(__dirname, 'resources', bin);
}

// ── Лог-файл (туди ж куди пише server.exe) ──────────────────────────────────
function logFilePath() {
  const base = process.env.LOCALAPPDATA || process.env.APPDATA || os.homedir();
  return path.join(base, 'SlengUniquifier', 'server.log');
}

// ── Очікуємо TCP-порт ────────────────────────────────────────────────────────
function waitForPort(port, timeoutMs = 25000) {
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + timeoutMs;
    const attempt  = () => {
      // Якщо server.exe вже впав — далі чекати немає сенсу
      if (serverExitCode !== null) {
        return reject(new Error(`Сервер завершився передчасно (код ${serverExitCode})`));
      }
      const sock = new net.Socket();
      sock.setTimeout(400);
      sock.on('connect', () => { sock.destroy(); resolve(); });
      sock.on('error',   () => { sock.destroy(); retry(); });
      sock.on('timeout', () => { sock.destroy(); retry(); });
      sock.connect(port, '127.0.0.1');
    };
    const retry = () => {
      if (Date.now() >= deadline) return reject(new Error('Сервер не відкрив порт за 25 сек'));
      setTimeout(attempt, 200);
    };
    attempt();
  });
}

// ── Старт Python-сервера ─────────────────────────────────────────────────────
function startServer() {
  const exePath = serverExePath();

  if (!fs.existsSync(exePath)) {
    throw new Error(`Файл server.exe не знайдено за шляхом:\n${exePath}\n\n` +
                    `Можливо, антивірус видалив його після встановлення.`);
  }

  console.log('[main] starting server:', exePath);

  serverProcess = spawn(exePath, [], {
    // pipe щоб зловити будь-яку stderr-помилку (раніше було ignore — і помилки зникали)
    stdio: ['ignore', 'pipe', 'pipe'],
    detached: false,
    windowsHide: true,
  });

  serverProcess.on('error', err => {
    console.error('[main] server spawn error:', err);
    serverStderr += `\n[spawn error] ${err.message}`;
    serverExitCode = -1;
  });

  serverProcess.stderr.on('data', chunk => {
    const s = chunk.toString('utf-8');
    serverStderr += s;
    console.error('[server stderr]', s);
  });

  serverProcess.stdout.on('data', chunk => {
    console.log('[server stdout]', chunk.toString('utf-8'));
  });

  serverProcess.on('exit', (code, signal) => {
    serverExitCode = code !== null ? code : (signal ? -signal : -999);
    console.log(`[main] server exited code=${code} signal=${signal}`);
  });
}

// ── Діагностичний діалог при невдалому старті ───────────────────────────────
function showDiagnosticDialog(error) {
  const logPath = logFilePath();
  let tailOfLog = '';
  try {
    if (fs.existsSync(logPath)) {
      const content = fs.readFileSync(logPath, 'utf-8');
      // Останні ~2000 символів — найсвіжіше
      tailOfLog = content.length > 2000 ? content.slice(-2000) : content;
    }
  } catch (e) {
    tailOfLog = `(не вдалось прочитати лог: ${e.message})`;
  }

  const reasons = [];
  if (serverExitCode !== null && serverExitCode !== 0) {
    reasons.push(`• Сервер впав з кодом ${serverExitCode}`);
  }
  if (serverStderr.toLowerCase().includes('access') ||
      serverStderr.toLowerCase().includes('permission')) {
    reasons.push('• Брак прав доступу до системних папок');
  }
  if (serverStderr.toLowerCase().includes('address already in use') ||
      serverStderr.includes('10048')) {
    reasons.push('• Порт 7474 уже зайнятий іншою програмою');
  }
  if (!fs.existsSync(serverExePath())) {
    reasons.push('• server.exe видалено антивірусом');
  }

  const text = (
    `Не вдалося запустити сервер додатку.\n\n` +
    `Помилка: ${error.message || error}\n\n` +
    (reasons.length ? `Ймовірні причини:\n${reasons.join('\n')}\n\n` : '') +
    `Що робити:\n` +
    `1. Додай папку «Sleng Uniquifier» у виключення антивірусу і Windows Defender\n` +
    `2. Перезапусти додаток\n` +
    `3. Якщо не допомогло — кинь повний лог куратору\n\n` +
    `Лог: ${logPath}`
  );

  const result = dialog.showMessageBoxSync({
    type: 'error',
    title: 'Помилка запуску',
    message: 'Sleng Уніфікатор не зміг запуститись',
    detail: text,
    buttons: [
      'Скопіювати лог у буфер',
      'Відкрити лог-файл',
      'Закрити',
    ],
    defaultId: 0,
    cancelId: 2,
  });

  if (result === 0) {
    // Копіюємо лог + наш stderr у буфер
    const full = `==== SLENG UNIQUIFIER ERROR LOG ====\n` +
                 `OS: ${process.platform} ${process.arch}\n` +
                 `Electron: ${process.versions.electron}\n` +
                 `Time: ${new Date().toISOString()}\n` +
                 `Exit code: ${serverExitCode}\n\n` +
                 `==== Electron-side stderr ====\n${serverStderr || '(empty)'}\n\n` +
                 `==== server.log (last 2000 chars) ====\n${tailOfLog || '(no log file)'}`;
    clipboard.writeText(full);
    dialog.showMessageBoxSync({
      type: 'info',
      message: 'Лог скопійовано',
      detail: 'Вставляй куратору в Telegram.',
    });
  } else if (result === 1) {
    if (fs.existsSync(logPath)) {
      shell.openPath(logPath);
    } else {
      dialog.showMessageBoxSync({ type: 'warning', message: 'Лог-файл ще не створено' });
    }
  }
}

// ── Створення головного вікна ────────────────────────────────────────────────
async function createWindow() {
  const iconFile  = process.platform === 'win32' ? 'icon.ico' : 'icon.icns';
  const iconPath  = app.isPackaged
    ? path.join(process.resourcesPath, iconFile)
    : path.join(__dirname, iconFile);

  mainWindow = new BrowserWindow({
    width:           620,
    height:          820,
    minWidth:        520,
    minHeight:       640,
    resizable:       true,
    fullscreenable:  true,
    title:           'Sleng Унікалізатор',
    icon:            iconPath,
    backgroundColor: '#0a0a0a',
    show:            false,
    webPreferences: {
      nodeIntegration:  false,
      contextIsolation: true,
    },
  });

  // Прибираємо menubar повністю — професійний нативний look без зайвого File/Edit/View
  Menu.setApplicationMenu(null);

  // Хоткеї: F11 = fullscreen toggle, Ctrl+0/+/- = zoom
  mainWindow.webContents.on('before-input-event', (event, input) => {
    if (input.key === 'F11' && input.type === 'keyDown') {
      mainWindow.setFullScreen(!mainWindow.isFullScreen());
      event.preventDefault();
    }
  });

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

// ── App lifecycle ────────────────────────────────────────────────────────────
app.whenReady().then(async () => {
  try {
    startServer();
  } catch (e) {
    showDiagnosticDialog(e);
    app.quit();
    return;
  }

  try {
    await waitForPort(PORT);
  } catch (e) {
    console.error('[main] waitForPort failed:', e);
    showDiagnosticDialog(e);
    app.quit();
    return;
  }

  await createWindow();

  // Auto-update — перевіряємо через 10 сек після старту.
  // Якщо є нова версія, юзер побачить діалог і зможе оновитись.
  updater.startUpdateCheck(mainWindow);
});

app.on('window-all-closed', () => {
  if (serverProcess) {
    try { serverProcess.kill(); } catch {}
    serverProcess = null;
  }
  app.quit();
});

// Зловимо несподівані помилки на рівні Node (інакше Electron мовчки крашиться)
process.on('uncaughtException', (err) => {
  console.error('[main] uncaughtException:', err);
  try {
    dialog.showErrorBox('Непередбачена помилка', err.stack || err.message || String(err));
  } catch {}
});
