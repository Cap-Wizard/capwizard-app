"use strict";

const { contextBridge, ipcRenderer, webUtils } = require("electron");

contextBridge.exposeInMainWorld("api", {
  backendUrl: () => ipcRenderer.invoke("backend-url"),
  backendToken: ipcRenderer.sendSync("backend-token"),
  cloudUrl: () => ipcRenderer.invoke("cloud-url"),
  selectFolder: () => ipcRenderer.invoke("select-folder"),
  selectVideo: () => ipcRenderer.invoke("select-video"),
  selectVideos: () => ipcRenderer.invoke("select-videos"),
  selectScript: () => ipcRenderer.invoke("select-script"),
  selectImages: () => ipcRenderer.invoke("select-images"),
  selectSkill: () => ipcRenderer.invoke("select-skill"),
  saveSkill: (name) => ipcRenderer.invoke("save-skill", name),
  openPath: (p) => ipcRenderer.invoke("open-path", p),
  // real path of a file or folder dropped on the window
  pathForFile: (f) => webUtils.getPathForFile(f),
  openExternal: (url) => ipcRenderer.invoke("open-external", url),
  getToken: () => ipcRenderer.invoke("auth-get-token"),
  setToken: (t) => ipcRenderer.invoke("auth-set-token", t),
  clearToken: () => ipcRenderer.invoke("auth-clear-token"),
  onBackendReady: (cb) =>
    ipcRenderer.on("backend-ready", (_e, data) => cb(data)),
});
