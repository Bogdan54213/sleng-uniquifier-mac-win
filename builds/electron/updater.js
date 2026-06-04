/**
 * Auto-updater для Sleng Уніфікатор.
 *
 * Архітектура:
 *   1. У UPDATE_INFO_URL лежить JSON виду:
 *        { "version": "1.1.0",
 *          "url":     "https://github.com/.../SlengUniquifier_Setup_v1.1.0.exe",
 *          "notes":   "Що змінилось у цій версії" }
 *      Хоститься на GitHub (raw.githubusercontent.com), Cloudflare Pages,
 *      чи будь-якому статичному URL.
 *
 *   2. При старті (через 10 сек після завантаження вікна) додаток смикає
 *      цей URL і порівнює `version` з власною (з package.json через app.getVersion()).
 *
 *   3. Якщо нова версія — показуємо діалог «Доступне оновлення».
 *
 *   4. Юзер тицяє «📥 Завантажити»:
 *        - качаємо .exe у %TEMP%
 *        - показуємо progress bar
 *        - запускаємо installer з /SILENT /CLOSEAPPLICATIONS
 *        - наш додаток закривається
 *        - installer все встановлює і автоматом запускає нову версію
 *
 *   5. Якщо юзер «Пізніше» — нагадаємо при наступному запуску.
 *
 * ВАЖЛИВО: щоб update-flow працював, у setup_electron.iss має бути:
 *   CloseApplications=force
 *   RestartApplications=yes
 * (вже додано).
 */

const { app, dialog, shell, BrowserWindow } = require('electron');
const { spawn } = require('child_process');
const fs    = require('fs');
const https = require('https');
const os    = require('os');
const path  = require('path');


// ─────────────────────────────────────────────────────────────────────────────
// КОНФІГУРАЦІЯ
// ─────────────────────────────────────────────────────────────────────────────

// Bogdan54213/sleng-uniquifier-mac-win — твій робочий репозиторій.
// ⚠️ Це raw-URL працює ТІЛЬКИ якщо репозиторій PUBLIC.
//    Приватний — потребує auth-токен, який не можна вкласти в .exe.
//    Або зроби репо public у Settings → Danger Zone → Change visibility,
//    або переключи на окремий public-репо для релізів.
const UPDATE_INFO_URL =
  'https://raw.githubusercontent.com/Bogdan54213/sleng-uniquifier-mac-win/main/latest.json';

// Скільки чекати між перевірками. 6 годин — достатньо для більшості випадків.
const CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000;

// Затримка перевірки після старту (даємо вікну прогрузитись).
const STARTUP_CHECK_DELAY_MS = 10_000;


// ─────────────────────────────────────────────────────────────────────────────
// УТИЛІТИ
// ─────────────────────────────────────────────────────────────────────────────

function fetchJson(url) {
  return new Promise((resolve, reject) => {
    const req = https.get(url, {
      headers: { 'User-Agent': 'SlengUniquifier-Updater' },
      timeout: 15000,
    }, (res) => {
      // Слідуємо за редіректами вручну (https.get не робить це автоматично)
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        return fetchJson(res.headers.location).then(resolve, reject);
      }
      if (res.statusCode !== 200) {
        return reject(new Error(`HTTP ${res.statusCode} on ${url}`));
      }
      let data = '';
      res.setEncoding('utf-8');
      res.on('data', (chunk) => data += chunk);
      res.on('end', () => {
        try { resolve(JSON.parse(data)); }
        catch (e) { reject(new Error(`Bad JSON: ${e.message}`)); }
      });
    });
    req.on('timeout', () => { req.destroy(new Error('Timeout')); });
    req.on('error', reject);
  });
}


function isNewerVersion(remote, local) {
  // Порівнюємо semver. 1.2.0 > 1.1.9, 1.0.10 > 1.0.9.
  const r = String(remote).split('.').map((n) => parseInt(n, 10) || 0);
  const l = String(local).split('.').map((n) => parseInt(n, 10) || 0);
  for (let i = 0; i < Math.max(r.length, l.length); i++) {
    const a = r[i] || 0;
    const b = l[i] || 0;
    if (a > b) return true;
    if (a < b) return false;
  }
  return false;
}


