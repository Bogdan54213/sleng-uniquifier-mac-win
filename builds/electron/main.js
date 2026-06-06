const { app, BrowserWindow, shell, Menu, dialog, clipboard, ipcMain } = require('electron');
const path  = require('path');
const fs    = require('fs');
const os    = require('os');
const http  = require('http');
const crypto = require('crypto');
const { spawn } = require('child_process');
const updater = require('./updater');

const PORT = 7474;
const SERVER_TOKEN = crypto.randomBytes(16).toString('hex');
let serverProcess  = null;
let serverStderr   = '';     // зібраний stderr якщо процес впав
let serverExitCode = null;   // exit code якщо процес закінчився передчасно
let mainWindow     = null;

function settingsPath() {
  return path.join(app.getPath('userData'), 'settings.json');
}

function readSettings() {
  try {
    return JSON.parse(fs.readFileSync(settingsPath(), 'utf-8'));
  } catch {
    return {};
  }
}

function writeSettings(settings) {
  const file = settingsPath();
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(settings, null, 2), 'utf-8');
}

function getOutputDir() {
  const dir = readSettings().outputDir || '';
  return dir && fs.existsSync(dir) ? dir : '';
}

function setOutputDir(dir) {
  const settings = readSettings();
  settings.outputDir = dir || '';
  writeSettings(settings);
}

function uniqueOutputPath(dir, filename) {
  const parsed = path.parse(filename || 'video_unique.mp4');
  let candidate = path.join(dir, parsed.base);
  let i = 2;
  while (fs.existsSync(candidate)) {
    candidate = path.join(dir, `${parsed.name}_${i}${parsed.ext || '.mp4'}`);
    i += 1;
  }
  return candidate;
}

// ── Шлях до server.exe (cross-platform) ──────────────────────────────────────
function resourceDir() {
  if (app.isPackaged) {
    return process.resourcesPath;
  }
  return path.join(__dirname, 'resources');
}

function serverExePath() {
  const bin = process.platform === 'win32' ? 'server.exe' : 'server';
  return path.join(resourceDir(), bin);
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
      if (serverExitCode !== null) {
        return reject(new Error(`Сервер завершився передчасно (код ${serverExitCode})`));
      }

      let settled = false;
      const retryOnce = () => {
        if (settled) return;
        settled = true;
        retry();
      };

      const req = http.get({
        hostname: '127.0.0.1',
        port,
        path: '/api/runtime',
        timeout: 400,
      }, res => {
        let body = '';
        res.setEncoding('utf8');
        res.on('data', chunk => { body += chunk; });
        res.on('end', () => {
          try {
            const data = JSON.parse(body);
            if (data && data.token === SERVER_TOKEN) {
              settled = true;
              return resolve();
            }
          } catch {}
          retryOnce();
        });
      });

      req.on('timeout', () => { req.destroy(); retryOnce(); });
      req.on('error',   () => retryOnce());
    };
    const retry = () => {
      if (Date.now() >= deadline) return reject(new Error('Сервер не відкрив порт за 25 сек'));
      setTimeout(attempt, 200);
    };
    attempt();
  });
}

// ── Самозцілення: вбиваємо зомбі-server.exe на порту 7474 ────────────────────
//
// Корінь проблеми: коли Electron крашиться / закривається через Task Manager,
// дочірній server.exe не отримує SIGTERM і продовжує жити. Наступний запуск
// Sleng не може забіндитись на 7474 → юзер бачить "Port already in use".
//
// Замість того щоб питати юзера руками вбити процес — робимо це самі.
// Безпечно: вбиваємо ТІЛЬКИ якщо процес називається 'server.exe' (наш бекенд).
// Не чіпаємо інші процеси які випадково можуть сидіти на 7474.
async function killZombieOnPort(port) {
  return new Promise((resolve) => {
    const { exec } = require('child_process');
    exec(`netstat -ano | findstr :${port}`, (err, stdout) => {
      if (err || !stdout) return resolve(false);  // нікого нема — OK

      // Парсимо PIDs у стані LISTENING. Кожен рядок типу:
      //   TCP    127.0.0.1:7474   0.0.0.0:0   LISTENING   23328
      const pids = new Set();
      for (const line of stdout.split(/\r?\n/)) {
        const m = line.match(/LISTENING\s+(\d+)\s*$/);
        if (m) pids.add(m[1]);
      }
      if (!pids.size) return resolve(false);

      // Для кожного PID перевіряємо ім'я процесу — якщо server.exe → kill
      let pending = pids.size;
      let killed = false;
      pids.forEach((pid) => {
        exec(`tasklist /FI "PID eq ${pid}" /FO CSV /NH`, (e, out) => {
          if (!e && out && /^"server\.exe"/i.test(out.trim())) {
            console.log(`[main] killing zombie server.exe PID=${pid}`);
            exec(`taskkill /F /PID ${pid}`, () => {
              killed = true;
              if (--pending === 0) resolve(killed);
            });
          } else {
            if (--pending === 0) resolve(killed);
          }
        });
      });
    });
  });
}

