const { app, BrowserWindow, Menu, dialog, shell, ipcMain } = require("electron");
const { spawn, spawnSync } = require("child_process");
const fs = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");

const APP_NAME = "AeroCentral";
const PORT_FALLBACKS = [8080, 8094, 8095, 8096, 8097];
const PACKAGED_PROCESS_NAMES = new Set([
  "ground_station_server.exe",
  "px6c_connector.exe",
  "mavlink_simulator.exe"
]);

let mainWindow = null;
let backendProcess = null;
let backendUrl = "";
let config = null;
let ownsBackend = false;

const SMOKE_TEST = process.argv.includes("--smoke-test");
const FORCE_OWN_BACKEND = process.argv.includes("--force-own-backend");
const ALLOW_BACKEND_REUSE = process.argv.includes("--reuse-backend") || process.env.AEROCENTRAL_REUSE_BACKEND === "1";

app.commandLine.appendSwitch("disable-http-cache");
app.commandLine.appendSwitch("disk-cache-size", "0");

let isPrimaryInstance = true;
if (!SMOKE_TEST) {
  const gotSingleInstanceLock = app.requestSingleInstanceLock();
  if (!gotSingleInstanceLock) {
    isPrimaryInstance = false;
    app.quit();
  } else {
    app.on("second-instance", () => {
      if (!mainWindow || mainWindow.isDestroyed()) return;
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
      refreshMainWindow({ hard: true }).catch(() => {});
    });
  }
}

function cliArgValue(prefix) {
  const match = process.argv.find((arg) => arg.startsWith(prefix));
  return match ? match.slice(prefix.length) : "";
}

const CONFIG_PATH_OVERRIDE =
  cliArgValue("--aerocentral-config=") ||
  cliArgValue("--config=") ||
  process.env.AEROCENTRAL_CONFIG_PATH ||
  "";

const DEFAULT_CONFIG = {
  backend: {
    port: 8080,
    readyTimeoutMs: 30000
  },
  mavlink: {
    autoConnect: false,
    type: "serial",
    serialPort: "",
    baud: 115200,
    listenAddress: "0.0.0.0",
    listenPort: 14550,
    targetIp: "127.0.0.1",
    targetPort: 14550
  },
  window: {
    width: 1440,
    height: 960
  }
};

function deepMerge(base, override) {
  const result = Array.isArray(base) ? [...base] : { ...base };
  for (const [key, value] of Object.entries(override || {})) {
    if (value && typeof value === "object" && !Array.isArray(value)) {
      result[key] = deepMerge(result[key] || {}, value);
    } else {
      result[key] = value;
    }
  }
  return result;
}

function userDataDir() {
  return app.getPath("userData");
}

function configPath() {
  if (CONFIG_PATH_OVERRIDE) {
    return path.resolve(CONFIG_PATH_OVERRIDE);
  }
  return path.join(userDataDir(), "app_config.json");
}

function readJson(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8").replace(/^\uFEFF/, ""));
  } catch (_) {
    return null;
  }
}

function writeJsonIfMissing(filePath, data) {
  if (fs.existsSync(filePath)) return;
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, JSON.stringify(data, null, 2), "utf8");
}

function loadDesktopConfig() {
  const file = configPath();
  writeJsonIfMissing(file, DEFAULT_CONFIG);
  config = deepMerge(DEFAULT_CONFIG, readJson(file) || {});
  return config;
}

function projectRoot() {
  return path.resolve(__dirname, "..", "..");
}

function backendRoot() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, "backend");
  }
  return projectRoot();
}

function backendExecutablePath(root) {
  const suffix = process.platform === "win32" ? ".exe" : "";
  return firstExisting([
    path.join(root, "ground_station_server", `ground_station_server${suffix}`),
    path.join(root, `ground_station_server${suffix}`)
  ]) || path.join(root, `ground_station_server${suffix}`);
}

function firstExisting(paths) {
  return paths.find((candidate) => candidate && fs.existsSync(candidate)) || "";
}

