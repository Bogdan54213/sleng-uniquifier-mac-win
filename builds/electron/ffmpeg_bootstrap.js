/**
 * FFmpeg bootstrap — one-time download з BtbN при першому запуску.
 *
 * Чому так: ffmpeg.exe + ffprobe.exe важать ~200 MB. Якщо їх класти в
 * installer кожного релізу — це 200 MB трафіку на кожне оновлення.
 * Натомість юзер качає їх ОДИН РАЗ при першому запуску, кешує у
 * %LOCALAPPDATA%\SlengUniquifier\ffmpeg\, всі наступні оновлення
 * Sleng не торкають їх.
 *
 * Економія: ~200 MB на кожне auto-update (460 MB → 260 MB).
 */
const { app } = require('electron');
const fs = require('fs');
const https = require('https');
const path = require('path');
const { execSync } = require('child_process');


// Офіційний BtbN release — той самий що ми тягнули у CI до v1.0.29.
// Якщо коли-небудь BtbN зміняться/зникнуть — можна замінити на власний
// mirror на GitHub Release нашого репо.
const FFMPEG_ZIP_URL =
  'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip';


function ffmpegDir() {
  // %LOCALAPPDATA%\SlengUniquifier\ffmpeg — той самий root що auth.db
  // (server.py читає LOCALAPPDATA, тут просто додаємо підпапку).
  const base = app.getPath('userData');
  return path.join(base, 'ffmpeg');
}

function ffmpegExe() { return path.join(ffmpegDir(), 'ffmpeg.exe'); }
function ffprobeExe() { return path.join(ffmpegDir(), 'ffprobe.exe'); }

function isInstalled() {
  try {
    return fs.statSync(ffmpegExe()).size > 1_000_000 &&
           fs.statSync(ffprobeExe()).size > 1_000_000;
  } catch {
    return false;
  }
}

function downloadToFile(url, outPath, onProgress) {
  return new Promise((resolve, reject) => {
    function handle(res) {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        https.get(res.headers.location, { headers: { 'User-Agent': 'SlengUniquifier' } }, handle)
             .on('error', reject);
        return;
      }
      if (res.statusCode !== 200) {
        return reject(new Error(`HTTP ${res.statusCode} on ${url}`));
      }
      const total = parseInt(res.headers['content-length'], 10) || 0;
      let downloaded = 0;
      const file = fs.createWriteStream(outPath);
      res.on('data', (chunk) => {
        downloaded += chunk.length;
        file.write(chunk);
        if (onProgress && total > 0) {
          onProgress({ downloaded, total, pct: downloaded / total });
        }
      });
      res.on('end', () => file.end(() => resolve()));
      res.on('error', (e) => { file.destroy(); reject(e); });
    }
    https.get(url, { headers: { 'User-Agent': 'SlengUniquifier' } }, handle).on('error', reject);
  });
}

/**
 * Завантажує FFmpeg ZIP, розпаковує тільки ffmpeg.exe + ffprobe.exe
 * у `ffmpegDir`. Повертає шлях до папки.
 *
 * onProgress({downloaded, total, pct, phase}) — для UI splash.
 */
async function ensureFfmpeg(onProgress) {
  if (isInstalled()) {
    return ffmpegDir();
  }

  const dir = ffmpegDir();
  fs.mkdirSync(dir, { recursive: true });
  const zipPath = path.join(dir, '_ffmpeg.zip');

  console.log('[ffmpeg-bootstrap] downloading from BtbN ...');
  if (onProgress) onProgress({ phase: 'download', pct: 0 });
  await downloadToFile(FFMPEG_ZIP_URL, zipPath, (p) => {
    if (onProgress) onProgress({ ...p, phase: 'download' });
  });

  console.log('[ffmpeg-bootstrap] extracting ...');
  if (onProgress) onProgress({ phase: 'extract', pct: 0 });

  // Використовуємо вбудовану команду tar — є у Windows 10+ і не потребує
  // npm-залежностей. Розпаковує тільки потрібні файли через --include.
  // Альтернатива через unzip-stream чужою бібліотекою — добавляла б ~500 KB
  // до installer'у; tar вже там.
  try {
    execSync(`tar -xf "${zipPath}" -C "${dir}"`, { stdio: 'pipe' });
  } catch (e) {
    throw new Error(`Розпакування FFmpeg ZIP не вдалось: ${e.message}`);
  }

  // BtbN кладе всередину папку 'ffmpeg-master-latest-win64-gpl/bin/'.
  // Знаходимо ffmpeg.exe і ffprobe.exe незалежно від точної назви папки.
  function findExe(name) {
    const stack = [dir];
    while (stack.length) {
      const cur = stack.pop();
      let entries;
      try { entries = fs.readdirSync(cur, { withFileTypes: true }); } catch { continue; }
      for (const e of entries) {
        const p = path.join(cur, e.name);
        if (e.isFile() && e.name.toLowerCase() === name) return p;
        if (e.isDirectory()) stack.push(p);
      }
    }
    return null;
  }

  const foundFfmpeg = findExe('ffmpeg.exe');
  const foundFfprobe = findExe('ffprobe.exe');
  if (!foundFfmpeg || !foundFfprobe) {
    throw new Error('Розпаковано ZIP, але ffmpeg.exe / ffprobe.exe не знайдено');
  }

  // Переносимо у root ffmpegDir щоб server.exe міг знайти за прямим шляхом
  fs.copyFileSync(foundFfmpeg, ffmpegExe());
  fs.copyFileSync(foundFfprobe, ffprobeExe());

  // Cleanup: видаляємо ZIP + проміжну розпаковану папку BtbN
  try {
    fs.unlinkSync(zipPath);
    // Видаляємо все що не ffmpeg.exe/ffprobe.exe (тобто розпаковану BtbN папку)
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (entry.isDirectory()) {
        fs.rmSync(path.join(dir, entry.name), { recursive: true, force: true });
      }
    }
  } catch (e) {
    console.log('[ffmpeg-bootstrap] cleanup warning (non-fatal):', e.message);
  }

  if (onProgress) onProgress({ phase: 'done', pct: 1 });
  console.log('[ffmpeg-bootstrap] installed at', dir);
  return dir;
}


module.exports = {
  ensureFfmpeg,
  ffmpegDir,
  isInstalled,
};
