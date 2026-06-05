const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('slengSettings', {
  getOutputDir: () => ipcRenderer.invoke('settings:get-output-dir'),
  chooseOutputDir: () => ipcRenderer.invoke('settings:choose-output-dir'),
});

// Update API — replaces native dialogs. Renderer слухає події і малює власний UI.
contextBridge.exposeInMainWorld('slengUpdate', {
  // Запитати поточний статус (наприклад, при відкритті pill'у).
  getStatus: () => ipcRenderer.invoke('update:get-status'),
  // Форсувати перевірку (наприклад, юзер натиснув "Перевірити" вручну).
  check: () => ipcRenderer.invoke('update:check'),
  // Стартувати завантаження + інсталяцію.
  startInstall: () => ipcRenderer.invoke('update:start'),
  // Підписки на події з main process.
  onStatus: (cb) => ipcRenderer.on('update:status', (_e, info) => cb(info)),
  onProgress: (cb) => ipcRenderer.on('update:progress', (_e, p) => cb(p)),
  onInstalling: (cb) => ipcRenderer.on('update:installing', () => cb()),
  onError: (cb) => ipcRenderer.on('update:error', (_e, msg) => cb(msg)),
});