// Чекаємо доки порт справді звільниться після kill (TimeWait race).
async function waitForPortFree(port, maxMs = 3000) {
  const net = require('net');
  const start = Date.now();
  while (Date.now() - start < maxMs) {
    const free = await new Promise((resolve) => {
      const s = net.createServer();
      s.once('error', () => resolve(false));
      s.once('listening', () => s.close(() => resolve(true)));
      s.listen(port, '127.0.0.1');
    });
    if (free) return true;
    await new Promise((r) => setTimeout(r, 150));
  }
  return false;
}

// ── Старт Python-сервера ─────────────────────────────────────────────────────
function startServer() {
  const exePath = serverExePath();

  if (!fs.existsSync(exePath)) {
    throw new Error(`Файл server.exe не знайдено за шляхом:\n${exePath}\n\n` +
                    `Можливо, антивірус видалив його після встановлення.`);
  }

  console.log('[main] starting server:', exePath);

  const resDir = resourceDir();

  serverProcess = spawn(exePath, [], {
    // pipe щоб зловити будь-яку stderr-помилку (раніше було ignore — і помилки зникали)
    stdio: ['ignore', 'pipe', 'pipe'],
    detached: false,
    windowsHide: true,
    // SLENG_NO_BROWSER=1 — server.py не відкриває браузер на старті,
    // бо Electron сам показує UI всередині BrowserWindow.
    env: {
      ...process.env,
      SLENG_NO_BROWSER: '1',
      SLENG_SERVER_TOKEN: SERVER_TOKEN,
      // FFMPEG_DIR тепер вказує на userData/ffmpeg (ensureFfmpeg завантажив сюди).
      // Раніше було resDir — поруч з server.exe — але це 200MB на кожне auto-update.
      FFMPEG_DIR: require('./ffmpeg_bootstrap').ffmpegDir(),
    },
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

ipcMain.handle('settings:get-output-dir', () => getOutputDir());

ipcMain.handle('settings:choose-output-dir', async () => {
  const current = getOutputDir();
  const result = await dialog.showOpenDialog(mainWindow, {
    title: 'Оберіть папку для готових відео',
    defaultPath: current || app.getPath('videos'),
    properties: ['openDirectory', 'createDirectory'],
  });
  if (result.canceled || !result.filePaths.length) {
    return { canceled: true, outputDir: current };
  }
  const outputDir = result.filePaths[0];
  setOutputDir(outputDir);
  return { canceled: false, outputDir };
});

function attachDownloadHandler(win) {
  win.webContents.session.on('will-download', (event, item) => {
    const outputDir = getOutputDir();
    if (!outputDir) return;
    const savePath = uniqueOutputPath(outputDir, item.getFilename());
    item.setSavePath(savePath);
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

  // Compact desktop tool — фіксоване вікно, не resizable.
  // Це native Windows app, не браузер: нема адресного рядка, нема вкладок.
  mainWindow = new BrowserWindow({
    width:           980,
    height:          680,
    minWidth:        980,
    minHeight:       680,
    maxWidth:        980,
    maxHeight:       680,
    resizable:       false,
    maximizable:     false,
    fullscreenable:  false,
    title:           '',
    icon:            iconPath,
    backgroundColor: '#050505',
    autoHideMenuBar: true,
    frame:           true,
    titleBarStyle:   'hidden',
    // ВАЖЛИВО: color ТУТ має МАТИ ТОЧНО таке саме значення як `--bg-panel`
    // в index.html (зараз #18181B). Інакше у Windows shell зона з ─/✕
    // показується як темно-сірий «квадрат» поруч з топбаром іншого кольору.
    titleBarOverlay: {
      color:       '#18181B',
      symbolColor: '#8d8f99',
      height:      34,
    },
    show:            false,
    webPreferences: {
      nodeIntegration:  false,
      contextIsolation: true,
      preload:          path.join(__dirname, 'preload.js'),
      // У production .exe DevTools повністю відключений — блокує F12,
      // Ctrl+Shift+I, mainWindow.webContents.openDevTools() усе разом.
      // У dev-режимі (npm start) — лишається для діагностики.
      devTools:         !app.isPackaged,
    },
  });

  attachDownloadHandler(mainWindow);

  // Додатковий захист: перехоплюємо Ctrl+Shift+I / F12 у production
  // і блокуємо їх явно (хоча devTools:false уже мав би це робити).
  if (app.isPackaged) {
    mainWindow.webContents.on('before-input-event', (event, input) => {
      const isDevTools =
        (input.control && input.shift && input.key.toLowerCase() === 'i') ||
        input.key === 'F12';
      if (isDevTools) event.preventDefault();
    });
  }

  // Прибираємо menubar повністю — професійний нативний look без зайвого File/Edit/View
  Menu.setApplicationMenu(null);

  // Хоткеї для діагностики (вікно фіксоване — F11/maximize не потрібні)
  //   Ctrl+Shift+I  — Chromium DevTools
  //   Ctrl+R / F5   — reload
  mainWindow.webContents.on('before-input-event', (event, input) => {
    if (input.type !== 'keyDown') return;
    if (input.control && input.shift && input.key.toUpperCase() === 'I') {
      mainWindow.webContents.toggleDevTools();
      event.preventDefault();
    } else if ((input.control && input.key.toUpperCase() === 'R') || input.key === 'F5') {
      mainWindow.webContents.reload();
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

  // Коли main готова — спершу закриваємо splash, потім показуємо main.
  // Таким чином немає миті коли видно обидва вікна (або жодного).
  mainWindow.once('ready-to-show', () => {
    if (splashWindow && !splashWindow.isDestroyed()) {
      try { splashWindow.close(); } catch {}
      splashWindow = null;
    }
    mainWindow.show();
  });

  await mainWindow.loadURL(`http://127.0.0.1:${PORT}`);
}

// ── Splash window (показуємо одразу, поки server.exe розпаковує _MEI) ──────
let splashWindow = null;
function createSplashWindow() {
  splashWindow = new BrowserWindow({
    width:           360,
    height:          240,
    frame:           false,
    transparent:     true,
    resizable:       false,
    movable:         true,
    skipTaskbar:     false,
    alwaysOnTop:     false,
    center:          true,
    hasShadow:       true,
    backgroundColor: '#00000000',
    title:           'Sleng Унікалізатор',
    show:            false,
    webPreferences: {
      nodeIntegration:  false,
      contextIsolation: true,
    },
  });
  splashWindow.loadFile(path.join(__dirname, 'splash.html'));
  splashWindow.once('ready-to-show', () => {
    if (splashWindow && !splashWindow.isDestroyed()) splashWindow.show();
  });
  splashWindow.on('closed', () => { splashWindow = null; });
}

function closeSplash() {
  if (splashWindow && !splashWindow.isDestroyed()) {
    try { splashWindow.close(); } catch {}
  }
  splashWindow = null;
}

// ── App lifecycle ────────────────────────────────────────────────────────────
app.whenReady().then(async () => {
  // 1) ПЕРШЕ — splash, миттєво. Юзер не бачить голого чорного flash'a.
  createSplashWindow();

  // 1.5) Самозцілення: якщо від попереднього запуску живий зомбі-server.exe
  // на нашому порту — вбиваємо. Інакше startServer запустить новий процес
  // який одразу впаде з "Port already in use".
  try {
    const killed = await killZombieOnPort(PORT);
    if (killed) {
      console.log('[main] zombie server.exe killed, waiting for port to free');
      await waitForPortFree(PORT, 3000);
    }
  } catch (e) {
    console.warn('[main] killZombieOnPort failed (non-fatal):', e.message);
  }

  // 1.7) Перевіряємо що ffmpeg/ffprobe доступні. При першому запуску їх нема —
  // тоді качаємо ZIP з BtbN (~80MB, одноразово) і кешуємо в userData/ffmpeg.
  // Усі наступні апдейти Sleng НЕ перекачують ці 200MB (раніше були у installer).
  const ffmpegBootstrap = require('./ffmpeg_bootstrap');
  try {
    if (!ffmpegBootstrap.isInstalled()) {
      console.log('[main] ffmpeg not found — downloading on first launch ...');
      // Можна було б показати progress у splash, але для MVP — просто блок-чекаємо.
      // Splash і так показує "Завантажую..." дефолтний текст.
      await ffmpegBootstrap.ensureFfmpeg((p) => {
        if (p.phase === 'download' && p.pct) {
          console.log(`[ffmpeg] download ${Math.round(p.pct * 100)}%`);
        }
      });
    }
  } catch (e) {
    closeSplash();
    showDiagnosticDialog(new Error(
      'Не вдалось завантажити FFmpeg (потрібен для уніфікації відео).\n\n' +
      'Перевір інтернет-зʼєднання та перезапусти Sleng.\n\nДеталі: ' + e.message
    ));
    app.quit();
    return;
  }

  try {
    startServer();
  } catch (e) {
    closeSplash();
    showDiagnosticDialog(e);
    app.quit();
    return;
  }

  try {
    await waitForPort(PORT);
  } catch (e) {
    console.error('[main] waitForPort failed:', e);
    closeSplash();
    showDiagnosticDialog(e);
    app.quit();
    return;
  }

  // 2) Створюємо main вікно (приховане). У ready-to-show callback'у
  //    автоматично закриється splash і покажеться main — плавно.
  await createWindow();

  // Auto-update: ТИХА фонова перевірка через 3 сек.
  // НЕМАЄ native popup'у. Якщо є оновлення — посилаємо IPC у renderer,
  // який показує власну "Доступне оновлення" pill у топбарі.
  registerUpdateIPC();
  scheduleSilentUpdateCheck();
});


// ── Auto-update IPC + silent background check ────────────────────────────────

// Останній відомий результат перевірки. Renderer може його запитати
// у будь-який момент через slengUpdate.getStatus().
let pendingUpdate = { available: false };

function registerUpdateIPC() {
  ipcMain.handle('update:get-status', () => pendingUpdate);

  ipcMain.handle('update:check', async () => {
    const info = await updater.checkForUpdate();
    pendingUpdate = info;
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('update:status', info);
    }
    return info;
  });

  ipcMain.handle('update:start', async () => {
    if (!pendingUpdate.available) return { ok: false, error: 'No update available' };
    try {
      const installerPath = await updater.downloadInstaller(pendingUpdate.url, (p) => {
        if (mainWindow && !mainWindow.isDestroyed()) {
          mainWindow.webContents.send('update:progress', p);
        }
      });
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('update:installing');
      }
      // Даємо renderer'у час показати "Запускаю інсталер..." до закриття вікна
      setTimeout(() => updater.runInstallerAndQuit(installerPath), 800);
      return { ok: true };
    } catch (e) {
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('update:error', e.message);
      }
      return { ok: false, error: e.message };
    }
  });
}

function scheduleSilentUpdateCheck() {
  if (!app.isPackaged) {
    console.log('[updater] dev mode — skip update check');
    return;
  }
  setTimeout(async () => {
    const info = await updater.checkForUpdate();
    pendingUpdate = info;
    if (info.available && mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('update:status', info);
    }
  }, 3000);
}

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