function frontendResourceRoot(root) {
  const candidates = app.isPackaged
    ? [
        path.join(process.resourcesPath, "frontend"),
        path.join(process.resourcesPath, "app"),
        root
      ]
    : [root];
  return firstExisting(candidates.filter((candidate) => {
    try {
      return fs.existsSync(path.join(candidate, "index.html"))
        && fs.existsSync(path.join(candidate, "app.js"))
        && fs.existsSync(path.join(candidate, "styles.css"));
    } catch (_) {
      return false;
    }
  }));
}

function pythonCommand(root) {
  const explicit = process.env.PYTHON_EXE || process.env.PYTHON;
  if (explicit) return { command: explicit, args: [path.join(root, "ground_station_server.py")] };
  if (process.platform === "win32") {
    const bundledPython = firstExisting([
      path.join(root, ".venv", "Scripts", "python.exe"),
      path.join(os.homedir(), ".cache", "codex-runtimes", "codex-primary-runtime", "dependencies", "python", "python.exe")
    ]);
    if (bundledPython) {
      return { command: bundledPython, args: [path.join(root, "ground_station_server.py")] };
    }
    return { command: "py", args: ["-3", path.join(root, "ground_station_server.py")] };
  }
  const localPython = firstExisting([
    path.join(root, ".venv", "bin", "python"),
    "/usr/bin/python3",
    "/usr/local/bin/python3"
  ]);
  return { command: localPython || "python3", args: [path.join(root, "ground_station_server.py")] };
}

function backendCommand(root) {
  const packagedServer = backendExecutablePath(root);
  if (fs.existsSync(packagedServer)) {
    return { command: packagedServer, args: [] };
  }
  return pythonCommand(root);
}

function normalizeForCompare(value) {
  return path.resolve(value || "").toLowerCase();
}

function isPathInside(childPath, parentPath) {
  const child = normalizeForCompare(childPath);
  const parent = normalizeForCompare(parentPath);
  return child === parent || child.startsWith(`${parent}${path.sep}`);
}

function listPackagedBackendProcesses(root) {
  if (process.platform !== "win32") return [];
  const script = [
    "$ErrorActionPreference='SilentlyContinue'",
    "$names=@('ground_station_server.exe','px6c_connector.exe','mavlink_simulator.exe')",
    "Get-CimInstance Win32_Process | Where-Object { $names -contains $_.Name } | Select-Object ProcessId,Name,ExecutablePath | ConvertTo-Json -Compress"
  ].join("; ");
  const result = spawnSync("powershell.exe", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], {
    windowsHide: true,
    encoding: "utf8",
    timeout: 6000
  });
  if (result.status !== 0 || !result.stdout.trim()) return [];
  try {
    const parsed = JSON.parse(result.stdout.trim());
    return (Array.isArray(parsed) ? parsed : [parsed])
      .filter((item) => item && PACKAGED_PROCESS_NAMES.has(String(item.Name || "").toLowerCase()))
      .filter((item) => item.ExecutablePath && isPathInside(item.ExecutablePath, root));
  } catch (_) {
    return [];
  }
}

function killProcessTree(pid) {
  if (!pid) return false;
  try {
    const result = spawnSync("taskkill", ["/PID", String(pid), "/T", "/F"], {
      windowsHide: true,
      stdio: "ignore",
      timeout: 6000
    });
    return result.status === 0;
  } catch (_) {
    return false;
  }
}

function cleanupStalePackagedBackendProcesses({ reason = "startup" } = {}) {
  const root = backendRoot();
  const processes = listPackagedBackendProcesses(root)
    .filter((item) => Number(item.ProcessId) !== process.pid)
    .filter((item) => !backendProcess || Number(item.ProcessId) !== Number(backendProcess.pid));
  const killed = [];
  for (const item of processes) {
    if (killProcessTree(item.ProcessId)) {
      killed.push({
        pid: Number(item.ProcessId),
        name: item.Name,
        path: item.ExecutablePath
      });
    }
  }
  if (killed.length) {
    try {
      fs.appendFileSync(
        logFile("desktop-cleanup.log"),
        `${new Date().toISOString()} ${reason} ${JSON.stringify(killed)}\n`,
        "utf8"
      );
    } catch (_) {
      // Cleanup logging is diagnostic only.
    }
  }
  return killed;
}