function downloadInstaller(url, onProgress) {
  const tmpFile = path.join(
    os.tmpdir(),
    `SlengUniquifier_Update_${Date.now()}.exe`,
  );

  return new Promise((resolve, reject) => {
    function handle(res) {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        // Редірект (GitHub releases часто роблять 302 на CDN)
        https.get(res.headers.location, { headers: { 'User-Agent': 'SlengUniquifier' } }, handle)
             .on('error', reject);
        return;
      }
      if (res.statusCode !== 200) {
        return reject(new Error(`Download failed: HTTP ${res.statusCode}`));
      }

      const total = parseInt(res.headers['content-length'], 10) || 0;
      let downloaded = 0;
      const file = fs.createWriteStream(tmpFile);

      res.on('data', (chunk) => {
        downloaded += chunk.length;
        file.write(chunk);
        if (onProgress && total > 0) {
          onProgress({ downloaded, total, pct: downloaded / total });
        }
      });

      res.on('end', () => {
        file.end(() => resolve(tmpFile));
      });

      res.on('error', (e) => {
        file.destroy();
        try { fs.unlinkSync(tmpFile); } catch {}
        reject(e);
      });
    }

    https.get(url, { headers: { 'User-Agent': 'SlengUniquifier' } }, handle)
         .on('error', reject);
  });
}


function runInstallerAndQuit(installerPath) {
  // /SILENT — без UI installer'а
  // /CLOSEAPPLICATIONS — закриває нашу запущену версію перед install
  // /RESTARTAPPLICATIONS — після install запускає її назад
  // detached:true + stdio:ignore — installer переживе наш app.quit()
  spawn(installerPath, ['/SILENT', '/CLOSEAPPLICATIONS', '/RESTARTAPPLICATIONS'], {
    detached: true,
    stdio:    'ignore',
    windowsHide: true,
  }).unref();

  // Даємо installer'у час спочатку запуститись, потім гасимо себе.
  setTimeout(() => app.quit(), 1000);
}


// ─────────────────────────────────────────────────────────────────────────────
// ОСНОВНИЙ FLOW
// ─────────────────────────────────────────────────────────────────────────────

let lastCheckAt = 0;
let dismissedVersion = null;  // версія яку юзер відклав «Пізніше» — не питаємо повторно


async function checkForUpdate(force = false) {
  const now = Date.now();
  if (!force && (now - lastCheckAt) < CHECK_INTERVAL_MS) {
    return { available: false, throttled: true };
  }
  lastCheckAt = now;

  let info;
  try {
    info = await fetchJson(UPDATE_INFO_URL);
  } catch (e) {
    console.log('[updater] check failed:', e.message);
    return { available: false, error: e.message };
  }

  const remoteVer = info && info.version;
  const localVer  = app.getVersion();

  console.log(`[updater] local=${localVer} remote=${remoteVer}`);

  if (!remoteVer) {
    return { available: false, error: 'latest.json без поля version' };
  }
  if (!isNewerVersion(remoteVer, localVer)) {
    return { available: false, current: localVer };
  }

  return {
    available: true,
    version:   remoteVer,
    url:       info.url,
    notes:     info.notes || '',
  };
}


