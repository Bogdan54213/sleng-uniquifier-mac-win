const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('slengSettings', {
  getOutputDir: () => ipcRenderer.invoke('settings:get-output-dir'),
  chooseOutputDir: () => ipcRenderer.invoke('settings:choose-output-dir'),
});