function logFile(name) {
  const logDir = path.join(userDataDir(), "logs");
  fs.mkdirSync(logDir, { recursive: true });
  return path.join(logDir, name);
}

function writeSmokeResult(payload) {
  try {
    fs.writeFileSync(path.join(userDataDir(), "smoke-test-result.json"), JSON.stringify(payload, null, 2), "utf8");
  } catch (_) {
    // Smoke test output should not block app startup or shutdown.
  }
}

function startBackend() {
  const root = backendRoot();
  const usesPackagedBackend = fs.existsSync(backendExecutablePath(root));
  const commandSpec = backendCommand(root);
  const env = {
    ...process.env,
    AUTO_OPEN: "0",
    PORT: String(config.backend.port || 8080),
    PYTHONUNBUFFERED: "1",
    GCS_DESKTOP: "1",
    GCS_DATA_DIR: userDataDir()
  };
  const frontendRoot = frontendResourceRoot(root);
  if (frontendRoot) {
    env.GCS_RESOURCE_ROOT = frontendRoot;
  }
  const stdout = fs.openSync(logFile("backend.out.log"), "a");
  const stderr = fs.openSync(logFile("backend.err.log"), "a");
  backendProcess = spawn(commandSpec.command, commandSpec.args, {
    cwd: root,
    env,
    windowsHide: true,
    stdio: ["ignore", stdout, stderr]
  });
  ownsBackend = true;
  backendProcess.on("exit", (code) => {
    if (code !== 0 && mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send("desktop-backend-exit", { code });
    }
    backendProcess = null;
  });
}

function probeUrl(url, timeoutMs = 700) {
  return new Promise((resolve) => {
    const request = http.get(`${url}api/version`, { timeout: timeoutMs }, (response) => {
      response.resume();
      resolve(response.statusCode >= 200 && response.statusCode < 500);
    });
    request.on("timeout", () => {
      request.destroy();
      resolve(false);
    });
    request.on("error", () => resolve(false));
  });
}

function backendCandidatePorts() {
  return [Number(config.backend.port || 8080), ...PORT_FALLBACKS]
    .filter((port, index, all) => Number.isFinite(port) && port > 0 && all.indexOf(port) === index);
}

async function findBackendUrl({ excludeUrls = new Set() } = {}) {
  for (const port of backendCandidatePorts()) {
    const url = `http://127.0.0.1:${port}/`;
    if (excludeUrls.has(url)) continue;
    if (await probeUrl(url)) return url;
  }
  return "";
}

async function snapshotBackendUrls() {
  const found = new Set();
  for (const port of backendCandidatePorts()) {
    const url = `http://127.0.0.1:${port}/`;
    if (await probeUrl(url)) found.add(url);
  }
  return found;
}

async function waitForBackend({ excludeUrls = new Set() } = {}) {
  const timeoutMs = Number(config.backend.readyTimeoutMs || 30000);
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const found = await findBackendUrl({ excludeUrls });
    if (found) return found;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  return "";
}

function fetchJson(url, options = {}) {
  return new Promise((resolve, reject) => {
    const request = http.request(url, options, (response) => {
      let body = "";
      response.setEncoding("utf8");
      response.on("data", (chunk) => { body += chunk; });
      response.on("end", () => {
        try {
          resolve({ statusCode: response.statusCode, data: body ? JSON.parse(body) : {} });
        } catch (error) {
          reject(error);
        }
      });
    });
    request.on("error", reject);
    if (options.body) request.write(options.body);
    request.end();
  });
}

async function getSessionToken() {
  try {
    const response = await fetchJson(`${backendUrl}api/status`);
    return response.data?.sessionToken || "";
  } catch (_) {
    return "";
  }
}