async function promptAndInstall(parentWindow, updateInfo) {
  // Якщо юзер уже відклав цю версію — не питаємо повторно (до наступного запуску)
  if (dismissedVersion === updateInfo.version) {
    return false;
  }

  const result = dialog.showMessageBoxSync(parentWindow, {
    type:      'info',
    title:     'Доступне оновлення',
    message:   `📦 Версія ${updateInfo.version} вже доступна`,
    detail:    (updateInfo.notes || 'Опис відсутній.').slice(0, 700) +
               '\n\nПри натисканні «Оновити»:\n' +
               '  • Завантажиться інсталер (~50 МБ)\n' +
               '  • Поточна версія закриється\n' +
               '  • Нова встановиться і запустяться автоматом',
    buttons:   ['📥 Оновити', '⏳ Пізніше'],
    defaultId: 0,
    cancelId:  1,
  });

  if (result !== 0) {
    dismissedVersion = updateInfo.version;
    return false;
  }

  // Показуємо вікно прогресу
  const progressWin = new BrowserWindow({
    parent:        parentWindow,
    modal:         true,
    width:         420,
    height:        160,
    resizable:     false,
    minimizable:   false,
    maximizable:   false,
    title:         'Завантаження оновлення',
    backgroundColor: '#0d1b34',
    webPreferences: {
      nodeIntegration:  true,
      contextIsolation: false,
    },
  });

  // Маленький HTML inline — без окремих файлів
  const progressHtml = `
    <!DOCTYPE html>
    <html><head><meta charset="utf-8"></head>
    <body style="margin:0; font-family: 'Segoe UI', system-ui, sans-serif;
                 background:#0d1b34; color:#fff; padding:20px;">
      <h3 style="margin:0 0 12px; font-size:14px; font-weight:600;">
        Завантажуємо v${updateInfo.version}…
      </h3>
      <div style="background:#1e2f4e; height:18px; border-radius:9px; overflow:hidden;">
        <div id="bar" style="background:linear-gradient(90deg, #4a90e2, #5cb85c);
                              height:100%; width:0%; transition:width 0.2s;"></div>
      </div>
      <p id="status" style="margin:12px 0 0; opacity:0.7; font-size:13px;">
        Підготовка…
      </p>
      <script>
        const { ipcRenderer } = require('electron');
        ipcRenderer.on('progress', (e, p) => {
          const pct = Math.round(p.pct * 100);
          document.getElementById('bar').style.width = pct + '%';
          const mb = (b) => (b / 1048576).toFixed(1);
          document.getElementById('status').textContent =
            mb(p.downloaded) + ' / ' + mb(p.total) + ' МБ  (' + pct + '%)';
        });
      </script>
    </body></html>
  `;
  progressWin.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(progressHtml));

  try {
    const installerPath = await downloadInstaller(updateInfo.url, (p) => {
      if (!progressWin.isDestroyed()) {
        progressWin.webContents.send('progress', p);
      }
    });

    if (!progressWin.isDestroyed()) progressWin.close();

    dialog.showMessageBoxSync(parentWindow, {
      type:    'info',
      title:   'Готово',
      message: 'Завантаження завершено',
      detail:  'Зараз запустимо інсталер. Додаток закриється — це нормально.\n' +
               'Після завершення нова версія відкриється автоматично.',
      buttons: ['Встановити'],
    });

    runInstallerAndQuit(installerPath);
    return true;

  } catch (e) {
    if (!progressWin.isDestroyed()) progressWin.close();
    dialog.showErrorBox(
      'Помилка завантаження',
      `Не вдалось завантажити оновлення:\n${e.message}\n\n` +
      'Спробуй пізніше або скачай вручну з:\n' +
      updateInfo.url
    );
    return false;
  }
}


/**
 * Точка входу: запускається з main.js одразу після створення вікна.
 * Робить перевірку через STARTUP_CHECK_DELAY_MS і показує діалог якщо є оновлення.
 */
function startUpdateCheck(mainWindow) {
  // Не перевіряємо в dev-режимі (npm start) — там app.isPackaged === false
  if (!app.isPackaged) {
    console.log('[updater] dev mode — skip update check');
    return;
  }

  setTimeout(async () => {
    const info = await checkForUpdate(true);
    if (info.available && mainWindow && !mainWindow.isDestroyed()) {
      await promptAndInstall(mainWindow, info);
    }
  }, STARTUP_CHECK_DELAY_MS);
}


module.exports = {
  startUpdateCheck,
  checkForUpdate,
  promptAndInstall,
};
