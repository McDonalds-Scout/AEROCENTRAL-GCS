const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("aeroCentralDesktop", {
  platform: process.platform,
  isDesktop: true,
  refresh(hard = false) {
    return ipcRenderer.invoke("desktop-refresh-ui", { hard: Boolean(hard) });
  },
  onBackendExit(callback) {
    ipcRenderer.on("desktop-backend-exit", (_event, payload) => callback(payload));
  },
  onConnectionError(callback) {
    ipcRenderer.on("desktop-connection-error", (_event, payload) => callback(payload));
  }
});