async function postBackend(pathname, payload = {}) {
  const token = await getSessionToken();
  const body = JSON.stringify(payload);
  return fetchJson(`${backendUrl}${pathname.replace(/^\//, "")}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Content-Length": Buffer.byteLength(body),
      "X-GCS-Token": token
    },
    body
  });
}

function connectionPayload() {
  const mavlink = config.mavlink || {};
  return {
    type: mavlink.type || "serial",
    serialPort: mavlink.serialPort || "",
    baud: Number(mavlink.baud || 115200),
    listenAddress: mavlink.listenAddress || "0.0.0.0",
    listenPort: Number(mavlink.listenPort || 14550),
    targetIp: mavlink.targetIp || "127.0.0.1",
    targetPort: Number(mavlink.targetPort || 14550)
  };
}

async function autoConnectMavlink({ force = false } = {}) {
  if (!backendUrl) return;
  if (!force && !config.mavlink?.autoConnect) return;
  const payload = connectionPayload();
  if (payload.type === "serial" && !payload.serialPort) {
    if (force) navigateToPage("connection");
    return;
  }
  try {
    await postBackend("/api/connection/start", payload);
  } catch (error) {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send("desktop-connection-error", { message: error.message });
    }
  }
}

function navigateToPage(pageName) {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  const safePage = String(pageName).replace(/[^a-zA-Z0-9_-]/g, "");
  mainWindow.webContents.executeJavaScript(
    `document.querySelector('[data-page="${safePage}"]')?.click(); true;`
  ).catch(() => {});
}

async function refreshMainWindow({ hard = false } = {}) {
  if (!mainWindow || mainWindow.isDestroyed()) return { ok: false, reason: "window_not_ready" };
  if (hard) {
    try {
      await mainWindow.webContents.session.clearCache();
    } catch (_) {
      // A failed cache clear should not prevent a reload.
    }
    mainWindow.webContents.reloadIgnoringCache();
  } else {
    mainWindow.webContents.reload();
  }
  return { ok: true, hard: Boolean(hard), backendUrl };
}

async function clearFrontendRuntimeCache() {
  const session = mainWindow?.webContents?.session;
  if (!session) return;
  try {
    await session.clearCache();
    await session.clearStorageData({ storages: ["serviceworkers", "cachestorage"] });
  } catch (_) {
    // Cache cleanup should never block flight operations startup.
  }
}

function buildMenu() {
  return Menu.buildFromTemplate([
    {
      label: "File",
      submenu: [
        {
          label: "Open Log",
          accelerator: "CmdOrCtrl+O",
          click: () => navigateToPage("tuning")
        },
        {
          label: "Export Report",
          accelerator: "CmdOrCtrl+E",
          click: () => navigateToPage("tuning")
        },
        {
          label: "Settings",
          accelerator: "CmdOrCtrl+,",
          click: () => shell.openPath(configPath())
        },
        { type: "separator" },
        { label: "Exit", role: "quit" }
      ]
    },
    {
      label: "Connection",
      submenu: [
        {
          label: "Connect PX4",
          accelerator: "CmdOrCtrl+L",
          click: () => autoConnectMavlink({ force: true })
        },
        {
          label: "Disconnect",
          click: () => postBackend("/api/connection/stop", {}).catch(() => {})
        },
        {
          label: "Select Port",
          click: () => navigateToPage("connection")
        }
      ]
    },
    {
      label: "Tools",
      submenu: [
        { label: "Parameter", click: () => navigateToPage("tuning") },
        { label: "Calibration", click: () => navigateToPage("aircraftCalibration") },
        { label: "Mission", click: () => navigateToPage("mission") },
        { label: "Actuator Test", click: () => navigateToPage("tuning") }
      ]
    },
    {
      label: "View",
      submenu: [
        {
          label: "Refresh UI",
          accelerator: "F5",
          click: () => refreshMainWindow()
        },
        {
          label: "Force Refresh UI",
          accelerator: "CmdOrCtrl+Shift+R",
          click: () => refreshMainWindow({ hard: true })
        },
        { type: "separator" },
        { role: "toggleDevTools" },
        { type: "separator" },
        { role: "resetZoom" },
        { role: "zoomIn" },
        { role: "zoomOut" },
        { type: "separator" },
        { role: "togglefullscreen" }
      ]
    },
    {
      label: "Help",
      submenu: [
        {
          label: "Open Runtime Data Folder",
          click: () => shell.openPath(userDataDir())
        },
        {
          label: "About AeroCentral",
          click: () => dialog.showMessageBox(mainWindow, {
            type: "info",
            title: `About ${APP_NAME}`,
            message: `${APP_NAME} Ground Control Station`,
            detail: `Backend: ${backendUrl || "not ready"}\nData: ${userDataDir()}\nPlatform: ${os.platform()} ${os.release()}`
          })
        }
      ]
    }
  ]);
}

ipcMain.handle("desktop-refresh-ui", (_event, options = {}) => refreshMainWindow(options));

async function createWindow() {
  loadDesktopConfig();
  mainWindow = new BrowserWindow({
    width: Number(config.window.width || 1440),
    height: Number(config.window.height || 960),
    minWidth: 1180,
    minHeight: 760,
    backgroundColor: "#071014",
    title: `${APP_NAME} Ground Control Station`,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false
    }
  });

  Menu.setApplicationMenu(buildMenu());
  await clearFrontendRuntimeCache();

  cleanupStalePackagedBackendProcesses({ reason: "startup" });
  const shouldReuseBackend = ALLOW_BACKEND_REUSE && !FORCE_OWN_BACKEND;
  const existingBackendUrls = await snapshotBackendUrls();
  backendUrl = shouldReuseBackend ? await findBackendUrl() : "";
  if (!backendUrl) {
    startBackend();
    backendUrl = await waitForBackend({ excludeUrls: existingBackendUrls });
  }

  if (!backendUrl) {
    await dialog.showMessageBox(mainWindow, {
      type: "error",
      title: "Backend failed to start",
      message: "AeroCentral backend service did not become ready.",
      detail: `Check logs in:\n${path.join(userDataDir(), "logs")}`
    });
    app.quit();
    return;
  }

  await mainWindow.loadURL(`${backendUrl}?desktop=${Date.now()}`);
  autoConnectMavlink().catch(() => {});
}

function stopBackend() {
  if (!backendProcess || !ownsBackend) return;
  const pid = backendProcess.pid;
  try {
    if (process.platform === "win32" && pid) {
      spawnSync("taskkill", ["/PID", String(pid), "/T", "/F"], {
        windowsHide: true,
        stdio: "ignore",
        timeout: 5000
      });
    } else {
      backendProcess.kill();
    }
  } catch (_) {
    // Process may already be closed.
  }
  backendProcess = null;
  ownsBackend = false;
}

async function runSmokeTest() {
  loadDesktopConfig();
  cleanupStalePackagedBackendProcesses({ reason: "smoke-test" });
  const existingBackendUrls = await snapshotBackendUrls();
  backendUrl = ALLOW_BACKEND_REUSE && !FORCE_OWN_BACKEND ? await findBackendUrl() : "";
  const reusedExistingBackend = Boolean(backendUrl);
  if (!backendUrl) {
    startBackend();
    backendUrl = await waitForBackend({ excludeUrls: existingBackendUrls });
  }
  if (!backendUrl) {
    const payload = {
      ok: false,
      error: "backend_not_ready",
      logs: path.join(userDataDir(), "logs")
    };
    writeSmokeResult(payload);
    console.error(JSON.stringify(payload));
    stopBackend();
    app.exit(1);
    return;
  }
  try {
    const version = await fetchJson(`${backendUrl}api/version`);
    const payload = {
      ok: true,
      backendUrl,
      configPath: configPath(),
      configuredPort: config.backend?.port,
      reusedExistingBackend,
      ownsBackend,
      version: version.data || {}
    };
    writeSmokeResult(payload);
    console.log(JSON.stringify(payload));
    stopBackend();
    app.exit(0);
  } catch (error) {
    const payload = {
      ok: false,
      backendUrl,
      error: error.message
    };
    writeSmokeResult(payload);
    console.error(JSON.stringify(payload));
    stopBackend();
    app.exit(1);
  }
}

app.whenReady().then(() => {
  if (!isPrimaryInstance) return;
  if (SMOKE_TEST) {
    runSmokeTest();
  } else {
    createWindow();
  }
});

app.on("before-quit", () => {
  if (ownsBackend && backendUrl) {
    postBackend("/api/connection/stop", {}).catch(() => {});
  }
  stopBackend();
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});
