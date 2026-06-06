/**
 * Auto-updater для Sleng Уніфікатор — ЧИСТА ЛОГІКА (без UI).
 *
 * Архітектура: цей модуль БЕЗ діалогів. UI зроблений у renderer'і (index.html),
 * сюди передається тільки result/progress через IPC. Тому:
 *   - тут НЕМАЄ електронових dialog.showMessageBox
 *   - тут НЕМАЄ BrowserWindow для прогресу
 *   - main.js під'єднує events через IPC
 *
 * Експортуємо 3 функції:
 *   checkForUpdate()          — повертає {available, version, url, notes} | {available:false}
 *   downloadInstaller(url, cb) — качає .exe у %TEMP%, callback(progress)
 *   runInstallerAndQuit(path) — запускає installer і робить app.quit()
 */

const { app } = require('electron');
const { spawn } = require('child_process');
const fs = require('fs');
const https = require('https');
const os = require('os');
const path = require('path');

const UPDATE_INFO_URL =
  'https://raw.githubusercontent.com/Bogdan54213/sleng-uniquifier-mac-win/main/latest.json';


function fetchJson(url) {
  return new Promise((resolve, reject) => {
    const req = https.get(url, {
      headers: { 'User-Agent': 'SlengUniquifier-Updater' },
      timeout: 15000,
    }, (res) => {
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
  const tmpFile = path.join(os.tmpdir(), `SlengUniquifier_Update_${Date.now()}.exe`);

  return new Promise((resolve, reject) => {
    function handle(res) {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
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
  spawn(installerPath, ['/SILENT', '/CLOSEAPPLICATIONS', '/RESTARTAPPLICATIONS'], {
    detached: true,
    stdio: 'ignore',
    windowsHide: true,
  }).unref();
  setTimeout(() => app.quit(), 1000);
}


async function checkForUpdate() {
  let info;
  try {
    // Cache-bust query param — GitHub raw інколи серверує закешовану версію
    // до ~5 хв після push. Додаємо ?t=timestamp щоб гарантовано отримати свіжу
    // latest.json. Не впливає на серверну логіку (GitHub просто ігнорує).
    const url = UPDATE_INFO_URL + '?t=' + Date.now();
    info = await fetchJson(url);
  } catch (e) {
    console.log('[updater] check failed:', e.message);
    return { available: false, error: e.message };
  }

  const remoteVer = info && info.version;
  const localVer = app.getVersion();
  console.log(`[updater] local=${localVer} remote=${remoteVer}`);

  if (!remoteVer) {
    return { available: false, error: 'latest.json без поля version' };
  }
  if (!isNewerVersion(remoteVer, localVer)) {
    return { available: false, current: localVer };
  }
  return {
    available: true,
    version: remoteVer,
    url: info.url,
    notes: info.notes || '',
  };
}


module.exports = {
  checkForUpdate,
  downloadInstaller,
  runInstallerAndQuit,
};
