const $ = (selector, scope = document) => scope.querySelector(selector);
const $$ = (selector, scope = document) => [...scope.querySelectorAll(selector)];
const TELEMETRY_FETCH_INTERVAL_MS = 33;
const TELEMETRY_SCRIPT_INTERVAL_MS = 50;
const CONNECTION_STATUS_INTERVAL_MS = 1000;
const LOGGING_STATUS_INTERVAL_MS = 1500;
const VERSION_POLL_INTERVAL_MS = 30000;
const RC_SETUP_REFRESH_INTERVAL_MS = 50;
const MAP_VISUAL_UPDATE_INTERVAL_MS = 500;
const MAP_AUTO_FOLLOW_INTERVAL_MS = 1200;
const CHART_REDRAW_INTERVAL_MS = 1000;
const LIVE_NUMERIC_COMMIT_INTERVAL_MS = 50;
const TELEMETRY_OFFLINE_GRACE_MS = 2500;
const OPERATOR_NAME_KEY = "uav-gcs-operator-name";
const MAX_ALERT_HISTORY = 80;
let activePageName = "overview";
let telemetryTransportMode = "boot";
const DEV_TOOLS_KEY = "uav-gcs-dev-tools";
const DEV_TOOLS_ENABLED = resolveDevToolsEnabled();
document.documentElement.classList.toggle("dev-tools-enabled", DEV_TOOLS_ENABLED);

function resolveNode(target) {
  return typeof target === "string" ? $(target) : target;
}

function setTextIfChanged(target, value) {
  const node = resolveNode(target);
  if (!node) return false;
  const text = value === null || value === undefined ? "" : String(value);
  if (node.textContent === text) return false;
  node.textContent = text;
  return true;
}

function setHtmlIfChanged(target, value) {
  const node = resolveNode(target);
  if (!node) return false;
  const html = value === null || value === undefined ? "" : String(value);
  if (node.innerHTML === html) return false;
  node.innerHTML = html;
  return true;
}

function setExclusiveStateClass(target, states, state) {
  const node = resolveNode(target);
  if (!node) return;
  if (node.classList.contains(state) && states.every((item) => item === state || !node.classList.contains(item))) return;
  node.classList.remove(...states);
  node.classList.add(state);
}

const clock = $("#clock");
const formatTime = (date) => new Intl.DateTimeFormat("zh-CN", {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false
}).format(date);

function updateClock() {
  clock.textContent = formatTime(new Date());
}
updateClock();
setInterval(updateClock, 1000);

let elapsedSeconds = 0;
setInterval(() => {
  elapsedSeconds += 1;
  const h = String(Math.floor(elapsedSeconds / 3600)).padStart(2, "0");
  const m = String(Math.floor((elapsedSeconds % 3600) / 60)).padStart(2, "0");
  const s = String(elapsedSeconds % 60).padStart(2, "0");
  if ($("#elapsed")) $("#elapsed").textContent = `${h}:${m}:${s}`;
}, 1000);

function showToast(title = "操作成功", message = "设置已更新") {
  const toast = $("#toast");
  $("strong", toast).textContent = title;
  $("small", toast).textContent = message;
  toast.classList.add("show");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.remove("show"), 2600);
}

function initDesktopRefreshControls() {
  const desktop = window.aeroCentralDesktop;
  if (!desktop?.isDesktop || $("#desktopRefreshControls")) return;
  const actions = $(".topbar-actions");
  if (!actions) return;
  const group = document.createElement("div");
  group.id = "desktopRefreshControls";
  group.className = "desktop-refresh-controls";
  group.innerHTML = `
    <button type="button" id="desktopRefreshUi" title="刷新软件界面，快捷键 F5">刷新</button>
    <button type="button" id="desktopHardRefreshUi" title="强制清理缓存并刷新，快捷键 Ctrl+Shift+R">强制刷新</button>
  `;
  const anchor = $("#clock");
  actions.insertBefore(group, anchor || null);

  $("#desktopRefreshUi", group).addEventListener("click", async () => {
    showToast("正在刷新软件界面", "仅刷新 UI，不会重启后端或断开飞控连接");
    try {
      await desktop.refresh(false);
    } catch (_) {
      window.location.reload();
    }
  });
  $("#desktopHardRefreshUi", group).addEventListener("click", async () => {
    showToast("正在强制刷新", "清理 Electron 缓存后重新加载当前 UI");
    try {
      await desktop.refresh(true);
    } catch (_) {
      window.location.replace(`${window.location.pathname}?v=${Date.now()}`);
    }
  });
}

initDesktopRefreshControls();

function safeStorageGet(key) {
  try {
    return localStorage.getItem(key);
  } catch (_) {
    return null;
  }
}

function safeStorageSet(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch (_) {}
}

function safeStorageRemove(key) {
  try {
    localStorage.removeItem(key);
  } catch (_) {}
}

function resolveDevToolsEnabled() {
  const params = new URLSearchParams(window.location.search);
  if (params.get("dev") === "1" || params.get("devTools") === "1") {
    safeStorageSet(DEV_TOOLS_KEY, "1");
    return true;
  }
  if (params.get("dev") === "0" || params.get("devTools") === "0") {
    safeStorageRemove(DEV_TOOLS_KEY);
    return false;
  }
  return safeStorageGet(DEV_TOOLS_KEY) === "1";
}

function applyDevToolVisibility() {
  $$("[data-dev-only]").forEach((element) => {
    element.hidden = !DEV_TOOLS_ENABLED;
  });
}

applyDevToolVisibility();

let activeBuildSignature = "";
let versionRefreshPrompted = false;

function isLocalDevHost() {
  return ["localhost", "127.0.0.1", "::1"].includes(window.location.hostname);
}

function unregisterStaleServiceWorkers() {
  if (!isLocalDevHost()) return;
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.getRegistrations()
      .then((registrations) => registrations.forEach((registration) => registration.unregister()))
      .catch(() => {});
  }
  if ("caches" in window) {
    caches.keys()
      .then((keys) => keys
        .filter((key) => /workbox|precache|pwa|uav|gcs|ground/i.test(key))
        .forEach((key) => caches.delete(key)))
      .catch(() => {});
  }
}

function versionSignature(info = {}) {
  return [info.version, info.frontendHash, info.gitCommit].filter(Boolean).join("|");
}

function formatBuildTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "unknown";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false
  }).format(date);
}

function ensureBuildBadge() {
  let badge = $("#buildVersionBadge");
  if (badge) return badge;
  badge = document.createElement("span");
  badge.id = "buildVersionBadge";
  badge.className = "build-version-badge";
  badge.textContent = "Build --";
  const actions = $(".topbar-actions");
  const anchor = $("#clock");
  if (actions && anchor) actions.insertBefore(badge, anchor);
  else document.body.appendChild(badge);
  return badge;
}

async function fetchVersionInfo() {
  const response = await fetch(`/api/version?t=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`version ${response.status}`);
  return response.json();
}

function renderBuildVersion(info = {}) {
  const badge = ensureBuildBadge();
  const hash = info.frontendHash || info.version || "unknown";
  const commit = info.gitCommit || "unknown";
  badge.textContent = `Build ${String(hash).slice(0, 8)}`;
  badge.title = `前端版本: ${info.version || "unknown"}\n前端 hash: ${info.frontendHash || "unknown"}\n后端启动: ${formatBuildTime(info.buildTime)}\nGit: ${commit}`;
}

function showVersionRefreshNotice(info = {}) {
  if (versionRefreshPrompted) return;
  versionRefreshPrompted = true;
  let banner = $("#versionRefreshBanner");
  if (!banner) {
    banner = document.createElement("div");
    banner.id = "versionRefreshBanner";
    banner.className = "version-refresh-banner";
    banner.innerHTML = `
      <strong>检测到地面站新版本</strong>
      <span>后端或前端版本已变化，建议刷新以避免旧 UI 缓存。</span>
      <button type="button">刷新</button>
    `;
    document.body.appendChild(banner);
    $("button", banner).addEventListener("click", () => {
      window.location.replace(`${window.location.pathname}?v=${Date.now()}`);
    });
  }
  banner.hidden = false;
  renderBuildVersion(info);
}

async function initBuildVersion() {
  unregisterStaleServiceWorkers();
  try {
    const info = await fetchVersionInfo();
    activeBuildSignature = versionSignature(info);
    renderBuildVersion(info);
  } catch (error) {
    ensureBuildBadge().textContent = "Build offline";
  }
  setInterval(async () => {
    try {
      const info = await fetchVersionInfo();
      const signature = versionSignature(info);
      if (activeBuildSignature && signature && signature !== activeBuildSignature) {
        showVersionRefreshNotice(info);
      } else {
        renderBuildVersion(info);
      }
    } catch (_) {}
  }, VERSION_POLL_INTERVAL_MS);
}

initBuildVersion();

function normalizeOperatorName(name) {
  const trimmed = String(name || "").trim();
  return trimmed || "指挥员";
}

function operatorInitial(name) {
  const value = normalizeOperatorName(name);
  const first = [...value][0] || "指";
  return /[a-z]/i.test(first) ? first.toUpperCase() : first;
}

function currentOperatorName() {
  return normalizeOperatorName(safeStorageGet(OPERATOR_NAME_KEY) || $("#operatorName")?.textContent);
}

function escapeAttribute(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  }[char]));
}

function applyOperatorName(name) {
  const normalized = normalizeOperatorName(name);
  $("#operatorName").textContent = normalized;
  $("#operatorAvatar").textContent = operatorInitial(normalized);
  return normalized;
}

applyOperatorName(currentOperatorName());

const alertHistory = [];
const acknowledgedAlertIds = new Set();

function alertHash(text) {
  let hash = 0;
  for (const char of String(text || "")) {
    hash = ((hash << 5) - hash + char.charCodeAt(0)) | 0;
  }
  return `px4-${Math.abs(hash)}`;
}

function normalizeWarningItem(item) {
  if (item === null || item === undefined) return null;
  if (typeof item === "object") {
    const text = item.text || item.message || item.warning || "";
    if (!String(text).trim()) return null;
    return {
      text: String(text).trim(),
      severity: item.severity || item.level || "",
      source: item.source || "PX4 STATUSTEXT",
    };
  }
  const text = String(item).trim();
  return text ? { text, severity: "", source: "PX4 STATUSTEXT" } : null;
}

function classifyWarning(warning) {
  const text = `${warning.severity || ""} ${warning.text}`.toLowerCase();
  if (/critical|emergency|failsafe|failed|failure|error|denied|battery|power|prearm|crash|严重|失败|拒绝|低电|电池|失控|解锁失败/.test(text)) {
    return { level: "critical", icon: "!" };
  }
  if (/warn|warning|timeout|gps|ekf|mag|compass|rc|link|sensor|vibration|告警|超时|罗盘|磁|卫星|传感器|链路|遥控/.test(text)) {
    return { level: "warning", icon: "!" };
  }
  return { level: "info", icon: "i" };
}

function addRealtimeWarning(item) {
  const warning = normalizeWarningItem(item);
  if (!warning) return false;
  const id = alertHash(`${warning.source}:${warning.text}`);
  const existing = alertHistory.find((entry) => entry.id === id);
  if (existing) {
    const now = Date.now();
    if (now - existing.lastSeen < 1000) return false;
    existing.lastSeen = now;
    existing.count += 1;
    return true;
  }
  const classified = classifyWarning(warning);
  alertHistory.unshift({
    id,
    text: warning.text,
    source: warning.source,
    level: classified.level,
    icon: classified.icon,
    firstSeen: Date.now(),
    lastSeen: Date.now(),
    count: 1,
  });
  if (alertHistory.length > MAX_ALERT_HISTORY) alertHistory.length = MAX_ALERT_HISTORY;
  return true;
}

function renderRealtimeAlerts(force = false) {
  const list = $(".alert-list");
  if (!list) return;
  const pending = alertHistory.filter((item) => !acknowledgedAlertIds.has(item.id));
  const signature = pending.map((item) => `${item.id}:${item.count}`).join("|");
  if (!force && signature === renderRealtimeAlerts.lastSignature) return;
  renderRealtimeAlerts.lastSignature = signature;
  list.innerHTML = `<div class="alert-empty" id="alertEmpty">当前无告警</div>` + pending.map((item) => `
    <div class="alert-item ${item.level}" data-alert-id="${escapeAttribute(item.id)}">
      <span class="alert-icon">${item.icon}</span>
      <div>
        <strong>${escapeHtml(item.text)}</strong>
        <p>${escapeHtml(item.source)}${item.count > 1 ? ` · 重复 ${item.count} 次` : ""}</p>
        <time>${new Date(item.lastSeen).toLocaleTimeString("zh-CN", { hour12: false })}</time>
      </div>
      <button title="确认告警">✓</button>
    </div>`).join("");
  refreshAlertCount();
}
renderRealtimeAlerts.lastSignature = "";

function updateRealtimeAlerts(warnings) {
  let changed = false;
  (Array.isArray(warnings) ? warnings : []).forEach((warning) => {
    changed = addRealtimeWarning(warning) || changed;
  });
  if (changed) renderRealtimeAlerts();
}

function refreshAlertCount() {
  const count = $$(".alert-item").length;
  $$(".alert-count").forEach((item) => item.textContent = count);
  const navBadge = $(".nav-badge");
  if (navBadge) {
    navBadge.textContent = count;
    navBadge.style.display = count ? "" : "none";
  }
  const alertEmpty = $("#alertEmpty");
  if (alertEmpty) alertEmpty.hidden = count > 0;
}

function acknowledge(alert) {
  if (!alert) return;
  const id = alert.dataset.alertId;
  if (id) acknowledgedAlertIds.add(id);
  alert.classList.add("acknowledged");
  setTimeout(() => {
    renderRealtimeAlerts(true);
  }, 250);
  showToast("告警已确认", "这是 UI 处理确认，不会向飞控发送 ACK");
}

$(".alert-list").addEventListener("click", (event) => {
  const button = event.target.closest(".alert-item button");
  if (button) acknowledge(button.closest(".alert-item"));
});
refreshAlertCount();

$("#ackAll").addEventListener("click", () => {
  const alerts = $$(".alert-item");
  if (!alerts.length) {
    showToast("暂无待处理告警", "当前系统状态良好");
    return;
  }
  alerts.forEach((alert, index) => setTimeout(() => acknowledge(alert), index * 90));
});

function focusPanel(selector) {
  const panel = $(selector);
  if (!panel) return;
  panel.scrollIntoView({ behavior: "smooth", block: "center" });
  panel.classList.remove("focus-flash");
  requestAnimationFrame(() => panel.classList.add("focus-flash"));
}

function showPage(name) {
  activePageName = name;
  document.body.classList.toggle("flight-ops-active", name === "overview");
  $$(".page-view").forEach((page) => page.classList.remove("active"));
  const target = $(`#${name}Page`);
  if (target) target.classList.add("active");
  window.scrollTo({ top: 0, behavior: "smooth" });
  if (name === "mission") activateMissionPlanner();
  if (name === "risk") renderRiskPage();
  if (name === "feasibility") renderFeasibilityPage();
  if (name === "geofence") {
    initializeGeofenceMap();
    renderGeofencePage();
  }
  if (name === "rcSetup") refreshRcSetup();
  if (name === "aircraftCalibration") refreshAircraftCalibration(true);
}

function installFlightOpsEnhancements() {
  const tuningNav = $('.nav-item[data-page="tuning"]');
  if (tuningNav && !$('.nav-item[data-page="aircraftCalibration"]')) {
    tuningNav.insertAdjacentHTML("afterend", `
      <button class="nav-item" data-page="aircraftCalibration" title="飞机校准">
        <span class="icon">◎</span><span>飞机校准</span>
      </button>
    `);
  }
  const topbarActions = $(".topbar-actions");
  const newMissionButton = $("#newMission");
  if (topbarActions && !$("#armToggleButton")) {
    (newMissionButton || topbarActions).insertAdjacentHTML(newMissionButton ? "beforebegin" : "beforeend", `
      <button class="arm-button locked" id="armToggleButton" title="通过 MAVLink 向飞控发送 Arm/Disarm">
        <span id="armToggleState">未解锁</span>
      </button>
    `);
  }

  const connectionNav = $('.nav-item[data-page="connection"]');
  if (connectionNav && !$('.nav-item[data-page="rcSetup"]')) {
    connectionNav.insertAdjacentHTML("beforebegin", `
      <button class="nav-item" data-page="rcSetup" title="RC 设置">
        <span class="icon">RC</span><span>RC 设置</span>
      </button>
    `);
  }
  if (connectionNav && !$('.nav-item[data-page="gcsDiagnostic"]')) {
    connectionNav.insertAdjacentHTML("beforebegin", `
      <button class="nav-item" data-page="gcsDiagnostic" title="GCS 链路诊断">
        <span class="icon">↔</span><span>链路诊断</span>
      </button>
    `);
  }
  if (connectionNav && !$('.nav-item[data-page="connectionCheck"]')) {
    connectionNav.insertAdjacentHTML("beforebegin", `
      <button class="nav-item" data-page="connectionCheck" title="连接自检">
        <span class="icon">✓</span><span>连接自检</span>
      </button>
    `);
  }

  const connectionPage = $("#connectionPage");
  const tuningPage = $("#tuningPage");
  if (tuningPage && !$("#aircraftCalibrationPage")) {
    tuningPage.insertAdjacentHTML("beforebegin", `
      <section class="page-view operational-page aircraft-calibration-page" id="aircraftCalibrationPage">
        <div class="page-heading">
          <div>
            <p class="eyebrow">系统设置 / 飞机</p>
            <h2>飞机校准中心</h2>
            <p>独立于遥控器校准的飞机校准：陀螺仪、加速度计、磁罗盘、水平姿态、空速、电源与测试安全框架</p>
          </div>
          <div class="aircraft-calibration-actions">
            <span class="mode-badge" id="aircraftCalibrationMode">等待状态</span>
            <button class="ghost-button" id="aircraftCalibrationRefresh">刷新</button>
            <button class="ghost-button danger-lite" id="aircraftCalibrationForceRefresh">强制刷新/清除状态</button>
          </div>
        </div>
        <div class="safety-banner aircraft-calibration-warning">
          飞机校准必须在未解锁、拆除螺旋桨、地面安全环境下执行。AI 不会触发校准命令；电源、执行器、电机模块当前默认只读。
        </div>
        <article class="panel aircraft-calibration-status-panel">
          <div class="panel-header">
            <div><h3>校准前链路状态</h3><p>必须识别目标系统 / 目标组件，并持续发送地面站心跳</p></div>
            <span class="health-badge offline" id="aircraftCalibrationSafety">禁止</span>
          </div>
          <div class="aircraft-calibration-link-grid" id="aircraftCalibrationLinkGrid"></div>
        </article>
        <div class="aircraft-calibration-layout">
          <article class="panel aircraft-calibration-catalog">
            <div class="panel-header">
              <div><h3>校准状态总览</h3><p>状态只来自真实遥测、命令确认、飞控文本或明确的模拟模式，不伪造成功</p></div>
            </div>
            <div class="aircraft-calibration-card-grid" id="aircraftCalibrationCards"></div>
          </article>
          <article class="panel aircraft-calibration-detail">
            <div class="panel-header">
              <div><h3 id="aircraftCalibrationTitle">选择校准项目</h3><p id="aircraftCalibrationSubtitle">选择左侧卡片查看流程和安全条件</p></div>
              <span class="health-badge" id="aircraftCalibrationKind">--</span>
            </div>
            <div class="aircraft-calibration-detail-grid">
              <div>
                <small>当前类型</small>
                <strong id="aircraftCalibrationSelectedType">--</strong>
              </div>
              <div>
                <small>状态来源</small>
                <strong id="aircraftCalibrationBasis">--</strong>
              </div>
              <div>
                <small>真实 MAVLink</small>
                <strong id="aircraftCalibrationMavlink">--</strong>
              </div>
              <div>
                <small>可执行</small>
                <strong id="aircraftCalibrationExecutable">--</strong>
              </div>
            </div>
            <div class="aircraft-calibration-section">
              <h4>操作说明</h4>
              <ul id="aircraftCalibrationInstructions"></ul>
            </div>
            <div class="aircraft-calibration-section" id="aircraftCalibrationPoseSection" hidden>
              <h4>加速度计六面姿态</h4>
              <div class="aircraft-calibration-poses" id="aircraftCalibrationPoses"></div>
            </div>
            <div class="aircraft-calibration-section">
              <h4>安全确认</h4>
              <div class="aircraft-calibration-checks" id="aircraftCalibrationChecks"></div>
              <label class="aircraft-calibration-confirm">确认文本
                <input id="aircraftCalibrationConfirmation" placeholder="请输入：已确认安全">
              </label>
              <label class="aircraft-calibration-mock" data-dev-only>
                <input type="checkbox" id="aircraftCalibrationMock"> 模拟模式：未连接真实飞控，仅用于界面测试
              </label>
            </div>
            <div class="button-row">
              <button class="primary-button" id="aircraftCalibrationStart">开始校准</button>
              <button class="ghost-button" id="aircraftCalibrationRetry">重试</button>
              <button class="ghost-button" id="aircraftCalibrationCancel">取消会话</button>
            </div>
          </article>
        </div>
        <div class="aircraft-calibration-layout lower">
          <article class="panel">
            <div class="panel-header"><div><h3>命令确认 / 飞控文本</h3><p>显示当前校准会话与飞控最近文本状态</p></div><span class="mode-badge" id="aircraftCalibrationSessionBadge">未开始</span></div>
            <div class="aircraft-calibration-log" id="aircraftCalibrationLog">尚未开始飞机校准会话。</div>
          </article>
          <article class="panel">
            <div class="panel-header"><div><h3>电源 / 执行器 / 电机框架</h3><p>第一阶段只读诊断，避免误转电机或自动改电源参数</p></div></div>
            <div class="aircraft-calibration-readonly" id="aircraftCalibrationReadonly"></div>
          </article>
        </div>
      </section>
    `);
  }
  if (!$("#aircraftCalibrationModal")) {
    document.body.insertAdjacentHTML("beforeend", `
      <div class="modal-backdrop aircraft-calibration-modal-backdrop" id="aircraftCalibrationModal" hidden>
        <section class="modal aircraft-calibration-modal" role="dialog" aria-modal="true" aria-labelledby="aircraftCalibrationModalTitle">
          <div class="modal-header">
            <div>
              <h3 id="aircraftCalibrationModalTitle">加速度计校准向导</h3>
              <p id="aircraftCalibrationModalSubtitle">根据 PX4 飞控文本依次完成六面姿态采样</p>
            </div>
            <button type="button" class="icon-button" id="aircraftCalibrationModalClose">×</button>
          </div>
          <div class="aircraft-calibration-guide-status">
            <span class="health-badge" id="aircraftCalibrationGuideBadge">等待开始</span>
            <strong id="aircraftCalibrationGuidePrompt">开始后请按飞控提示摆放飞机。</strong>
            <small id="aircraftCalibrationGuideNote">每个姿态放稳后不要扶着晃动，等待飞控接受当前姿态再换下一面。</small>
          </div>
          <div class="aircraft-calibration-progress">
            <div id="aircraftCalibrationGuideProgressBar"></div>
          </div>
          <div class="aircraft-calibration-guide-poses" id="aircraftCalibrationGuidePoses"></div>
          <div class="aircraft-calibration-guide-log" id="aircraftCalibrationGuideLog"></div>
          <div class="modal-actions">
            <button type="button" class="ghost-button" id="aircraftCalibrationModalDismiss">关闭窗口</button>
          </div>
        </section>
      </div>
    `);
  }
  if (connectionPage && !$("#rcSetupPage")) {
    connectionPage.insertAdjacentHTML("beforebegin", `
      <section class="page-view operational-page rc-setup-page" id="rcSetupPage">
        <div class="page-heading">
          <div><p class="eyebrow">SETUP / RADIO</p><h2>遥控器校准与映射</h2><p>独立 RC 设置页面，基于 MAVLink RC_CHANNELS 与 PX4 RC_MAP 参数</p></div>
          <button class="ghost-button" id="rcRefresh">刷新 RC 状态</button>
        </div>
        <div class="rc-setup-tabs">
          <button class="active" data-rc-tab="monitor">RC Monitor</button>
          <button data-rc-tab="wizard">Calibration Wizard</button>
        </div>
        <article class="panel rc-setup-panel" id="rcMonitorTab">
          <div class="panel-header"><div><h3>RC Monitor</h3><p>CH1-CH18 raw input、当前映射和诊断提示</p></div><span class="health-badge offline" id="rcSetupBadge">Disconnected</span></div>
          <div class="rc-link-status-panel" id="rcLinkStatusPanel">
            <div class="rc-link-head">
              <div><small>RC Link Status</small><strong id="rcLinkTitle">Unknown</strong></div>
              <span class="rc-link-pill grey" id="rcLinkQuality">Unknown</span>
            </div>
            <div class="rc-link-grid" id="rcLinkGrid"></div>
            <div class="rc-link-meta" id="rcLinkMeta"></div>
          </div>
          <div class="rc-status-grid" id="rcStatusGrid"></div>
          <div class="rc-source-strip" id="rcSourceStrip"></div>
          <div class="rc-mapped-grid" id="rcSetupMapped"></div>
          <div class="rc-table-wrap"><table class="data-table rc-table"><thead><tr><th>CH</th><th>功能</th><th>PWM</th><th>%</th><th>MIN</th><th>MAX</th><th>TRIM</th><th>REV</th><th>状态</th></tr></thead><tbody id="rcSetupChannels"></tbody></table></div>
          <div class="rc-warning-list" id="rcSetupWarnings"></div>
        </article>
        <article class="panel rc-setup-panel" id="rcWizardTab" hidden>
          <div class="panel-header"><div><h3>RC Calibration Wizard</h3><p>先预览 diff，人工确认后才写入 PX4 参数</p></div><span class="health-badge offline" id="rcWizardSafety">等待会话</span></div>
          <div class="rc-wizard-layout">
            <div class="rc-wizard-steps" id="rcWizardSteps"></div>
            <div class="rc-wizard-main">
              <h3 id="rcWizardTitle">Step 0 安全确认</h3>
              <p id="rcWizardInstruction">请确认拆除桨叶，飞控处于 Disarmed。本功能只校准遥控器输入，不发送控制命令。</p>
              <div class="rc-live-bars" id="rcWizardLiveBars"></div>
              <div class="rc-wizard-actions">
                <button class="primary-button" id="rcStartSession">开始 RC 校准会话</button>
                <button class="ghost-button" id="rcCaptureStep">采样当前步骤</button>
                <button class="ghost-button" id="rcNextStep">下一步</button>
                <button class="ghost-button" id="rcPreview">生成预览</button>
              </div>
              <div class="rc-preview-table" id="rcPreviewTable">尚未生成参数预览。</div>
              <div class="rc-write-row">
                <input id="rcApplyConfirmation" placeholder="写入请输入：确认写入RC参数">
                <button class="danger-button" id="rcApplyParams">写入 PX4</button>
                <input id="rcRestoreConfirmation" placeholder="恢复请输入：确认恢复RC参数">
                <button class="ghost-button" id="rcRestoreParams">恢复备份</button>
              </div>
              <div class="rc-warning-list" id="rcWizardMessage"></div>
            </div>
          </div>
        </article>
      </section>
    `);
  }
  if (connectionPage && !$("#gcsDiagnosticPage")) {
    connectionPage.insertAdjacentHTML("beforebegin", `
      <section class="page-view operational-page gcs-diagnostic-page" id="gcsDiagnosticPage">
        <article class="panel">
          <div class="panel-header">
            <div>
              <h3>GCS Link Diagnostic</h3>
              <p id="gcsDiagnosticSummary">等待 MAVLink 连接。后端会以 GCS 身份发送 HEARTBEAT。</p>
            </div>
            <span class="mavlink-monitor-badge offline" id="gcsDiagnosticBadge">Disconnected</span>
          </div>
          <div class="gcs-diagnostic-grid" id="gcsDiagnosticGrid"></div>
          <div class="rc-link-status-panel compact" id="gcsRcLinkStatusPanel">
            <div class="rc-link-head">
              <div><small>RC Link Status</small><strong id="gcsRcLinkTitle">Unknown</strong></div>
              <span class="rc-link-pill grey" id="gcsRcLinkQuality">Unknown</span>
            </div>
            <div class="rc-link-grid" id="gcsRcLinkGrid"></div>
            <div class="rc-link-meta" id="gcsRcLinkMeta"></div>
          </div>
          <div class="performance-diagnostic" id="performanceDiagnostic">
            <strong>Performance Diagnostic</strong>
            <div id="performanceDiagnosticGrid"></div>
          </div>
          <div class="gcs-safety-panel">
            <strong>安全边界</strong>
            <p>当前阶段 UI 不发送 RC_OVERRIDE / MANUAL_CONTROL，不做自动 Arm/Disarm；只发送 GCS heartbeat、消息请求、参数读取和人工触发的测试命令。</p>
          </div>
          <div class="arming-diagnostic-panel">
            <div class="panel-header compact">
              <div><h3>解锁失败诊断</h3><p>用于对照 QGC 的 Arming denied / Preflight Fail 原因</p></div>
            </div>
            <div class="arming-diagnostic-grid" id="armingDiagnosticGrid"></div>
            <div class="arming-reason" id="armingDiagnosticReason">等待飞控 STATUSTEXT 或 RC_CHANNELS。</div>
          </div>
        </article>
        <article class="panel">
          <div class="panel-header"><div><h3>STATUSTEXT / COMMAND_ACK</h3><p>最新 100 条飞控文本和命令确认</p></div></div>
          <div class="status-text-list" id="statusTextList"></div>
          <div class="command-ack-list" id="commandAckList"></div>
        </article>
      </section>
    `);
  }
  if (connectionPage && !$("#connectionCheckPage")) {
    connectionPage.insertAdjacentHTML("beforebegin", `
      <section class="page-view operational-page connection-check-page" id="connectionCheckPage">
        <article class="panel">
          <div class="panel-header">
            <div>
              <h3>连接自检</h3>
              <p>检查 MAVLink 心跳、消息频率、GPS、姿态、电池和命令链路</p>
            </div>
            <button class="ghost-button" id="runConnectionSelfCheck">立即自检</button>
          </div>
          <div class="self-check-summary" id="connectionSelfCheckSummary">
            <strong>等待自检</strong>
            <span>连接飞控后点击“立即自检”，系统会读取真实 MAVLink 状态。</span>
          </div>
          <div class="self-check-grid" id="connectionSelfCheckGrid"></div>
        </article>
      </section>
    `);
  }

  const workspaceGrid = $(".workspace-grid");
  const monitorHost = $(".workspace-side-stack") || workspaceGrid;
  if (monitorHost && !$("#mavlinkMonitorPanel")) {
    monitorHost.insertAdjacentHTML("beforeend", `
      <article class="panel mavlink-panel" id="mavlinkMonitorPanel">
        <div class="panel-header">
          <div>
            <h3>MAVLink 消息监视器</h3>
            <p id="mavlinkMonitorSummary">等待心跳与消息流</p>
          </div>
          <span class="mavlink-monitor-badge offline" id="mavlinkMonitorBadge">离线</span>
        </div>
        <div class="mavlink-monitor-list" id="mavlinkMonitorList"></div>
      </article>
    `);
  }

  $("#runConnectionSelfCheck")?.addEventListener("click", () => runConnectionSelfCheck(true));
  $("#aircraftCalibrationRefresh")?.addEventListener("click", () => refreshAircraftCalibration(true));
  $("#aircraftCalibrationForceRefresh")?.addEventListener("click", forceRefreshAircraftCalibration);
  $("#aircraftCalibrationStart")?.addEventListener("click", startAircraftCalibration);
  $("#aircraftCalibrationRetry")?.addEventListener("click", startAircraftCalibration);
  $("#aircraftCalibrationCancel")?.addEventListener("click", cancelAircraftCalibration);
  $("#aircraftCalibrationModalClose")?.addEventListener("click", closeAircraftCalibrationGuide);
  $("#aircraftCalibrationModalDismiss")?.addEventListener("click", closeAircraftCalibrationGuide);
  $("#aircraftCalibrationModal")?.addEventListener("click", (event) => {
    if (event.target?.id === "aircraftCalibrationModal") closeAircraftCalibrationGuide();
  });
  applyDevToolVisibility();
}

installFlightOpsEnhancements();
installFlightOperationsLayout();

function moveExistingElement(host, element) {
  if (!host || !element) return null;
  host.appendChild(element);
  return element;
}

function ensureTopFlightStatusBar() {
  const topbar = $(".topbar");
  if (!topbar || $("#topFlightStatusBar")) return;
  const actions = $(".topbar-actions", topbar);
  const status = document.createElement("div");
  status.id = "topFlightStatusBar";
  status.className = "top-flight-status-bar offline";
  status.innerHTML = `
    <div class="top-flight-pill connection"><small>LINK</small><strong id="topFlightConnection">Disconnected</strong></div>
    <div class="top-flight-pill mode"><small>MODE</small><strong id="topFlightMode">UNKNOWN</strong></div>
    <div class="top-flight-pill armed"><small>ARM</small><strong id="topFlightArmed">Disarmed</strong></div>
    <div class="top-flight-pill gps"><small>GPS</small><strong id="topFlightGps">Fix --</strong></div>
    <div class="top-flight-pill battery"><small>BAT</small><strong id="topFlightBattery">--%</strong></div>
    <div class="top-flight-pill datalink"><small>MAV</small><strong id="topFlightLink">-- Hz</strong></div>
    <div class="top-flight-pill rc"><small>RC</small><strong id="topFlightRc">Unknown</strong></div>
    <div class="top-flight-pill throttle top-throttle-status" id="topThrottleStatus" title="来自 RC_CHANNELS + RC_MAP_THROTTLE 的真实油门输入">
      <small>THR</small><strong id="topThrottleValue">--%</strong><span id="topThrottleSource">等待 RC</span>
    </div>
    <div class="top-flight-pill mission"><small>MISSION</small><strong id="topFlightMission">待规划</strong></div>
  `;
  topbar.insertBefore(status, actions || null);
}

function installFlightOperationsLayout() {
  const overview = $("#overviewPage");
  if (!overview || $("#flightOpsDeck")) return;
  document.body.classList.add("flight-ops-active");
  ensureTopFlightStatusBar();

  const deck = document.createElement("section");
  deck.id = "flightOpsDeck";
  deck.className = "flight-ops-deck";
  deck.innerHTML = `
    <div class="flight-ops-mission" aria-label="任务摘要"></div>
    <div class="flight-ops-map" aria-label="地图与轨迹"></div>
    <aside class="flight-ops-left" aria-label="飞行数据侧栏"></aside>
    <aside class="flight-ops-right" aria-label="飞行状态侧栏"></aside>
  `;

  const bottom = document.createElement("section");
  bottom.id = "flightOpsBottomStrip";
  bottom.className = "flight-ops-bottom-strip";

  overview.insertBefore(deck, overview.firstChild);
  deck.appendChild(bottom);

  const mission = $(".flight-ops-mission", deck);
  const left = $(".flight-ops-left", deck);
  const center = $(".flight-ops-map", deck);
  const right = $(".flight-ops-right", deck);
  const workspace = $(".workspace-grid", overview);
  const analysis = $(".analysis-grid", overview);

  moveExistingElement(mission, $(".mission-strip", overview));
  moveExistingElement(left, $(".metrics-grid", overview));

  moveExistingElement(center, $(".map-panel", overview));

  moveExistingElement(right, $(".alerts-panel", overview));
  moveExistingElement(right, $(".systems-panel", overview));
  moveExistingElement(right, $("#mavlinkMonitorPanel"));
  moveExistingElement(right, $("#commandEvidencePanel"));

  moveExistingElement(bottom, $(".chart-panel", overview));
  moveExistingElement(bottom, $(".attitude-panel", overview));
  moveExistingElement(bottom, $(".compass-panel", overview));
  moveExistingElement(bottom, $(".flight-readiness-strip", overview));
  moveExistingElement(bottom, $(".flight-hud", overview));

  workspace?.remove();
  analysis?.remove();

  $("#quickConnectionButton")?.addEventListener("click", () => showPage("connection"));
  window.setTimeout(() => {
    if (typeof gpsMap !== "undefined" && gpsMap?.invalidateSize) gpsMap.invalidateSize();
  }, 80);
}

function setTopFlightText(id, value) {
  setTextIfChanged(`#${id}`, value);
}

function updateTopFlightStatusBar(data = {}, online = false) {
  ensureTopFlightStatusBar();
  const bar = $("#topFlightStatusBar");
  if (!bar) return;
  const warnings = Array.isArray(data.warnings) ? data.warnings : [];
  const hasCritical = warnings.some((item) => classifyWarning(normalizeWarningItem(item) || { text: "" }).level === "critical");
  const fixType = finite(data.fixType) ? Number(data.fixType) : null;
  const sats = finite(data.satellites) ? Math.round(Number(data.satellites)) : null;
  const battery = finite(data.battery) ? Math.round(Number(data.battery)) : null;
  const rate = finite(data.rateHz) ? Number(data.rateHz) : null;
  const rc = data.rcLink || {};

  bar.classList.toggle("offline", !online);
  bar.classList.toggle("online", !!online && !hasCritical);
  bar.classList.toggle("critical", !!online && hasCritical);
  $(".topbar")?.classList.toggle("flight-critical", !!online && hasCritical);
  $(".topbar")?.classList.toggle("flight-offline", !online);

  setTopFlightText("topFlightConnection", online ? "Connected" : "Disconnected");
  setTopFlightText("topFlightMode", data.mode || "UNKNOWN");
  setTopFlightText("topFlightArmed", data.armed ? "Armed" : "Disarmed");
  setTopFlightText("topFlightGps", fixType === null && sats === null ? "Fix --" : `Fix ${fixType ?? "--"} · ${sats ?? "--"}星`);
  setTopFlightText("topFlightBattery", battery === null ? "--%" : `${battery}%`);
  setTopFlightText("topFlightLink", rate === null ? "-- Hz" : `${rate.toFixed(rate >= 10 ? 0 : 1)} Hz`);
  setTopFlightText("topFlightRc", rc.rc_status_text || rc.rc_signal_quality || "Unknown");
  const missionProgress = missionProgressFromTelemetry(data);
  setTopFlightText("topFlightMission", missionProgress.active
    ? `航点 #${missionProgress.currentSeq}${missionProgress.total ? "/" + missionProgress.total : ""}`
    : ($("#currentMissionTitle")?.textContent || "待规划"));
}

function updateFlightModePanel(data = {}, online = false) {
  const panel = $("#flightModePanel");
  const state = $("#flightModePanelState");
  const current = $("#flightModePanelCurrent");
  const armed = $("#flightModePanelArmed");
  if (!panel && !state && !current && !armed) return;

  const isRealCommand = currentOperationMode === "real_command";
  const mode = data.mode || "UNKNOWN";
  setTextIfChanged(current, online ? mode : "UNKNOWN");
  if (armed) {
    setTextIfChanged(armed, online ? (data.armed ? "已解锁" : "未解锁") : "未连接");
    armed.classList.toggle("armed", !!online && !!data.armed);
    armed.classList.toggle("disarmed", !!online && !data.armed);
  }
  if (state) {
    if (!online) {
      setTextIfChanged(state, "等待连接");
      setExclusiveStateClass(state, ["online", "warn", "offline"], "offline");
    } else if (isRealCommand) {
      setTextIfChanged(state, "实机可用");
      setExclusiveStateClass(state, ["online", "warn", "offline"], "online");
    } else {
      setTextIfChanged(state, "安全拦截");
      setExclusiveStateClass(state, ["online", "warn", "offline"], "warn");
    }
  }
  panel?.classList.toggle("offline", !online);
}

const RC_WIZARD_STEPS = [
  { key: "safety", title: "Step 0 安全确认", instruction: "请确认拆除桨叶，飞控处于 Disarmed。本功能只校准遥控器输入，不发送控制命令。" },
  { key: "detect", title: "Step 1 检测遥控器", instruction: "确认收到 RC_CHANNELS，至少 5 路有效通道。" },
  { key: "center", title: "Step 2 中位采样", instruction: "松开所有摇杆，油门保持最低，点击采样。" },
  { key: "roll", title: "Step 3 识别 Roll", instruction: "左右推动 Roll 摇杆到全行程，再回中，然后采样。" },
  { key: "pitch", title: "Step 4 识别 Pitch", instruction: "前后推动 Pitch 摇杆到全行程，再回中，然后采样。" },
  { key: "throttle", title: "Step 5 识别 Throttle", instruction: "油门从最低推到最高，再回最低，然后采样。" },
  { key: "yaw", title: "Step 6 识别 Yaw", instruction: "左右推动 Yaw 摇杆到全行程，再回中，然后采样。" },
  { key: "ranges", title: "Step 7 采集 min/max", instruction: "移动所有摇杆、开关、旋钮到全行程，持续采样。" },
  { key: "flightMode", title: "Step 8 识别 Flight Mode", instruction: "拨动飞行模式开关所有档位，然后采样。" },
  { key: "armSwitch", title: "Step 9 识别 Arm Switch", instruction: "拨动 Arm switch。如果不用 Arm switch，可后续手动设为 0。" },
  { key: "preview", title: "Step 10 结果预览", instruction: "检查即将写入的参数 diff，不确认不会写入。" },
  { key: "apply", title: "Step 11 写入 PX4", instruction: "输入确认文本后才会逐项写入，并重新读取验证。" },
  { key: "verify", title: "Step 12 校准完成验证", instruction: "重新拨动摇杆，确认映射和方向正确。" },
];
let rcSetupState = null;
let rcSessionId = null;
let rcStepIndex = 0;
let rcSampleBuffer = [];
let rcSamplingTimer = null;
let rcSetupRefreshBusy = false;
let rcSetupRefreshTimer = null;
let rcSetupLastRefreshAt = 0;
let rcSetupRefreshHz = 0;

function rcPercent(pwm, min = 1000, max = 2000) {
  const value = Number(pwm);
  if (!Number.isFinite(value)) return null;
  const span = Math.max(1, Number(max || 2000) - Number(min || 1000));
  return Math.max(0, Math.min(100, (value - Number(min || 1000)) / span * 100));
}

const RC_FUNCTION_LABELS = {
  roll: "横滚 Roll",
  pitch: "俯仰 Pitch",
  throttle: "油门 Throttle",
  yaw: "航向 Yaw",
  flightMode: "飞行模式",
  armSwitch: "解锁开关",
};

function rcChannelFunctionMap(mapped = {}) {
  const byChannel = new Map();
  Object.entries(mapped || {}).forEach(([key, item]) => {
    const channel = Number(item?.channel);
    if (!Number.isFinite(channel) || channel <= 0) return;
    const labels = byChannel.get(channel) || [];
    labels.push(RC_FUNCTION_LABELS[key] || key);
    byChannel.set(channel, labels);
  });
  return byChannel;
}

function rcChannelFunctionLabel(item, channelFunctions) {
  if (item?.function) return item.function;
  const channel = Number(item?.channel);
  const labels = channelFunctions.get(channel);
  if (labels?.length) return labels.join(" / ");
  if (item?.used) return "辅助通道 / 开关";
  return "未映射";
}

function updateTopThrottle(data = {}) {
  const percent = Number(data.rcThrottlePercent);
  const pwm = data.rcThrottlePwm;
  const value = $("#topThrottleValue");
  const source = $("#topThrottleSource");
  const host = $("#topThrottleStatus");
  const valid = Number.isFinite(percent);
  setTextIfChanged(value, valid ? `${Math.round(percent)}%` : "--%");
  if (source) {
    setTextIfChanged(source, valid
      ? `${pwm || "--"} us · ${data.rcThrottleSource || "RC_MAP_THROTTLE"}`
      : "等待 RC_MAP/RC");
  }
  if (host) host.classList.toggle("offline", !valid);
}

function updateArmButtonState(data = {}) {
  const button = $("#armToggleButton");
  const label = $("#armToggleState");
  if (!button || !label) return;
  const armed = !!data.armed;
  button.classList.toggle("armed", armed);
  button.classList.toggle("locked", !armed);
  button.dataset.armTarget = armed ? "disarm" : "arm";
  setTextIfChanged(label, armed ? "上锁飞机" : "解锁飞机");
  const connected = !!(data.connected || data.heartbeat);
  button.disabled = !connected;
  button.title = connected
    ? (armed ? "发送 Disarm 上锁命令" : "发送 Arm 解锁命令")
    : "等待 MAVLink 飞控连接";
}

function fmtRcValue(value, suffix = "") {
  if (value === null || value === undefined || value === "") return "N/A";
  const number = Number(value);
  if (Number.isFinite(number)) return `${number % 1 ? number.toFixed(1) : Math.round(number)}${suffix}`;
  return `${value}${suffix}`;
}

function fmtRcAge(ms) {
  const value = Number(ms);
  if (!Number.isFinite(value)) return "N/A";
  return value < 1000 ? `${Math.round(value)} ms ago` : `${(value / 1000).toFixed(1)} s ago`;
}

function renderRcLinkStatus(rcLink = {}, prefix = "rc") {
  const title = $(`#${prefix}LinkTitle`);
  const quality = $(`#${prefix}LinkQuality`);
  const grid = $(`#${prefix}LinkGrid`);
  const meta = $(`#${prefix}LinkMeta`);
  const panel = $(`#${prefix}LinkStatusPanel`);
  if (!grid && !meta && !title) return;
  const level = rcLink.ui_level || "grey";
  if (panel) {
    panel.classList.remove("green", "yellow", "red", "grey");
    panel.classList.add(level);
  }
  setTextIfChanged(title, rcLink.rc_status_text || "Unknown");
  if (quality) {
    setTextIfChanged(quality, rcLink.rc_signal_quality || "unknown");
    quality.className = `rc-link-pill ${level}`;
  }
  if (grid) {
    setHtmlIfChanged(grid, [
      ["RC Status", rcLink.rc_status_text || "Unknown"],
      ["RSSI", rcLink.rc_rssi_percent === null || rcLink.rc_rssi_percent === undefined ? "N/A" : `${fmtRcValue(rcLink.rc_rssi_percent, "%")} (${rcLink.rc_rssi_raw ?? "raw N/A"})`],
      ["Update Rate", rcLink.rc_update_rate_hz === null || rcLink.rc_update_rate_hz === undefined ? "N/A" : `${fmtRcValue(rcLink.rc_update_rate_hz, " Hz")}`],
      ["Last Update", fmtRcAge(rcLink.rc_last_update_age_ms)],
      ["Channel Count", rcLink.rc_channel_count ?? "N/A"],
      ["Failsafe", rcLink.failsafe?.rc_lost ? "Yes" : rcLink.rc_status === "unknown" ? "Unknown" : "No"],
      ["Throttle Low", rcLink.throttle_low === null || rcLink.throttle_low === undefined ? "Unknown" : rcLink.throttle_low ? "Yes" : "No"],
      ["Manual Control", rcLink.manual_control_available ? "Available" : "N/A"],
    ].map(([label, value]) => `<div class="rc-link-card"><small>${label}</small><strong>${value}</strong></div>`).join(""));
  }
  if (meta) {
    const warnings = Array.isArray(rcLink.warnings) ? rcLink.warnings : [];
    setHtmlIfChanged(meta, warnings.length
      ? warnings.map((item) => `<p>${escapeAttribute(item)}</p>`).join("")
      : "<p>RC 状态等待数据。</p>");
  }
}

function renderRcSourceStrip(rcLink = {}, data = {}) {
  const host = $("#rcSourceStrip");
  if (!host) return;
  setHtmlIfChanged(host, [
    ["当前 RC 数据来源", rcLink.rc_source || "unavailable"],
    ["当前 RC_MAP 参数状态", rcLink.rc_map_status || (data.rcMapAvailable ? "received" : "unavailable")],
    ["当前通道映射是否可信", rcLink.mapping_confidence || "no"],
    ["active fixed profile", rcLink.activeFixedProfile || "default"],
  ].map(([label, value]) => `<span><small>${label}</small><strong>${value}</strong></span>`).join(""));
}

function renderPerformanceDiagnostic(data = {}) {
  const host = $("#performanceDiagnosticGrid");
  if (!host) return;
  const stats = normalizeMessageStats(data.messageStats || data.connection?.messageStats || {});
  const rcLink = data.rcLink || {};
  const telemetryAge = data.receivedAt ? Date.now() - Number(data.receivedAt) : null;
  setHtmlIfChanged(host, [
    ["MAVLink message rate", `${fmtRcValue(data.rateHz ?? data.connection?.rateHz ?? 0, " Hz")}`],
    ["WebSocket publish rate", "fixed internal"],
    ["frontend FPS", "browser controlled"],
    ["last telemetry update age", fmtRcAge(telemetryAge)],
    ["packet loss", data.packetLoss ?? data.connection?.packetLoss ?? "N/A"],
    ["RC update rate", rcLink.rc_update_rate_hz === null || rcLink.rc_update_rate_hz === undefined ? "N/A" : `${fmtRcValue(rcLink.rc_update_rate_hz, " Hz")}`],
    ["active fixed profile", rcLink.activeFixedProfile || "default"],
    ["RC_CHANNELS rate", stats.rates?.RC_CHANNELS ? `${fmtRcValue(stats.rates.RC_CHANNELS, " Hz")}` : "N/A"],
  ].map(([label, value]) => `<span><small>${label}</small><strong>${value}</strong></span>`).join(""));
}

let aircraftCalibrationOverview = null;
let aircraftCalibrationSelected = "gyro";
let aircraftCalibrationSessionId = null;
let aircraftCalibrationBusy = false;
let aircraftCalibrationGuideSessionId = null;
let aircraftCalibrationGuideType = null;

const AIRCRAFT_ACCEL_POSES = [
  { key: "level", title: "水平正放", detail: "飞机放在水平面，机身稳定不晃动" },
  { key: "left", title: "左侧朝下", detail: "左翼或左侧机臂朝下，保持静止" },
  { key: "right", title: "右侧朝下", detail: "右翼或右侧机臂朝下，保持静止" },
  { key: "nose_down", title: "机头朝下", detail: "机头向下，固定后等待飞控采样" },
  { key: "nose_up", title: "机头朝上", detail: "机头向上，固定后等待飞控采样" },
  { key: "upside_down", title: "倒置", detail: "机身倒置放稳，等待飞控确认" },
];

function aircraftLevelText(level) {
  return {
    green: "正常",
    yellow: "需关注",
    red: "失败/禁止",
    blue: "进行中",
    grey: "未知",
  }[level] || "未知";
}

function aircraftStatusText(status) {
  return {
    calibrated: "已校准",
    available: "可用",
    running: "进行中",
    started: "进行中",
    queued: "已入队",
    waiting_connector: "等待连接服务",
    required: "需要校准",
    warning: "需关注",
    failed: "失败",
    rejected: "已拒绝",
    timeout: "超时",
    expired: "已过期",
    unsupported: "飞控不支持",
    interrupted: "已中断",
    partial: "部分完成",
    not_available: "不可用",
    completed: "已完成",
    cancelled: "已取消",
    sent_no_ack: "等待飞控文本",
    unknown: "未知",
  }[status] || status || "未知";
}

function aircraftTypeText(type) {
  return {
    gyro: "陀螺仪",
    accel: "加速度计",
    compass: "磁罗盘",
    level_horizon: "水平姿态",
    airspeed: "空速计",
    power: "电源 / 电池",
    actuator: "执行器",
    motor: "电机",
  }[type] || type || "--";
}

function aircraftBasisText(value) {
  return {
    COMMAND_ACK: "命令确认",
    STATUSTEXT: "飞控文本",
    MAG_CAL_PROGRESS: "磁罗盘进度",
    MAG_CAL_REPORT: "磁罗盘报告",
    "VFR_HUD/airspeed": "空速遥测",
    "airspeed parameters": "空速参数",
    SYS_STATUS: "系统状态",
    BATTERY_STATUS: "电池状态",
    parameters: "参数",
    SERVO_OUTPUT_RAW: "执行器输出",
    "safety confirmation": "安全确认",
  }[value] || value || "--";
}

function translateAircraftCalibrationText(text) {
  const value = String(text || "").trim();
  if (!value) return "";
  const lower = value.toLowerCase();
  const commandType = value.match(/'type':\s*'([^']+)'/)?.[1] || value.match(/"type":\s*"([^"]+)"/)?.[1];
  const ackText = value.match(/'resultText':\s*'([^']+)'/)?.[1] || value.match(/"resultText":\s*"([^"]+)"/)?.[1];
  if (ackText) {
    const label = aircraftTypeText(commandType === "magnetometer" ? "compass" : commandType);
    return ackText === "ACCEPTED" || ackText === "IN_PROGRESS"
      ? `飞控已确认${label}校准命令：${ackText}`
      : `${label}校准命令未被飞控确认：${ackText}`;
  }
  const replacements = [
    ["Preflight: GPS Speed Accuracy too low", "飞前检查：GPS 速度精度过低"],
    ["Preflight Fail: vertical velocity unstable", "飞前检查失败：垂直速度不稳定"],
    ["Preflight: GPS Horizontal Pos Error too high", "飞前检查：GPS 水平位置误差过大"],
    ["Preflight: GPS Horizontal Pos Drift too high", "飞前检查：GPS 水平位置漂移过大"],
    ["Preflight Fail: No connection to the ground contro", "飞前检查失败：地面站连接中断"],
    ["Preflight Fail: No connection to the ground control station", "飞前检查失败：地面站连接中断"],
    ["Preflight Fail: Found 0 compass (required: 1)", "飞前检查失败：未检测到磁罗盘，当前无法进行磁罗盘校准"],
    ["Preflight Fail: Missing FMU SD Card", "飞前检查失败：飞控未检测到 FMU SD 卡"],
    ["Preflight Fail: Airspeed invalid", "飞前检查失败：空速数据无效"],
    ["Preflight Fail: Attitude failure (roll)", "飞前检查失败：横滚姿态异常，请先完成传感器校准并保持静止"],
    ["Preflight Fail: Attitude failure (pitch)", "飞前检查失败：俯仰姿态异常，请先完成传感器校准并保持静止"],
    ["Preflight Fail: Accel 0 inconsistent - check cal", "飞前检查失败：加速度计 0 数据不一致，请重新完成加速度计校准"],
    ["command denied during calibration", "飞控拒绝命令：当前已有校准正在进行，请先完成当前校准"],
    ["GCS connection regained", "地面站连接已恢复"],
    ["command is queued locally; waiting for connector to send it to the flight controller", "命令已提交到连接程序，正在发送给飞控"],
    ["calibration command sent to flight controller; waiting for COMMAND_ACK", "校准命令已发送到飞控，正在等待命令确认"],
    ["Flight controller accepted gyro calibration command; follow PX4 prompts", "飞控已确认陀螺仪校准命令，请保持飞机静止并等待完成提示"],
    ["Flight controller accepted accelerometer calibration command; follow PX4 prompts", "飞控已确认加速度计校准命令，请按 PX4 提示摆放飞机"],
    ["Flight controller accepted compass calibration command; follow PX4 prompts", "飞控已确认磁罗盘校准命令，请按 PX4 提示旋转飞机"],
    ["Flight controller accepted level horizon calibration command; follow PX4 prompts", "飞控已确认水平姿态校准命令，请保持飞机稳定"],
    ["Flight controller accepted airspeed calibration command; follow PX4 prompts", "飞控已确认空速计校准命令，请按 PX4 提示操作"],
    ["Compass calibration accepted; follow PX4 rotate prompts", "飞控已确认磁罗盘校准，请按 PX4 提示旋转飞机"],
    ["Compass calibration did not report progress", "磁罗盘校准未返回进度"],
    ["Compass calibration still running or no final report", "磁罗盘校准仍在进行或尚未返回最终报告"],
    ["[cal] hold vehicle still on a pending side", "校准提示：请把飞机保持在当前待采样姿态，不要晃动"],
    ["[cal] detected rest position", "校准提示：飞控检测到静止姿态，请继续保持不动"],
    ["[cal] orientation detected", "校准提示：飞控已识别当前方向，请继续保持稳定"],
    ["[cal] rotate to a different orientation", "当前姿态采样完成，请换到下一个未完成姿态"],
    ["rotate to a different side", "当前姿态采样完成，请换到下一个未完成姿态"],
    ["rotate to a different orientation", "当前姿态采样完成，请换到下一个未完成姿态"],
    ["[cal] down side done", "下方朝下姿态已完成，请换到下一个未完成姿态"],
    ["[cal] up side done", "上方朝下姿态已完成，请换到下一个未完成姿态"],
    ["[cal] left side done", "左侧朝下姿态已完成，请换到下一个未完成姿态"],
    ["[cal] right side done", "右侧朝下姿态已完成，请换到下一个未完成姿态"],
    ["[cal] front side done", "机头朝下姿态已完成，请换到下一个未完成姿态"],
    ["[cal] back side done", "机尾朝下姿态已完成，请换到下一个未完成姿态"],
    ["all sides complete", "六面姿态采样完成，等待飞控保存结果"],
    ["vehicle moved", "校准失败：飞机移动过大，请重新开始并保持静止"],
    ["[cal] detected", "校准提示：飞控已识别当前姿态，请继续保持稳定"],
    ["[cal] rotate vehicle", "校准提示：请按飞控要求缓慢旋转飞机"],
    ["[cal] calibration done", "校准完成"],
    ["[cal] calibration failed", "校准失败"],
    ["MAG_CAL_PROGRESS", "磁罗盘校准进度"],
    ["MAG_CAL_REPORT SUCCESS", "磁罗盘校准完成"],
  ];
  const found = replacements.find(([source]) => lower.includes(source.toLowerCase()));
  if (found) return found[1];
  if (lower.includes("[cal] pending:")) {
    const sideNames = { back: "机尾朝下", front: "机头朝下", left: "左侧朝下", right: "右侧朝下", up: "上方朝下", down: "下方朝下" };
    const pending = value.split(":").slice(1).join(":").trim().split(/\s+/).filter(Boolean).map((item) => sideNames[item] || item);
    return `待完成姿态：${pending.join("、")}`;
  }
  const px4Progress = value.match(/\[cal\]\s*progress\s*<(\d+)>/i);
  if (px4Progress) return `校准进度：${px4Progress[1]}%`;
  const progress = value.match(/calibration progress:\s*([^/]+)/i);
  if (progress) return `校准进度：${progress[1].trim()}`;
  if (lower.includes("[cal] calibration started") || lower.includes("calibration started")) {
    if (lower.includes("gyro")) return "陀螺仪校准已开始，请保持飞机静止";
    if (lower.includes("accel")) return "加速度计校准已开始，请按 PX4 提示摆放飞机";
    if (lower.includes("mag") || lower.includes("compass")) return "磁罗盘校准已开始，请按 PX4 提示旋转飞机";
    return "校准已开始，请按 PX4 提示操作";
  }
  if (lower.includes("calibration done") || lower.includes("calibration complete") || lower.includes("calibration successful")) {
    return "校准完成";
  }
  if (lower.includes("calibration failed") || lower.includes("cal failed")) {
    return `校准失败：${value}`;
  }
  return value;
}

function aircraftCalibrationHasFlightControllerEvidence(session = {}) {
  return (session.messages || []).some((item) => {
    const source = String(item.source || "");
    const text = String(item.text || "");
    return /COMMAND_ACK|命令确认/i.test(source)
      || /飞控已确认|COMMAND_ACK|ACCEPTED|IN_PROGRESS|\[cal\]|calibration started|calibration progress/i.test(text);
  });
}

function aircraftCalibrationMessageClass(item = {}) {
  const level = String(item.level || "").toLowerCase();
  const text = translateAircraftCalibrationText(item.text || "").toLowerCase();
  if (["error", "critical", "alert", "emergency"].some((token) => level.includes(token))) return "warn";
  if (/失败|拒绝|过低|过大|中断|denied|fail|lost|too low|too high/.test(text)) return "warn";
  return "ok";
}

function aircraftCalibrationLatestPrompt(session = {}) {
  const messages = session.messages || [];
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const text = translateAircraftCalibrationText(messages[index]?.text || "");
    if (text) return text;
  }
  return translateAircraftCalibrationText(session.reason || "") || "等待飞控返回校准提示。";
}

function aircraftCalibrationProgressPercent(session = {}) {
  if (session.status === "completed") return 100;
  const texts = [
    session.reason || "",
    ...(session.messages || []).map((item) => item.text || ""),
  ];
  for (let index = texts.length - 1; index >= 0; index -= 1) {
    const value = String(texts[index] || "");
    const px4 = value.match(/\[cal\]\s*progress\s*<(\d+)>/i);
    if (px4) return Math.max(0, Math.min(100, Number(px4[1])));
    const translated = translateAircraftCalibrationText(value);
    const translatedProgress = translated.match(/校准进度：\s*(\d+)/);
    if (translatedProgress) return Math.max(0, Math.min(100, Number(translatedProgress[1])));
    const generic = value.match(/progress[:\s]+(\d+)/i);
    if (generic) return Math.max(0, Math.min(100, Number(generic[1])));
  }
  return session.status === "accepted" || session.status === "running" ? 5 : 0;
}

function openAircraftCalibrationGuide(type, session = null) {
  const modal = $("#aircraftCalibrationModal");
  if (!modal) return;
  aircraftCalibrationGuideType = type;
  aircraftCalibrationGuideSessionId = session?.session_id || session?.sessionId || aircraftCalibrationSessionId;
  modal.hidden = false;
  renderAircraftCalibrationGuide(session);
}

function closeAircraftCalibrationGuide() {
  const modal = $("#aircraftCalibrationModal");
  if (modal) modal.hidden = true;
}

function renderAircraftCalibrationGuide(session = null) {
  const modal = $("#aircraftCalibrationModal");
  if (!modal || modal.hidden) return;
  const type = session?.calibration_type || aircraftCalibrationGuideType || aircraftCalibrationSelected;
  const isAccel = type === "accel" || type === "accelerometer";
  const title = $("#aircraftCalibrationModalTitle");
  const subtitle = $("#aircraftCalibrationModalSubtitle");
  if (title) title.textContent = isAccel ? "加速度计校准向导" : `${aircraftTypeText(type)}校准向导`;
  if (subtitle) subtitle.textContent = isAccel
    ? "按 PX4 提示依次完成六面姿态采样"
    : "实时显示 PX4 校准提示和命令确认";

  const status = session?.status || "queued";
  const progress = aircraftCalibrationProgressPercent(session || {});
  const prompt = session ? aircraftCalibrationLatestPrompt(session) : "开始后请按飞控提示摆放飞机。";
  const badge = $("#aircraftCalibrationGuideBadge");
  if (badge) {
    const labels = {
      queued: "等待发送",
      accepted: "飞控已确认",
      running: "执行中",
      partial: "仍在进行",
      sent_no_ack: "等待飞控文本",
      completed: "完成",
      failed: "失败",
      rejected: "拒绝",
      timeout: "超时",
      expired: "过期",
    };
    badge.textContent = labels[status] || status || "等待开始";
    badge.classList.toggle("offline", ["failed", "rejected", "timeout", "expired"].includes(status));
  }
  if ($("#aircraftCalibrationGuidePrompt")) $("#aircraftCalibrationGuidePrompt").textContent = prompt;
  if ($("#aircraftCalibrationGuideNote")) {
    $("#aircraftCalibrationGuideNote").textContent = isAccel
      ? "每完成一个姿态后再换下一面。采样时不要拿在手里晃，也不要连续快速翻转。"
      : "请只按 PX4 文本提示操作，校准过程中保持飞机未解锁。";
  }
  if ($("#aircraftCalibrationGuideProgressBar")) {
    $("#aircraftCalibrationGuideProgressBar").style.width = `${progress}%`;
  }

  const poseHost = $("#aircraftCalibrationGuidePoses");
  if (poseHost) {
    const currentIndex = Math.min(AIRCRAFT_ACCEL_POSES.length - 1, Math.floor(progress / 17));
    poseHost.innerHTML = isAccel
      ? AIRCRAFT_ACCEL_POSES.map((pose, index) => {
        const done = status === "completed" || progress >= (index + 1) * 16;
        const active = !done && index === currentIndex && !["failed", "rejected", "timeout", "expired"].includes(status);
        return `
          <div class="aircraft-guide-pose ${done ? "done" : ""} ${active ? "active" : ""}">
            <span>${index + 1}</span>
            <strong>${escapeHtml(pose.title)}</strong>
            <small>${escapeHtml(pose.detail)}</small>
          </div>
        `;
      }).join("")
      : `<div class="aircraft-guide-pose active"><span>!</span><strong>等待 PX4 提示</strong><small>请查看上方当前提示和下方飞控文本。</small></div>`;
  }

  const logHost = $("#aircraftCalibrationGuideLog");
  if (logHost) {
    const messages = (session?.messages || []).slice(-8).reverse();
    logHost.innerHTML = messages.length
      ? messages.map((item) => `<div class="${aircraftCalibrationMessageClass(item)}">${escapeHtml(translateAircraftCalibrationText(item.text || ""))}</div>`).join("")
      : "<div>尚未收到 PX4 校准文本。</div>";
  }
}

function aircraftCardByType(type) {
  return (aircraftCalibrationOverview?.cards || []).find((item) => item.type === type)
    || (aircraftCalibrationOverview?.cards || [])[0]
    || null;
}

function renderAircraftCalibrationLinkGrid(data = {}) {
  const host = $("#aircraftCalibrationLinkGrid");
  if (!host) return;
  const heartbeat = data.gcsHeartbeat || {};
  const rows = [
    ["飞控连接", data.connected ? "已连接" : "未连接", data.connected ? "ok" : "warn"],
    ["地面站心跳", heartbeat.sending ? `${heartbeat.rateHz || 0} Hz` : "未发送", heartbeat.sending ? "ok" : "warn"],
    ["目标系统 ID", data.targetSystem ?? "--", data.targetIdentified ? "ok" : "warn"],
    ["目标组件 ID", data.targetComponent ?? "--", data.targetIdentified ? "ok" : "warn"],
    ["解锁状态", data.armed ? "已解锁" : "未解锁", data.armed ? "warn" : "ok"],
    ["PX4校准", data.activeCalibration?.active ? data.activeCalibration.text || "正在进行" : "空闲", data.activeCalibration?.active ? "warn" : "ok"],
  ];
  host.innerHTML = rows.map(([label, value, state]) => `
    <div class="aircraft-link-card ${state}">
      <small>${label}</small>
      <strong>${escapeHtml(value)}</strong>
    </div>
  `).join("");
  const safety = $("#aircraftCalibrationSafety");
  if (safety) {
    const ok = data.connected && data.targetIdentified && heartbeat.sending && !data.armed;
    safety.textContent = ok ? "允许校准" : "禁止校准";
    safety.classList.toggle("offline", !ok);
  }
  const mode = $("#aircraftCalibrationMode");
  if (mode) mode.textContent = data.commandMode === "real_command" ? "实机指令模式" : "非指令模式";
}

function renderAircraftCalibrationCards(data = {}) {
  const host = $("#aircraftCalibrationCards");
  if (!host) return;
  host.innerHTML = (data.cards || []).map((card) => `
    <button class="aircraft-calibration-card ${card.level || "grey"} ${card.type === aircraftCalibrationSelected ? "active" : ""}" data-aircraft-calibration-type="${card.type}">
      <span>${escapeHtml(card.title)}</span>
      <strong>${escapeHtml(aircraftStatusText(card.status))}</strong>
      <small>${escapeHtml(translateAircraftCalibrationText(card.reason || ""))}</small>
      <em>${card.mavlink ? "真实 MAVLink 命令" : "只读诊断 / 安全框架"}</em>
    </button>
  `).join("");
}

function renderAircraftCalibrationDetail() {
  const card = aircraftCardByType(aircraftCalibrationSelected);
  if (!card) return;
  aircraftCalibrationSelected = card.type;
  $("#aircraftCalibrationTitle").textContent = card.title;
  $("#aircraftCalibrationSubtitle").textContent = `${aircraftStatusText(card.status)} · ${translateAircraftCalibrationText(card.reason || "")}`;
  $("#aircraftCalibrationKind").textContent = aircraftLevelText(card.level);
  $("#aircraftCalibrationSelectedType").textContent = aircraftTypeText(card.type);
  $("#aircraftCalibrationBasis").textContent = (card.statusBasis || []).map(aircraftBasisText).join(" / ") || "--";
  $("#aircraftCalibrationMavlink").textContent = card.mavlink ? "真实 MAVLink 命令" : "只读诊断 / 安全框架";
  $("#aircraftCalibrationExecutable").textContent = card.executable ? "可执行" : "当前不可执行";
  if ($("#aircraftCalibrationStart")) $("#aircraftCalibrationStart").disabled = !card.executable;
  if ($("#aircraftCalibrationRetry")) $("#aircraftCalibrationRetry").disabled = !!aircraftCalibrationOverview?.activeCalibration?.active;
  $("#aircraftCalibrationInstructions").innerHTML = (card.instructions || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  const poseSection = $("#aircraftCalibrationPoseSection");
  const poseHost = $("#aircraftCalibrationPoses");
  const poses = card.poses || [];
  if (poseSection && poseHost) {
    poseSection.hidden = !poses.length;
    poseHost.innerHTML = poses.map((pose, index) => `<span>${index + 1}. ${escapeHtml(pose)}</span>`).join("");
  }
  const checks = [
    ["飞控连接", aircraftCalibrationOverview?.connected],
    ["地面站心跳", aircraftCalibrationOverview?.gcsHeartbeat?.sending],
    ["目标系统已识别", aircraftCalibrationOverview?.targetIdentified],
    ["飞机未解锁", !aircraftCalibrationOverview?.armed],
    ["真实命令可用", card.mavlink && card.executable],
  ];
  $("#aircraftCalibrationChecks").innerHTML = checks.map(([label, ok]) => `
    <span class="${ok ? "ok" : "warn"}"><b>${ok ? "通过" : "未通过"}</b>${escapeHtml(label)}</span>
  `).join("");
}

function renderAircraftReadonly(data = {}) {
  const host = $("#aircraftCalibrationReadonly");
  if (!host) return;
  const power = data.power || {};
  const cards = [
    ["电压", power.voltage === null || power.voltage === undefined ? "-- V" : `${Number(power.voltage).toFixed(2)} V`, power.voltageValid],
    ["电流", power.current === null || power.current === undefined ? "-- A" : `${Number(power.current).toFixed(2)} A`, power.currentValid],
    ["电池余量", power.batteryPercent === null || power.batteryPercent === undefined ? "-- %" : `${power.batteryPercent}%`, power.batteryPercent !== null && power.batteryPercent !== undefined],
    ["执行器/电机", "默认只读，不主动输出", true],
  ];
  host.innerHTML = cards.map(([label, value, ok]) => `
    <div class="${ok ? "ok" : "warn"}"><small>${escapeHtml(label)}</small><strong>${escapeHtml(value)}</strong></div>
  `).join("") + (power.warnings?.length ? `<p>${power.warnings.map(escapeHtml).join("；")}</p>` : "<p>电源参数不会被自动修改。</p>");
}

function renderAircraftCalibrationLog(session = null) {
  const host = $("#aircraftCalibrationLog");
  const badge = $("#aircraftCalibrationSessionBadge");
  if (!host) return;
  if (!session) {
    host.textContent = "尚未开始飞机校准会话。";
    if (badge) badge.textContent = "未开始";
    return;
  }
  const statusLabels = {
    queued: "等待发送",
    running: "执行中",
    sent: "已发送",
    started: "已启动",
    accepted: "飞控已确认",
    partial: "仍在进行",
    sent_no_ack: "等待飞控文本",
    completed: "完成",
    failed: "失败",
    rejected: "拒绝",
    timeout: "超时",
    expired: "已过期",
    cancelled: "已取消",
  };
  const modeText = session.mode === "mock" ? "模拟模式" : "真实飞控";
  const hasFlightControllerEvidence = aircraftCalibrationHasFlightControllerEvidence(session);
  const shownStatus = session.status === "sent_no_ack" && hasFlightControllerEvidence
    ? "running"
    : session.status;
  if (badge) badge.textContent = `${statusLabels[shownStatus] || shownStatus || "--"} · ${modeText}`;
  const reason = translateAircraftCalibrationText(session.reason || "");
  const safety = (session.safety_checks || []).map((item) => `
    <div class="aircraft-log-item ${item.ok ? "ok" : "warn"}">
      <small>安全检查 · ${escapeHtml(item.key || "")}</small>
      <strong>${escapeHtml(item.label || "")}</strong>
      <span>${escapeHtml(item.ok ? "通过" : item.reason || "未通过")}</span>
    </div>
  `).join("");
  const currentStatus = `
    <div class="aircraft-log-item ${["failed", "rejected", "timeout", "expired"].includes(shownStatus) || (shownStatus === "sent_no_ack" && !hasFlightControllerEvidence) ? "warn" : "ok"}">
      <small>当前状态 · ${modeText}</small>
      <strong>${escapeHtml(statusLabels[shownStatus] || shownStatus || "--")}</strong>
      <span>${escapeHtml(reason || "等待飞控返回命令确认 / 飞控文本。")}</span>
    </div>
  `;
  const messages = (session.messages || []).map((item) => {
    const source = item.source === "STATUSTEXT" ? "飞控文本" : item.source === "COMMAND_ACK" ? "命令确认" : item.source || "飞机校准";
    return `
    <div class="aircraft-log-item ${aircraftCalibrationMessageClass(item)}">
      <small>${escapeHtml(source)} · ${item.timeMs ? new Date(item.timeMs).toLocaleTimeString("zh-CN", { hour12: false }) : "--"}</small>
      <strong>${escapeHtml(translateAircraftCalibrationText(item.text || ""))}</strong>
    </div>
  `}).join("");
  const queueHint = session.status === "queued" && !hasFlightControllerEvidence
    ? `<div class="aircraft-log-item warn"><small>等待发送</small><strong>命令已提交到连接程序，尚未收到飞控确认</strong><span>如果超过 5 秒仍停在这里，请确认 PX6C 连接程序正在运行、GCS 心跳正常、飞控未解锁。</span></div>`
    : "";
  const expiredHint = session.status === "expired"
    ? `<div class="aircraft-log-item warn"><small>已过期</small><strong>这条本地队列命令没有被连接程序及时发送</strong><span>请确认飞控仍连接、飞机未解锁，然后重新点击“开始校准”。</span></div>`
    : "";
  host.innerHTML = safety + currentStatus + queueHint + expiredHint + messages || "等待飞控返回命令确认 / 飞控文本。";
  if (aircraftCalibrationGuideSessionId === session.session_id || aircraftCalibrationGuideSessionId === session.commandId) {
    renderAircraftCalibrationGuide(session);
  }
}

async function refreshAircraftCalibration(force = false) {
  if (!$("#aircraftCalibrationPage")) return;
  if (aircraftCalibrationBusy && !force) return;
  aircraftCalibrationBusy = true;
  try {
    const data = await api("/api/aircraft-calibration/status");
    aircraftCalibrationOverview = data;
    renderAircraftCalibrationLinkGrid(data);
    renderAircraftCalibrationCards(data);
    renderAircraftCalibrationDetail();
    renderAircraftReadonly(data);
    const active = (data.sessions || []).find((item) => item.session_id === aircraftCalibrationSessionId)
      || (data.sessions || []).slice(-1)[0];
    if (active) {
      aircraftCalibrationSessionId = active.session_id;
      renderAircraftCalibrationLog(active);
    }
  } catch (error) {
    showToast("飞机校准状态读取失败", error.message);
  } finally {
    aircraftCalibrationBusy = false;
  }
}

async function pollAircraftCalibrationSession(sessionId) {
  if (!sessionId) return;
  for (let index = 0; index < 120; index += 1) {
    await new Promise((resolve) => setTimeout(resolve, 700));
    try {
      const result = await api(`/api/aircraft-calibration/session/${encodeURIComponent(sessionId)}`);
      if (result.session) renderAircraftCalibrationLog(result.session);
      await refreshAircraftCalibration(false);
      if (["completed", "failed", "rejected", "timeout", "expired", "cancelled"].includes(result.session?.status)) break;
    } catch (_) {
      break;
    }
  }
}

async function startAircraftCalibration() {
  const activeButton = $(".aircraft-calibration-card.active");
  const selectedType = activeButton?.dataset?.aircraftCalibrationType || aircraftCalibrationSelected;
  const card = aircraftCardByType(selectedType);
  if (!card) return;
  aircraftCalibrationSelected = card.type;
  const mock = !!$("#aircraftCalibrationMock")?.checked;
  const confirmation = $("#aircraftCalibrationConfirmation")?.value.trim() || "";
  if (!mock && !window.confirm(`确认开始${card.title}？请确认飞机未解锁、已拆除螺旋桨并处于安全环境。`)) return;
  try {
    if (!mock && aircraftCalibrationOverview?.commandMode !== "real_command") {
      const enableCommandMode = window.confirm("当前处于实机只读模式，飞机校准需要临时切换到实机指令模式。\n\n确认已拆除螺旋桨、飞机未解锁，并处于地面安全环境？");
      if (!enableCommandMode) {
        showToast("校准已取消", "当前仍为实机只读模式");
        return;
      }
      const modeResult = await api("/api/mode", {
        method: "POST",
        body: JSON.stringify({ mode: "real_command" }),
      });
      currentOperationMode = modeResult.mode || "real_command";
      if ($("#operationMode")) $("#operationMode").value = currentOperationMode;
      await refreshSafetyState();
      await refreshAircraftCalibration(true);
    }
    const response = await api("/api/aircraft-calibration/start", {
      method: "POST",
      body: JSON.stringify({
        calibration_type: card.type,
        confirmation,
        mock,
      }),
    });
    aircraftCalibrationSessionId = response.session_id || response.session?.session_id || null;
    if (response.commandId) trackCommandEvidence(response.commandId, `${card.title}飞机校准`);
    renderAircraftCalibrationLog(response.session);
    if (card.type === "accel" && response.session && response.session.status !== "rejected") {
      openAircraftCalibrationGuide(card.type, response.session);
    }
    const queued = response.status === "queued" || response.session?.status === "queued";
    showToast(queued ? "校准命令已提交，等待飞控确认" : response.status === "started" ? "校准命令已发送" : "校准未启动", translateAircraftCalibrationText(response.reason || ""));
    if (aircraftCalibrationSessionId && !mock) pollAircraftCalibrationSession(aircraftCalibrationSessionId);
    refreshAircraftCalibration(true);
  } catch (error) {
    showToast("飞机校准失败", error.message);
  }
}

async function cancelAircraftCalibration() {
  if (!aircraftCalibrationSessionId) {
    showToast("没有可取消的校准会话", "当前未开始飞机校准");
    return;
  }
  try {
    const result = await api("/api/aircraft-calibration/cancel", {
      method: "POST",
      body: JSON.stringify({ session_id: aircraftCalibrationSessionId }),
    });
    renderAircraftCalibrationLog(result.session);
    showToast("校准会话已取消", result.session?.reason || result.reason || "");
  } catch (error) {
    showToast("取消失败", error.message);
  }
}

async function forceRefreshAircraftCalibration() {
  const confirmed = window.confirm(
    "确认强制刷新并清除 UI 本地校准状态？\n\n这只会清除地面站页面里的校准会话、卡住的进度和旧飞控文本，不会向 PX4 发送取消校准命令。如果飞控本身仍在校准，请等待失败/完成或重启飞控。"
  );
  if (!confirmed) return;
  try {
    const result = await api("/api/aircraft-calibration/force-refresh", {
      method: "POST",
      body: JSON.stringify({ reason: "operator_force_refresh" }),
    });
    aircraftCalibrationSessionId = null;
    aircraftCalibrationGuideSessionId = null;
    closeAircraftCalibrationGuide();
    renderAircraftCalibrationLog(null);
    await refreshAircraftCalibration(true);
    showToast(
      "校准状态已强制刷新",
      `${result.reason || ""} 清除会话 ${result.clearedSessions ?? 0} 个，清除校准文本 ${result.clearedStatusTexts ?? 0} 条。`
    );
  } catch (error) {
    showToast("强制刷新失败", error.message);
  }
}

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-aircraft-calibration-type]");
  if (!button) return;
  aircraftCalibrationSelected = button.dataset.aircraftCalibrationType;
  renderAircraftCalibrationCards(aircraftCalibrationOverview || {});
  renderAircraftCalibrationDetail();
});

setInterval(() => {
  if (activePageName === "aircraftCalibration" && !document.hidden) refreshAircraftCalibration(false);
}, 1000);

function renderRcSetupTabs(active = "monitor") {
  $$(".rc-setup-tabs [data-rc-tab]").forEach((button) => {
    button.classList.toggle("active", button.dataset.rcTab === active);
  });
  if ($("#rcMonitorTab")) $("#rcMonitorTab").hidden = active !== "monitor";
  if ($("#rcWizardTab")) $("#rcWizardTab").hidden = active !== "wizard";
}

function renderRcWizardSteps() {
  const host = $("#rcWizardSteps");
  if (!host) return;
  host.innerHTML = RC_WIZARD_STEPS.map((step, index) => `
    <button class="${index === rcStepIndex ? "active" : ""}" data-rc-step-index="${index}">
      <span>${index}</span><strong>${step.title.replace(/^Step \\d+\\s*/, "")}</strong>
    </button>
  `).join("");
  const step = RC_WIZARD_STEPS[rcStepIndex];
  if ($("#rcWizardTitle")) $("#rcWizardTitle").textContent = step.title;
  if ($("#rcWizardInstruction")) $("#rcWizardInstruction").textContent = step.instruction;
}

function renderRcLiveBars(channels = []) {
  const host = $("#rcWizardLiveBars");
  if (!host) return;
  host.innerHTML = channels.slice(0, 18).map((item) => {
    const percent = rcPercent(item.pwm, item.min, item.max);
    return `
      <div class="rc-live-row">
        <span>CH${item.channel}</span>
        <i><b style="width:${percent ?? 0}%"></b></i>
        <strong>${item.pwm ?? "unused"}</strong>
      </div>
    `;
  }).join("");
}

async function refreshRcSetup() {
  if (rcSetupRefreshBusy) return;
  rcSetupRefreshBusy = true;
  try {
    const data = await api("/api/rc/full");
    const now = performance.now();
    if (rcSetupLastRefreshAt) {
      rcSetupRefreshHz = Math.round(10000 / Math.max(1, now - rcSetupLastRefreshAt)) / 10;
    }
    rcSetupLastRefreshAt = now;
    rcSetupState = data;
    const online = !!data.rcOnline;
    renderRcLinkStatus(data.rcLink || {}, "rc");
    renderRcSourceStrip(data.rcLink || {}, data);
    if ($("#rcSetupBadge")) {
      const linkRate = data.rcLink?.rc_update_rate_hz;
      $("#rcSetupBadge").textContent = online ? `RC Online · ${linkRate ?? "--"} Hz` : "No RC";
      $("#rcSetupBadge").classList.toggle("offline", !online);
    }
    if ($("#rcStatusGrid")) {
      $("#rcStatusGrid").innerHTML = [
        ["Connected", data.connected ? "Connected" : "Disconnected"],
        ["Vehicle HB", data.connection?.heartbeat ? "OK" : "No heartbeat"],
        ["GCS HB", data.gcsHeartbeat?.sending ? `${data.gcsHeartbeat.rateHz || 0} Hz` : "not sending"],
        ["Target", data.targetSystem ? `${data.targetSystem}:${data.targetComponent}` : "--"],
        ["Armed", data.armed ? "Armed" : "Disarmed"],
        ["Mode", data.mode || "--"],
        ["Fixed Profile", data.rcLink?.activeFixedProfile || "default"],
      ].map(([label, value]) => `<div class="rc-status-card"><small>${label}</small><strong>${value}</strong></div>`).join("");
    }
    if ($("#rcSetupMapped")) {
      const mapped = data.mapped || {};
      $("#rcSetupMapped").innerHTML = Object.entries({
        roll: "Roll", pitch: "Pitch", throttle: "Throttle", yaw: "Yaw", flightMode: "Flight Mode", armSwitch: "Arm Switch"
      }).map(([key, label]) => {
        const item = mapped[key] || {};
        return `<div class="rc-mapped-card"><small>${label}</small><strong>CH${item.channel || "--"}</strong><span>${item.pwm ?? "--"} us</span></div>`;
      }).join("");
    }
    if ($("#rcSetupChannels")) {
      const channelFunctions = rcChannelFunctionMap(data.mapped || {});
      $("#rcSetupChannels").innerHTML = (data.channels || []).map((item) => `
        <tr>
          <td>CH${item.channel}</td><td>${rcChannelFunctionLabel(item, channelFunctions)}</td><td>${item.pwm ?? "unused"}</td><td>${item.percent ?? "--"}</td>
          <td>${item.min ?? "--"}</td><td>${item.max ?? "--"}</td><td>${item.trim ?? "--"}</td><td>${item.rev ?? "--"}</td>
          <td>${item.used ? "used" : "unused"}</td>
        </tr>
      `).join("");
    }
    const warnings = [...(data.rcLink?.warnings || []), ...(data.warnings || [])];
    if ($("#rcSetupWarnings")) $("#rcSetupWarnings").innerHTML = warnings.length ? warnings.map((item) => `<p>${item}</p>`).join("") : "<p>暂无 RC 映射告警</p>";
    renderRcLiveBars(data.channels || []);
  } catch (error) {
    if ($("#rcSetupWarnings")) $("#rcSetupWarnings").innerHTML = `<p>${error.message}</p>`;
  } finally {
    rcSetupRefreshBusy = false;
  }
}

function startRcSampling(ms = 2200) {
  rcSampleBuffer = [];
  clearInterval(rcSamplingTimer);
  const pull = async () => {
    const data = await api("/api/rc/channels");
    rcSampleBuffer.push((data.channels || []).map((item) => item.pwm));
    renderRcLiveBars(data.channels || []);
  };
  pull().catch(() => {});
  rcSamplingTimer = setInterval(() => pull().catch(() => {}), RC_SETUP_REFRESH_INTERVAL_MS);
  return new Promise((resolve) => setTimeout(() => {
    clearInterval(rcSamplingTimer);
    rcSamplingTimer = null;
    resolve(rcSampleBuffer);
  }, ms));
}

async function startRcSession() {
  const result = await api("/api/rc/calibration/start", { method: "POST", body: JSON.stringify({ requestParameters: true }) });
  if (!result.accepted) throw new Error(result.reason || "无法创建 RC 校准会话");
  rcSessionId = result.session.id;
  rcStepIndex = 1;
  renderRcWizardSteps();
  showToast("RC 校准会话已创建", result.reason || rcSessionId);
}

async function captureRcStep() {
  if (!rcSessionId) await startRcSession();
  const step = RC_WIZARD_STEPS[rcStepIndex].key;
  const duration = step === "ranges" ? 8000 : step === "detect" ? 800 : 2400;
  if ($("#rcWizardMessage")) $("#rcWizardMessage").innerHTML = `<p>正在采样 ${step}...</p>`;
  const samples = await startRcSampling(duration);
  const result = await api("/api/rc/calibration/step", {
    method: "POST",
    body: JSON.stringify({ sessionId: rcSessionId, step, samples }),
  });
  if ($("#rcWizardMessage")) $("#rcWizardMessage").innerHTML = `<p>${result.result?.reason || result.result?.message || "步骤已记录"}</p>`;
}

async function previewRcCalibration() {
  if (!rcSessionId) throw new Error("请先开始 RC 校准会话");
  const result = await api("/api/rc/calibration/preview", { method: "POST", body: JSON.stringify({ sessionId: rcSessionId }) });
  const preview = result.preview || [];
  if ($("#rcPreviewTable")) {
    $("#rcPreviewTable").innerHTML = preview.length ? `
      <table class="data-table rc-table"><thead><tr><th>参数</th><th>当前</th><th>新值</th><th>状态</th></tr></thead><tbody>
      ${preview.map((item) => `<tr><td>${item.name}</td><td>${item.current ?? "--"}</td><td>${item.value}</td><td>${item.changed ? "将写入" : "无变化"}</td></tr>`).join("")}
      </tbody></table>
    ` : "没有生成可写入参数，请完成更多步骤。";
  }
}

async function applyRcCalibration() {
  const confirmation = $("#rcApplyConfirmation")?.value.trim();
  const result = await api("/api/rc/calibration/apply", {
    method: "POST",
    body: JSON.stringify({ sessionId: rcSessionId, confirmation }),
  });
  showToast(result.accepted ? "RC 参数已加入写入队列" : "RC 参数未写入", result.reason);
}

async function restoreRcCalibration() {
  const confirmation = $("#rcRestoreConfirmation")?.value.trim();
  const result = await api("/api/rc/calibration/restore", {
    method: "POST",
    body: JSON.stringify({ sessionId: rcSessionId, confirmation }),
  });
  showToast(result.accepted ? "已加入恢复队列" : "未恢复", result.reason);
}

function installRcSetupHandlers() {
  renderRcWizardSteps();
  $$(".rc-setup-tabs [data-rc-tab]").forEach((button) => button.addEventListener("click", () => renderRcSetupTabs(button.dataset.rcTab)));
  $("#rcRefresh")?.addEventListener("click", refreshRcSetup);
  $("#rcStartSession")?.addEventListener("click", () => startRcSession().catch((error) => showToast("RC 会话失败", error.message)));
  $("#rcCaptureStep")?.addEventListener("click", () => captureRcStep().catch((error) => showToast("RC 采样失败", error.message)));
  $("#rcNextStep")?.addEventListener("click", () => { rcStepIndex = Math.min(RC_WIZARD_STEPS.length - 1, rcStepIndex + 1); renderRcWizardSteps(); });
  $("#rcPreview")?.addEventListener("click", () => previewRcCalibration().catch((error) => showToast("RC 预览失败", error.message)));
  $("#rcApplyParams")?.addEventListener("click", () => applyRcCalibration().catch((error) => showToast("RC 写入失败", error.message)));
  $("#rcRestoreParams")?.addEventListener("click", () => restoreRcCalibration().catch((error) => showToast("RC 恢复失败", error.message)));
  $("#rcWizardSteps")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-rc-step-index]");
    if (!button) return;
    rcStepIndex = Number(button.dataset.rcStepIndex);
    renderRcWizardSteps();
  });
  clearInterval(rcSetupRefreshTimer);
  rcSetupRefreshTimer = setInterval(() => {
    if (activePageName !== "rcSetup" || document.hidden) return;
    refreshRcSetup();
  }, RC_SETUP_REFRESH_INTERVAL_MS);
}

installRcSetupHandlers();

function installAiOpsEnhancements() {
  const runPid = $("#runAiPid");
  if (runPid && !$("#aiModelMode")) {
    runPid.insertAdjacentHTML("beforebegin", `
      <label>AI 分析模式
        <select id="aiModelMode">
          <option value="fast_check">快速初筛</option>
          <option value="standard_analysis" selected>标准分析</option>
          <option value="final_review">最终复核</option>
        </select>
      </label>
      <label class="inline-check"><input type="checkbox" id="aiUseSimilarCases" checked> 使用历史相似案例</label>
    `);
  }
  const pidSafety = $("#aiPidSafety");
  if (pidSafety && !$("#aiPidCostPanel")) {
    pidSafety.insertAdjacentHTML("afterend", `
      <div class="ai-cost-panel" id="aiPidCostPanel">AI 成本统计等待分析结果。</div>
      <div class="similar-case-panel" id="aiPidSimilarCasesPanel">相似案例将在分析后显示。</div>
    `);
  }

  const audience = $("#aiReportAudience");
  if (audience && !$("#aiReportModelMode")) {
    audience.closest("label")?.insertAdjacentHTML("afterend", `
      <label>AI 分析模式
        <select id="aiReportModelMode">
          <option value="fast_check">快速初筛</option>
          <option value="standard_analysis" selected>标准分析</option>
          <option value="final_review">最终复核</option>
        </select>
      </label>
      <label class="inline-check"><input type="checkbox" id="aiReportUseSimilarCases" checked> 使用历史相似案例</label>
    `);
  }
  const aiReportCards = $("#aiReportCardGrid");
  if (aiReportCards && !$("#aiReportCostPanel")) {
    aiReportCards.insertAdjacentHTML("afterend", `
      <div class="ai-cost-panel" id="aiReportCostPanel">AI 成本统计等待生成结果。</div>
      <div class="similar-case-panel" id="aiSimilarCasesPanel">相似案例将在生成后显示。</div>
    `);
  }
}

installAiOpsEnhancements();

const overviewTargets = {
  fleet: ".systems-panel",
  analysis: ".chart-panel",
  alerts: ".alerts-panel"
};

$$(".nav-item[data-page]").forEach((button) => {
  button.addEventListener("click", () => {
    $$(".nav-item[data-page]").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    const page = button.dataset.page;
    if (page === "history") {
      openHistoryDrawer();
      return;
    }
    if (overviewTargets[page]) {
      showPage("overview");
      setTimeout(() => focusPanel(overviewTargets[page]), 180);
      return;
    }
    showPage(page);
  });
});

$("#brandHome").addEventListener("click", (event) => {
  event.preventDefault();
  window.scrollTo({ top: 0, behavior: "smooth" });
});

const missionOverview = {
  id: "未创建",
  name: "自定义任务",
  area: "待规划区域",
  fleet: "UAV-03",
  status: "任务待规划",
  altitude: 130,
};

function openMissionPlanner() {
  $$(".nav-item[data-page]").forEach((item) => item.classList.toggle("active", item.dataset.page === "mission"));
  showPage("mission");
  initializeMissionMap();
  showToast("进入任务规划", "在地图上点击即可添加航点");
}

$("#missionControl").addEventListener("click", (event) => {
  event.preventDefault();
  openMissionPlanner();
});

const modal = $("#missionModal");
$("#newMission").addEventListener("click", () => {
  modal.hidden = false;
  setTimeout(() => $(".modal input").focus(), 0);
});
$$(".close-modal").forEach((button) => button.addEventListener("click", () => modal.hidden = true));
modal.addEventListener("click", (event) => {
  if (event.target === modal) modal.hidden = true;
});
$("#altRange").addEventListener("input", (event) => {
  $("#altOutput").textContent = `${event.target.value} m`;
});
$("#missionForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const missionName = $("#missionNameInput").value.trim() || "自定义任务";
  const missionArea = $("#missionAreaInput").value;
  const missionFleet = $("#missionFleetInput").value;
  missionOverview.id = `MS-${new Date().toISOString().slice(2, 10).replaceAll("-", "")}-${String(Math.floor(Math.random() * 900) + 100)}`;
  missionOverview.name = missionName;
  missionOverview.area = missionArea;
  missionOverview.fleet = missionFleet;
  missionOverview.altitude = Number($("#altRange").value || 130);
  missionOverview.status = "任务待规划";
  clearAllWaypoints();
  $("#currentMissionTitle").textContent = `${missionName} · ${missionArea}`;
  $(".status-label").textContent = "任务待规划";
  $(".live-dot").style.background = "var(--amber)";
  updateMissionOverview();
  modal.hidden = true;
  openMissionPlanner();
  showToast("任务创建成功", `${missionName} 已初始化，请开始规划航点`);
});

let gpsMap = null;
let activeMapLayer = null;
let uavMarker = null;
let flightTrack = null;
let overviewMissionLine = null;
let overviewMissionMarkers = null;
let latestPosition = null;
let firstGpsFix = true;
let lastTelemetryAt = 0;
let previousTelemetryReceivedAt = 0;
let latestTelemetry = {};
let autoFollow = true;
let currentOperationMode = "demo";
const trackPoints = [];
let homeMarker = null;
let cachedUavIcon = null;
let lastMapVisualAt = 0;
let lastMapFollowAt = 0;
let lastMapLabel = "";
let chartDirty = true;
let chartSizeKey = "";

const mapLayers = window.L ? {
  "卫星": L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    { attribution: "Tiles © Esri", maxZoom: 19 }
  ),
  "地形": L.tileLayer(
    "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
    { attribution: "© OpenStreetMap contributors, SRTM | OpenTopoMap", maxZoom: 17 }
  ),
  "标准": L.tileLayer(
    "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    { attribution: "© OpenStreetMap contributors", maxZoom: 19 }
  )
} : {};

function initializeGpsMap() {
  if (!window.L) {
    $("#mapWaiting strong").textContent = "地图组件加载失败";
    $("#mapWaiting small").textContent = "请检查网络连接后刷新页面";
    return;
  }
  gpsMap = L.map("gpsMap", { zoomControl: false }).setView([31.2304, 121.4737], 12);
  activeMapLayer = mapLayers["卫星"].addTo(gpsMap);
  overviewMissionLine = L.polyline([], {
    color: "#f2b84b",
    weight: 3,
    opacity: 0.95,
    lineCap: "round",
    lineJoin: "round"
  }).addTo(gpsMap);
  overviewMissionMarkers = L.layerGroup().addTo(gpsMap);
  flightTrack = L.polyline([], { color: "#24c9d9", weight: 3, opacity: .9 }).addTo(gpsMap);
  setTimeout(() => gpsMap.invalidateSize(), 100);
}
initializeGpsMap();

function getUavMapIcon() {
  if (!window.L) return null;
  if (!cachedUavIcon) {
    cachedUavIcon = L.divIcon({
      className: "uav-map-icon",
      html: `<div class="uav-map-marker"><span>▲</span></div>`,
      iconSize: [38, 38],
      iconAnchor: [19, 19]
    });
  }
  return cachedUavIcon;
}

function updateMapMarkerHeading(marker, heading) {
  const markerElement = marker?.getElement?.();
  const markerBody = markerElement?.querySelector?.(".uav-map-marker");
  if (markerBody) markerBody.style.setProperty("--heading", `${heading}deg`);
}

function updateUavMarkerHeading(heading) {
  updateMapMarkerHeading(uavMarker, heading);
}

function missionCommandText(command = "") {
  const key = String(command || "WAYPOINT").toUpperCase();
  return {
    TAKEOFF: "起飞",
    WAYPOINT: "航点",
    LOITER: "盘旋",
    LAND: "降落",
    RTL: "返航"
  }[key] || key;
}

function missionCommandShort(command = "") {
  const key = String(command || "WAYPOINT").toUpperCase();
  return {
    TAKEOFF: "TO",
    WAYPOINT: "WP",
    LOITER: "LT",
    LAND: "LD",
    RTL: "RTL"
  }[key] || "WP";
}

function getMissionWaypointIcon(index, command) {
  if (!window.L) return null;
  const commandKey = String(command || "WAYPOINT").toLowerCase().replace(/[^a-z0-9_-]/g, "");
  return L.divIcon({
    className: "mission-waypoint-map-icon",
    html: `<div class="mission-waypoint-marker ${commandKey}"><span>${index + 1}</span><small>${missionCommandShort(command)}</small></div>`,
    iconSize: [34, 34],
    iconAnchor: [17, 17]
  });
}

function renderOverviewMissionRoute(options = {}) {
  if (!gpsMap || !window.L || !overviewMissionLine || !overviewMissionMarkers) return;
  const route = waypoints
    .filter((point) => finite(point.lat) && finite(point.lon))
    .map((point) => ({ ...point, lat: Number(point.lat), lon: Number(point.lon) }));

  overviewMissionLine.setLatLngs(route.map((point) => [point.lat, point.lon]));
  overviewMissionMarkers.clearLayers();

  route.forEach((point, index) => {
    const marker = L.marker([point.lat, point.lon], {
      icon: getMissionWaypointIcon(index, point.command),
      zIndexOffset: 650
    }).addTo(overviewMissionMarkers);
    const altitude = finite(point.altitude) ? `${Number(point.altitude).toFixed(0)} m` : "-- m";
    marker.bindTooltip(`${index + 1} · ${missionCommandText(point.command)} · ${altitude}`, {
      permanent: false,
      direction: "top",
      className: "mission-waypoint-tooltip",
      offset: [0, -12]
    });
  });

  if (options.fit && route.length) {
    const bounds = L.latLngBounds(route.map((point) => [point.lat, point.lon]));
    if (bounds.isValid()) gpsMap.fitBounds(bounds.pad(0.22), { animate: false });
  }
}

$$(".segmented button").forEach((button) => {
  button.addEventListener("click", () => {
    if (!gpsMap) return showToast("地图暂不可用", "请检查网络连接");
    const name = button.textContent.trim();
    $$(".segmented button").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    gpsMap.removeLayer(activeMapLayer);
    activeMapLayer = mapLayers[name].addTo(gpsMap);
    showToast("地图模式已切换", `当前显示：${name}`);
  });
});

$$("[data-map-action]").forEach((button) => {
  button.addEventListener("click", () => {
    if (!gpsMap) return showToast("地图暂不可用", "请检查网络连接");
    const action = button.dataset.mapAction;
    if (action === "zoom-in") {
      gpsMap.zoomIn();
    }
    if (action === "zoom-out") {
      gpsMap.zoomOut();
    }
    if (action === "locate") {
      if (!latestPosition) return showToast("尚未收到 GPS", "请先连接 MAVLink 数传");
      gpsMap.flyTo(latestPosition, Math.max(gpsMap.getZoom(), 16));
      showToast("已定位无人机", $("#coordinateText").textContent);
    }
    if (action === "follow") {
      autoFollow = !autoFollow;
      button.classList.toggle("active", autoFollow);
      showToast(autoFollow ? "自动跟随已开启" : "自动跟随已关闭", "地图仍会继续记录实际航迹");
    }
    if (action === "clear-track") {
      trackPoints.length = 0;
      flightTrack.setLatLngs([]);
      showToast("轨迹已清除", "新的 GPS 点将继续记录");
    }
  });
});

function finite(value) {
  return value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value));
}

function signedAngle(value) {
  const number = Number(value);
  return `${number >= 0 ? "+" : ""}${number.toFixed(1)}°`;
}

function headingCardinal(degrees) {
  const directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
  return directions[Math.round(((degrees % 360 + 360) % 360) / 45) % directions.length];
}

const ATTITUDE_RENDER_ALPHA = 0.94;
const attitudeRender = {
  initialized: false,
  current: { roll: 0, pitch: 0, yaw: 0, speed: 0 },
  target: { roll: 0, pitch: 0, yaw: 0, speed: null },
  rates: { roll: 0, pitch: 0, yaw: 0 },
  targetAt: 0,
  running: false,
  lastFrameAt: 0,
};
let lastHeavyTelemetryUiAt = 0;
let lastChartUiAt = 0;
let lastDiagnosticTelemetryUiAt = 0;
let lastReadinessTelemetryUiAt = 0;
let lastLiveTelemetryTextAt = 0;
let flightDisplayDom = null;
let realtimeInstrumentDriver = null;
const smoothInstrumentStats = {
  attitudeFrames: 0,
  compassFrames: 0,
  attitudeFps: 0,
  compassFps: 0,
  lastFpsAt: 0,
};

function getFlightDisplayDom() {
  if (flightDisplayDom) return flightDisplayDom;
  flightDisplayDom = {
    smoothAttitudeLayer: $("#smoothAttitudeLayer"),
    smoothCompassLayer: $("#smoothCompassLayer"),
    smoothAttitudeOffline: $("#smoothAttitudeOffline"),
    smoothCompassOffline: $("#smoothCompassOffline"),
    smoothAttitudeDebug: $("#smoothAttitudeDebug"),
    smoothCompassDebug: $("#smoothCompassDebug"),
    hudAttitudeScene: $("#hudAttitudeScene"),
    speed: $("#speed"),
    hudGroundSpeed: $("#hudGroundSpeed"),
    pitchValue: $("#pitchValue"),
    rollValue: $("#rollValue"),
    hudPitch: $("#hudPitch"),
    hudRoll: $("#hudRoll"),
    hudYaw: $("#hudYaw"),
    yawValue: $("#yawValue"),
    headingValue: $("#headingValue"),
    headingCardinal: $("#headingCardinal"),
    hudHeading: $("#hudHeading"),
    hudHeadingCardinal: $("#hudHeadingCardinal"),
    connectionRoll: $("#connectionRoll"),
    connectionPitch: $("#connectionPitch"),
    connectionYaw: $("#connectionYaw"),
    attitudeState: $("#attitudeState"),
    compassState: $("#compassState"),
  };
  return flightDisplayDom;
}

function shortestAngleDelta(target, current) {
  return ((target - current + 540) % 360) - 180;
}

function applyHudVisual(roll, pitch) {
  const dom = getFlightDisplayDom();
  const pitchOffset = Math.max(-30, Math.min(30, pitch)) * 2.8;
  dom.hudAttitudeScene?.style.setProperty("--roll-angle", `${-roll}deg`);
  dom.hudAttitudeScene?.style.setProperty("--pitch-offset", `${pitchOffset}px`);
}

function applySmoothInstrumentVisual(roll, pitch, yaw, timestamp) {
  const dom = getFlightDisplayDom();
  const now = Date.now();
  const ageMs = timestamp ? Math.max(0, now - timestamp) : Infinity;
  const online = ageMs <= 1000;
  const clampedPitch = Math.max(-35, Math.min(35, pitch));
  const pitchOffset = clampedPitch * 2.8;
  const normalizedYaw = (yaw % 360 + 360) % 360;
  if (dom.smoothAttitudeLayer) {
    dom.smoothAttitudeLayer.style.transform = `translate3d(0, ${pitchOffset}px, 0) rotate(${-roll}deg)`;
  }
  if (dom.smoothCompassLayer) {
    dom.smoothCompassLayer.style.transform = `translate3d(0, 0, 0) rotate(${-normalizedYaw}deg)`;
  }
  if (now - (applySmoothInstrumentVisual.lastTextAt || 0) >= 40) {
    applySmoothInstrumentVisual.lastTextAt = now;
    if (dom.pitchValue) dom.pitchValue.textContent = signedAngle(pitch);
    if (dom.rollValue) dom.rollValue.textContent = signedAngle(roll);
    if (dom.headingValue) dom.headingValue.textContent = `${String(Math.round(normalizedYaw)).padStart(3, "0")}°`;
    if (dom.headingCardinal) dom.headingCardinal.textContent = headingCardinal(normalizedYaw);
  }
  dom.smoothAttitudeOffline?.classList.toggle("visible", !online);
  dom.smoothCompassOffline?.classList.toggle("visible", !online);
  smoothInstrumentStats.attitudeFrames += 1;
  smoothInstrumentStats.compassFrames += 1;
  if (!smoothInstrumentStats.lastFpsAt) smoothInstrumentStats.lastFpsAt = now;
  const elapsed = now - smoothInstrumentStats.lastFpsAt;
  if (elapsed >= 1000) {
    smoothInstrumentStats.attitudeFps = Math.round(smoothInstrumentStats.attitudeFrames * 1000 / elapsed);
    smoothInstrumentStats.compassFps = Math.round(smoothInstrumentStats.compassFrames * 1000 / elapsed);
    smoothInstrumentStats.attitudeFrames = 0;
    smoothInstrumentStats.compassFrames = 0;
    smoothInstrumentStats.lastFpsAt = now;
    const ageText = Number.isFinite(ageMs) ? `${Math.round(ageMs)}ms` : "--";
    if (dom.smoothAttitudeDebug) dom.smoothAttitudeDebug.textContent = `age ${ageText} · fps ${smoothInstrumentStats.attitudeFps} · ${telemetryTransportMode}`;
    if (dom.smoothCompassDebug) dom.smoothCompassDebug.textContent = `age ${ageText} · fps ${smoothInstrumentStats.compassFps} · ${telemetryTransportMode}`;
  }
}

function formatLiveSpeed(value) {
  if (!Number.isFinite(Number(value))) return "--";
  const numeric = Number(value);
  return Math.abs(numeric) < 10 ? numeric.toFixed(2) : numeric.toFixed(1);
}

function ensureRealtimeInstruments() {
  if (realtimeInstrumentDriver) return realtimeInstrumentDriver;
  if (!window.UAVRealtimeInstruments?.create) return null;
  realtimeInstrumentDriver = window.UAVRealtimeInstruments.create({
    elements: getFlightDisplayDom(),
    signedAngle,
    headingCardinal,
    formatSpeed: formatLiveSpeed,
    transportMode: () => telemetryTransportMode,
    alpha: 0.93,
    speedAlpha: 0.88,
  });
  return realtimeInstrumentDriver;
}

function renderAttitudeFrame(timestamp = 0) {
  if (window.UAVRealtimeInstruments?.create) return;
  attitudeRender.running = true;
  if (timestamp && attitudeRender.lastFrameAt && timestamp - attitudeRender.lastFrameAt < 16) {
    requestAnimationFrame(renderAttitudeFrame);
    return;
  }
  attitudeRender.lastFrameAt = timestamp || performance.now();
  if (attitudeRender.initialized) {
    const current = attitudeRender.current;
    const target = attitudeRender.target;
    const predictionSeconds = attitudeRender.targetAt
      ? Math.min(0.12, Math.max(0, Date.now() - attitudeRender.targetAt) / 1000)
      : 0;
    const predictedRoll = target.roll + attitudeRender.rates.roll * predictionSeconds;
    const predictedPitch = target.pitch + attitudeRender.rates.pitch * predictionSeconds;
    const predictedYaw = (target.yaw + attitudeRender.rates.yaw * predictionSeconds + 360) % 360;
    current.roll += (predictedRoll - current.roll) * ATTITUDE_RENDER_ALPHA;
    current.pitch += (predictedPitch - current.pitch) * ATTITUDE_RENDER_ALPHA;
    current.yaw = (current.yaw + shortestAngleDelta(predictedYaw, current.yaw) * ATTITUDE_RENDER_ALPHA + 360) % 360;
    if (target.speed !== null) {
      current.speed += (target.speed - current.speed) * 0.9;
      const speedText = formatLiveSpeed(current.speed);
      const dom = getFlightDisplayDom();
      if (dom.speed) dom.speed.textContent = speedText;
      if (dom.hudGroundSpeed) dom.hudGroundSpeed.textContent = speedText;
    }
    applyHudVisual(current.roll, current.pitch);
    applySmoothInstrumentVisual(current.roll, current.pitch, current.yaw, attitudeRender.targetAt);
  }
  requestAnimationFrame(renderAttitudeFrame);
}

requestAnimationFrame(renderAttitudeFrame);

function setTrend(id, text, state = "neutral") {
  const node = $(`#${id}`);
  if (!node) return;
  setTextIfChanged(node, text);
  setExclusiveStateClass(node, ["up", "down", "neutral"], state);
}

function setText(id, text) {
  setTextIfChanged(`#${id}`, text);
}

function updateSignalBars(state = "offline") {
  const holder = $(".signal-bars");
  if (!holder) return;
  holder.classList.remove("online", "warn", "offline");
  holder.classList.add(state);
}

function updateRealMetricFooters(data, altitude, speed) {
  const age = finite(data.ageSeconds) ? Number(data.ageSeconds) : null;
  const receivedAt = finite(data.receivedAt) ? Number(data.receivedAt) : 0;
  const packetGapMs = previousTelemetryReceivedAt && receivedAt
    ? Math.max(0, receivedAt - previousTelemetryReceivedAt)
    : null;
  if (receivedAt) previousTelemetryReceivedAt = receivedAt;

  if (age !== null) {
    const linkState = age < 1 ? "online" : age < 3 ? "warn" : "offline";
    const freshness = age < 1 ? `${Math.round(age * 1000)} ms 前` : `${age.toFixed(1)} s 前`;
    $("#linkFreshness").textContent = `报文 ${freshness}`;
    updateSignalBars(linkState);
    setTrend("signalState", age < 1 ? "链路在线" : "链路变慢", age < 3 ? "up" : "down");
    setText("signalDetail", packetGapMs !== null ? `到达间隔 ${packetGapMs} ms` : "等待下一包");
  } else {
    $("#linkFreshness").textContent = "等待遥测";
    updateSignalBars("offline");
    setTrend("signalState", "等待链路", "neutral");
    setText("signalDetail", "MAVLink");
  }

  if (altitude !== null) {
    const climb = finite(data.climb) ? Number(data.climb) : null;
    setTrend("altitudeState", climb === null ? "高度有效" : `${climb >= 0 ? "爬升" : "下降"} ${Math.abs(climb).toFixed(1)} m/s`, climb === null ? "neutral" : climb >= 0 ? "up" : "down");
    setText("altitudeDetail", finite(data.alt) ? `海拔 ${Number(data.alt).toFixed(1)} m` : "相对高度");
  } else {
    setTrend("altitudeState", "等待高度", "neutral");
    setText("altitudeDetail", "真实遥测");
  }

  if (speed !== null) {
    setTrend("speedState", speed > 0.5 ? "地速在线" : "低速/静止", speed > 0.5 ? "up" : "neutral");
    setText("speedDetail", finite(data.heading) ? `航向 ${Math.round(Number(data.heading))}°` : "等待航向");
  } else {
    setTrend("speedState", "等待地速", "neutral");
    setText("speedDetail", "真实遥测");
  }

  if (finite(data.battery) || finite(data.voltage)) {
    const battery = finite(data.battery) ? Number(data.battery) : null;
    const voltage = finite(data.voltage) ? Number(data.voltage) : null;
    const current = finite(data.current) ? Number(data.current) : null;
    setTrend("batteryState", battery === null ? "电源在线" : battery < 25 ? "电量偏低" : "电池在线", battery !== null && battery < 25 ? "down" : "up");
    setText("batteryDetail", `${voltage !== null ? voltage.toFixed(1) + " V" : "--"}${current !== null ? " · " + current.toFixed(1) + " A" : ""}`);
  } else {
    setTrend("batteryState", "等待电池", "neutral");
    setText("batteryDetail", "真实遥测");
  }

  const fixType = finite(data.fixType) ? Number(data.fixType) : null;
  const sats = finite(data.satellites) ? Math.round(Number(data.satellites)) : null;
  if (fixType !== null || sats !== null) {
    const gpsOk = fixType === null || fixType >= 3;
    setTrend("gpsState", gpsOk ? `Fix ${fixType ?? "--"}` : `Fix ${fixType} 未定位`, gpsOk ? "up" : "down");
    setText("gpsDetail", `${sats ?? "--"} 星${finite(data.eph) ? " · HDOP " + Number(data.eph).toFixed(2) : ""}`);
  } else {
    setTrend("gpsState", "等待 GPS", "neutral");
    setText("gpsDetail", "真实遥测");
  }
}

function setReadinessCard(id, state) {
  const card = $(id);
  if (!card) return;
  setExclusiveStateClass(card, ["online", "warn", "offline"], state);
}

function setReadinessText(id, value) {
  setTextIfChanged(id, value);
}

function formatLinkAge(seconds) {
  if (!Number.isFinite(Number(seconds))) return "--";
  const value = Number(seconds);
  return value < 1 ? `${Math.round(value * 1000)} ms` : `${value.toFixed(1)} s`;
}

function updateFlightReadiness(data = {}, online = true) {
  const normalized = normalizeMessageStats(data.messageStats);
  const heartbeat = online && !!(data.heartbeat || data.connected || normalized.counts.HEARTBEAT);
  const rate = finite(data.rateHz) ? Number(data.rateHz) : 0;
  const age = finite(data.ageSeconds)
    ? Number(data.ageSeconds)
    : lastTelemetryAt ? (Date.now() - lastTelemetryAt) / 1000 : null;
  const messageTypes = Object.keys(normalized.counts).filter((key) => Number(normalized.counts[key]) > 0);
  const linkState = !heartbeat || age === null || age > 3 || rate <= 0
    ? "offline"
    : age > 1.2 || rate < 2 ? "warn" : "online";
  setReadinessCard("#linkQualityCard", linkState);
  setReadinessText("#linkQualityState", linkState === "online" ? "链路在线" : linkState === "warn" ? "链路偏慢" : "等待心跳");
  setReadinessText("#linkQualityRate", rate.toFixed(rate >= 10 ? 0 : 1));
  setReadinessText("#linkQualityAge", formatLinkAge(age));
  setReadinessText("#linkQualityTypes", `${messageTypes.length} 类`);

  const fixType = finite(data.fixType) ? Number(data.fixType) : null;
  const satellites = finite(data.satellites) ? Math.round(Number(data.satellites)) : null;
  const eph = finite(data.eph) ? Number(data.eph) : null;
  const hasHome = !!(data.home_position || (finite(data.homeLat) && finite(data.homeLon)));
  const gpsState = fixType === null && satellites === null
    ? "offline"
    : (fixType >= 3 && (satellites === null || satellites >= 8)) ? "online" : "warn";
  setReadinessCard("#gpsQualityCard", gpsState);
  setReadinessText("#gpsQualityState", gpsState === "online" ? "定位可用" : gpsState === "warn" ? "定位待确认" : "未定位");
  setReadinessText("#gpsQualityFix", `Fix ${fixType ?? "--"}`);
  setReadinessText("#gpsQualitySat", `${satellites ?? "--"} 星`);
  setReadinessText("#gpsQualityEph", eph === null ? "--" : eph.toFixed(2));
  setReadinessText("#gpsQualityHome", hasHome ? "已建立" : "未建立");

  const battery = finite(data.battery) ? Number(data.battery) : null;
  const voltage = finite(data.voltage) ? Number(data.voltage) : null;
  const current = finite(data.current) ? Number(data.current) : null;
  const hasPower = battery !== null || voltage !== null || current !== null;
  const powerState = !hasPower ? "offline" : (battery !== null && battery < 30) ? "warn" : "online";
  setReadinessCard("#powerHealthCard", powerState);
  setReadinessText("#powerHealthState", powerState === "online" ? "电源在线" : powerState === "warn" ? "电量偏低" : "等待电池");
  setReadinessText("#powerHealthBattery", battery === null ? "--" : Math.round(battery));
  setReadinessText("#powerHealthVoltage", voltage === null ? "-- V" : `${voltage.toFixed(1)} V`);
  setReadinessText("#powerHealthCurrent", current === null ? "-- A" : `${current.toFixed(1)} A`);
}

function setSystemLight(id, state) {
  const light = $(`#${id}`);
  if (!light) return;
  setExclusiveStateClass(light, ["ok", "warm", "bad"], state);
}

function setSystemText(id, text) {
  setTextIfChanged(`#${id}`, text);
}

function updateSystemsPanel(data, online = true) {
  const badge = $("#systemsHealthBadge");
  const subtitle = $("#systemsSubtitle");
  if (!online) {
    if (badge) {
      badge.textContent = "未连接";
      badge.classList.add("offline");
    }
    if (subtitle) subtitle.textContent = "未收到实时 MAVLink";
    setSystemLight("systemFlightDot", "bad");
    setSystemLight("systemPowerDot", "warm");
    setSystemLight("systemGpsDot", "warm");
    setSystemLight("systemActuatorDot", "warm");
    setSystemLight("systemLinkDot", "bad");
    setSystemText("systemFlightStatus", "未连接");
    setSystemText("systemPowerStatus", "等待电池");
    setSystemText("systemGpsStatus", "未定位");
    setSystemText("systemActuatorStatus", "等待输出");
    setSystemText("systemLinkStatus", "无心跳");
    setSystemText("systemLastMessage", "--");
    setSystemText("systemDetail", "当前没有实时飞控心跳，机载系统状态不可判定。");
    return;
  }

  const fixType = finite(data.fixType) ? Number(data.fixType) : 0;
  const sats = finite(data.satellites) ? Math.round(Number(data.satellites)) : 0;
  const battery = finite(data.battery) ? Math.round(Number(data.battery)) : null;
  const voltage = finite(data.voltage) ? Number(data.voltage) : null;
  const outputs = Array.isArray(data.servo_outputs) ? data.servo_outputs.filter((value) => Number(value) > 0) : [];
  const flightOk = data.connected !== false && !!data.mode;
  const gpsOk = fixType >= 3;
  const powerOk = battery !== null || voltage !== null;
  const actuatorOk = outputs.length > 0;
  const allPrimaryOk = flightOk && gpsOk && powerOk;

  if (badge) {
    badge.textContent = allPrimaryOk ? "运行正常" : "部分待确认";
    badge.classList.toggle("offline", !allPrimaryOk);
  }
  if (subtitle) subtitle.textContent = `${data.vehicleId || "PX6C"} · ${data.mode || "UNKNOWN"} · ${data.armed ? "已解锁" : "未解锁"}`;
  setSystemLight("systemFlightDot", flightOk ? "ok" : "bad");
  setSystemLight("systemPowerDot", powerOk ? "ok" : "warm");
  setSystemLight("systemGpsDot", gpsOk ? "ok" : "warm");
  setSystemLight("systemActuatorDot", actuatorOk ? "ok" : "warm");
  setSystemLight("systemLinkDot", "ok");
  setSystemText("systemFlightStatus", `${data.mode || "UNKNOWN"} · ${data.armed ? "已解锁" : "未解锁"}`);
  setSystemText("systemPowerStatus", powerOk ? `${battery ?? "--"}% · ${voltage ? voltage.toFixed(1) + "V" : "--"}` : "未收到电池");
  setSystemText("systemGpsStatus", gpsOk ? `Fix ${fixType} · ${sats} 星` : `Fix ${fixType} · ${sats} 星`);
  setSystemText("systemActuatorStatus", actuatorOk ? `${outputs.length} 路输出` : "未收到输出");
  setSystemText("systemLinkStatus", `在线 · ${data.lastMessage || "MAVLink"}`);
  setSystemText("systemLastMessage", data.lastMessage || "--");
  setSystemText("systemDetail", allPrimaryOk ? "飞控、GNSS、电源链路均有实时数据。" : "部分机载数据缺失，请检查 GPS、电池或 MAVLink 消息流。");
}

function updateAttitude(data) {
  const hasRoll = finite(data.roll);
  const hasPitch = finite(data.pitch);
  const hasYaw = finite(data.yaw) || finite(data.heading);
  if (!hasRoll && !hasPitch && !hasYaw) return;

  const roll = hasRoll ? Number(data.roll) : 0;
  const pitch = hasPitch ? Number(data.pitch) : 0;
  const yaw = finite(data.yaw) ? Number(data.yaw) : Number(data.heading);
  const normalizedYaw = (yaw % 360 + 360) % 360;

  const instrumentDriver = ensureRealtimeInstruments();
  if (instrumentDriver) {
    instrumentDriver.setTarget({
      roll,
      pitch,
      yaw: normalizedYaw,
      rollRate: finite(data.rollRate) ? Number(data.rollRate) : 0,
      pitchRate: finite(data.pitchRate) ? Number(data.pitchRate) : 0,
      yawRate: finite(data.yawRate) ? Number(data.yawRate) : 0,
      speed: finite(data.speed) ? Number(data.speed) : null,
      timestamp: finite(data.attitudeTimeMs) ? Number(data.attitudeTimeMs) : Date.now(),
    });
    updateAttitude.lastAt = Date.now();
    updateAttitude.onlineStateApplied = true;
    return;
  }

  attitudeRender.target.roll = roll;
  attitudeRender.target.pitch = pitch;
  attitudeRender.target.yaw = normalizedYaw;
  attitudeRender.rates.roll = finite(data.rollRate) ? Number(data.rollRate) : 0;
  attitudeRender.rates.pitch = finite(data.pitchRate) ? Number(data.pitchRate) : 0;
  attitudeRender.rates.yaw = finite(data.yawRate) ? Number(data.yawRate) : 0;
  attitudeRender.targetAt = finite(data.attitudeTimeMs) ? Number(data.attitudeTimeMs) : Date.now();
  if (!attitudeRender.initialized) {
    attitudeRender.current.roll = roll;
    attitudeRender.current.pitch = pitch;
    attitudeRender.current.yaw = normalizedYaw;
    attitudeRender.current.speed = finite(data.speed) ? Number(data.speed) : 0;
    attitudeRender.initialized = true;
    applyHudVisual(roll, pitch);
    applySmoothInstrumentVisual(roll, pitch, normalizedYaw, attitudeRender.targetAt);
  }

  const now = Date.now();
  updateAttitude.lastAt = now;
  if (now - updateAttitude.lastTextAt < 40) return;
  updateAttitude.lastTextAt = now;

  const dom = getFlightDisplayDom();
  const pitchText = signedAngle(pitch);
  const rollText = signedAngle(roll);
  const yawText = `${normalizedYaw.toFixed(1)}°`;
  const headingText = `${String(Math.round(normalizedYaw)).padStart(3, "0")}°`;
  const cardinal = headingCardinal(normalizedYaw);
  if (dom.pitchValue) dom.pitchValue.textContent = pitchText;
  if (dom.rollValue) dom.rollValue.textContent = rollText;
  if (dom.hudPitch) dom.hudPitch.textContent = pitchText;
  if (dom.hudRoll) dom.hudRoll.textContent = rollText;
  if (dom.hudYaw) dom.hudYaw.textContent = yawText;
  if (dom.yawValue) dom.yawValue.textContent = yawText;
  if (dom.headingValue) dom.headingValue.textContent = headingText;
  if (dom.headingCardinal) dom.headingCardinal.textContent = cardinal;
  if (dom.hudHeading) dom.hudHeading.textContent = headingText;
  if (dom.hudHeadingCardinal) dom.hudHeadingCardinal.textContent = cardinal;
  if (dom.connectionRoll) dom.connectionRoll.textContent = rollText;
  if (dom.connectionPitch) dom.connectionPitch.textContent = pitchText;
  if (dom.connectionYaw) dom.connectionYaw.textContent = yawText;
  if (!updateAttitude.onlineStateApplied) {
    if (dom.attitudeState) {
      dom.attitudeState.textContent = "ATTITUDE 在线";
      dom.attitudeState.classList.remove("offline");
      dom.attitudeState.classList.add("online");
    }
    if (dom.compassState) {
      dom.compassState.textContent = "罗盘在线";
      dom.compassState.classList.remove("offline");
      dom.compassState.classList.add("online");
    }
    updateAttitude.onlineStateApplied = true;
  }
}
updateAttitude.lastAt = 0;
updateAttitude.lastTextAt = 0;
updateAttitude.onlineStateApplied = false;

function normalizeFlightModeKey(mode = "") {
  const text = String(mode || "").toUpperCase();
  if (text.includes("AUTO") && text.includes("MISSION")) return "mission";
  if (text.includes("AUTO") && text.includes("RTL")) return "rtl";
  if (text === "RTL" || text.includes("RETURN")) return "rtl";
  if (text.includes("AUTO") && text.includes("LAND")) return "land";
  if (text.includes("POSCTL") || text.includes("POSITION")) return "position";
  if (text.includes("ALTCTL") || text.includes("ALTITUDE")) return "altitude";
  if (text.includes("MANUAL")) return "manual";
  return "";
}

function updateFlightModeButtons(mode) {
  const active = normalizeFlightModeKey(mode);
  $$("#flightModeButtons [data-flight-mode]").forEach((button) => {
    const key = button.dataset.flightMode;
    button.classList.toggle("active", key === active);
    button.classList.toggle("danger", key === "land" || key === "rtl");
  });
}

async function requestFlightMode(modeKey) {
  const label = {
    manual: "手动模式",
    position: "位置模式",
    altitude: "定高模式",
    land: "降落模式",
    rtl: "返航模式",
    mission: "任务模式",
  }[modeKey] || modeKey;
  if (modeKey === "land" && !window.confirm("确认切换到降落模式？飞控会开始执行 AUTO LAND。")) {
    return;
  }
  if (modeKey === "rtl" && !window.confirm("确认切换到返航模式？飞控会开始执行 AUTO RTL。")) {
    return;
  }
  const button = $(`#flightModeButtons [data-flight-mode="${modeKey}"]`);
  try {
    if (button) button.classList.add("pending");
    if ($("#flightModeCommandState")) $("#flightModeCommandState").textContent = `正在请求切换到 ${label}...`;
    const result = await api("/api/flight-mode", {
      method: "POST",
      body: JSON.stringify({ mode: modeKey, confirmLand: modeKey === "land", confirmRtl: modeKey === "rtl" })
    });
    if (!result.accepted) {
      if ($("#flightModeCommandState")) $("#flightModeCommandState").textContent = result.reason;
      showToast("模式切换被阻止", result.reason);
      return;
    }
    showToast("飞行模式命令已发送", result.reason);
    trackCommandEvidence(result.commandId, `飞行模式切换：${label}`);
    const status = await waitForCommandStatus(result.commandId, 18, 350);
    if ($("#flightModeCommandState")) $("#flightModeCommandState").textContent = status.message || result.reason;
    if (status.status === "accepted") {
      showToast("飞行模式切换完成", status.message || label);
    } else if (status.status === "sent_no_ack") {
      showToast("等待飞控确认", status.message || "命令已发送但暂未收到 ACK");
    } else if (status.status === "rejected") {
      showToast("飞行模式切换失败", status.message || "飞控拒绝该模式");
    }
  } catch (error) {
    if ($("#flightModeCommandState")) $("#flightModeCommandState").textContent = error.message;
    showToast("飞行模式切换失败", error.message);
  } finally {
    if (button) button.classList.remove("pending");
  }
}

$$("#flightModeButtons [data-flight-mode]").forEach((button) => {
  button.addEventListener("click", () => requestFlightMode(button.dataset.flightMode));
});

async function requestArmToggle() {
  const button = $("#armToggleButton");
  const wantsArm = button?.dataset.armTarget !== "disarm";
  const actionLabel = wantsArm ? "解锁" : "上锁";
  if (wantsArm) {
    const ok = window.confirm("确认已拆除螺旋桨，并处于安全测试环境？\n\n点击确定后，UI 会向飞控发送真实 Arm 解锁命令。");
    if (!ok) return;
  } else {
    const ok = window.confirm("确认向飞控发送 Disarm 上锁命令？");
    if (!ok) return;
  }
  try {
    if (button) button.classList.add("pending");
    const result = await api("/api/arm", {
      method: "POST",
      body: JSON.stringify({
        arm: wantsArm,
        confirmation: wantsArm ? "确认已拆桨并处于安全测试环境" : "确认上锁",
      }),
    });
    if (!result.accepted) {
      showToast(`${actionLabel}被安全层阻止`, result.reason);
      return;
    }
    showToast(`${actionLabel}命令已发送`, result.reason);
    trackCommandEvidence(result.commandId, `${actionLabel}飞机`);
    const status = await waitForCommandStatus(result.commandId, 22, 300);
    if (status.status === "accepted") {
      showToast(`${actionLabel}已被飞控确认`, status.message || "COMMAND_ACK accepted");
    } else if (status.status === "sent_no_ack") {
      showToast(`${actionLabel}等待确认`, status.message || "暂未收到 COMMAND_ACK");
    } else if (status.status === "rejected") {
      showToast(`${actionLabel}失败`, status.message || "飞控拒绝命令");
    }
  } catch (error) {
    showToast(`${actionLabel}失败`, error.message);
  } finally {
    if (button) button.classList.remove("pending");
  }
}

$("#armToggleButton")?.addEventListener("click", requestArmToggle);

const MAVLINK_MONITOR_MESSAGES = [
  { type: "HEARTBEAT", label: "心跳", required: true, maxAge: 3 },
  { type: "ATTITUDE", label: "姿态", required: true, maxAge: 2 },
  { type: "GLOBAL_POSITION_INT", label: "GPS 位置", required: true, maxAge: 4 },
  { type: "GPS_RAW_INT", label: "GPS 原始", required: false, maxAge: 5 },
  { type: "VFR_HUD", label: "速度/高度", required: true, maxAge: 3 },
  { type: "SYS_STATUS", label: "系统状态", required: true, maxAge: 5 },
  { type: "BATTERY_STATUS", label: "电池", required: false, maxAge: 8 },
  { type: "RADIO_STATUS", label: "数传链路", required: false, maxAge: 8 },
  { type: "STATUSTEXT", label: "飞控文本", required: false, maxAge: 30 },
  { type: "COMMAND_ACK", label: "命令确认", required: false, maxAge: 30 }
];

function normalizeMessageStats(stats) {
  const safe = stats && typeof stats === "object" ? stats : {};
  return {
    counts: safe.counts && typeof safe.counts === "object" ? safe.counts : {},
    rates: safe.rates && typeof safe.rates === "object" ? safe.rates : {},
    lastSeen: safe.lastSeen && typeof safe.lastSeen === "object" ? safe.lastSeen : {},
    ages: safe.ages && typeof safe.ages === "object" ? safe.ages : {},
    recent: Array.isArray(safe.recent) ? safe.recent : []
  };
}

function messageAgeSeconds(lastSeen) {
  if (!lastSeen) return null;
  if (typeof lastSeen === "number") return Number.isFinite(lastSeen) ? lastSeen : null;
  const timestamp = Date.parse(lastSeen);
  if (!Number.isFinite(timestamp)) return null;
  return Math.max(0, (Date.now() - timestamp) / 1000);
}

function renderMavlinkMonitor(stats, status = {}) {
  const list = $("#mavlinkMonitorList");
  if (!list) return;
  const normalized = normalizeMessageStats(stats);
  const heartbeat = !!(status.heartbeat || status.connected || normalized.counts.HEARTBEAT);
  const receivedTypes = Object.keys(normalized.counts).filter((key) => normalized.counts[key] > 0);
  const summary = $("#mavlinkMonitorSummary");
  const badge = $("#mavlinkMonitorBadge");
  if (summary) {
    const rate = Number(status.rateHz || 0);
    summary.textContent = heartbeat
      ? `已接收 ${receivedTypes.length} 类消息 · ${rate.toFixed(rate >= 10 ? 0 : 1)} Hz`
      : "等待飞控 HEARTBEAT";
  }
  if (badge) {
    badge.textContent = heartbeat ? "在线" : "离线";
    badge.classList.toggle("offline", !heartbeat);
  }
  if (window.getComputedStyle && getComputedStyle(list).display === "none") return;

  const rows = MAVLINK_MONITOR_MESSAGES.map((item) => {
    const count = Number(normalized.counts[item.type] || 0);
    const rate = Number(normalized.rates[item.type] || 0);
    const age = normalized.ages[item.type] !== undefined
      ? Number(normalized.ages[item.type])
      : messageAgeSeconds(normalized.lastSeen[item.type]);
    const missing = count <= 0;
    const stale = age !== null && age > item.maxAge;
    const state = missing ? (item.required ? "missing" : "optional") : stale ? "stale" : "ok";
    const stateText = missing ? (item.required ? "未收到" : "可选") : stale ? `${age.toFixed(1)}s 前` : "正常";
    return `
      <div class="mavlink-row ${state}">
        <div><strong>${item.type}</strong><small>${item.label}</small></div>
        <span>${count || "--"}</span>
        <span>${rate ? rate.toFixed(rate >= 10 ? 0 : 1) : "--"} Hz</span>
        <em>${stateText}</em>
      </div>
    `;
  }).join("");
  list.innerHTML = rows;
}

function renderConnectionSelfCheck(result) {
  const summary = $("#connectionSelfCheckSummary");
  const grid = $("#connectionSelfCheckGrid");
  if (!summary || !grid) return;
  if (!result) {
    summary.innerHTML = `<strong>等待自检</strong><span>连接飞控后点击“立即自检”。</span>`;
    grid.innerHTML = "";
    return;
  }
  const checks = Array.isArray(result.checks) ? result.checks : [];
  const failed = checks.filter((item) => item && item.passed === false);
  const score = result.score ?? checks.filter((item) => item.passed).length;
  const total = result.total ?? checks.length;
  const passed = result.allowed ?? result.ok ?? (!failed.length && total > 0);
  summary.classList.toggle("passed", !!passed);
  summary.classList.toggle("failed", failed.length > 0);
  summary.innerHTML = `
    <strong>${passed ? "连接链路可用" : "连接链路未通过"}</strong>
    <span>${result.summary || `通过 ${score}/${total} 项`}</span>
  `;
  grid.innerHTML = checks.map((item) => `
    <div class="self-check-card ${item.passed ? "passed" : "failed"}">
      <span>${item.passed ? "通过" : "异常"}</span>
      <strong>${escapeAttribute(item.label || item.name || item.key || "检查项")}</strong>
      <small>${escapeAttribute(item.detail || item.message || "--")}</small>
    </div>
  `).join("");
}

let lastConnectionSelfCheckAt = 0;

async function runConnectionSelfCheck(showResultToast = false) {
  try {
    const result = await api("/api/connection/self-check");
    lastConnectionSelfCheckAt = Date.now();
    renderConnectionSelfCheck(result);
    if (showResultToast) {
      const passed = result.allowed ?? result.ok;
      showToast(passed ? "连接自检通过" : "连接自检未通过", result.summary || "请查看自检详情");
    }
    return result;
  } catch (error) {
    renderConnectionSelfCheck({
      allowed: false,
      summary: error.message,
      checks: [{ name: "UI 服务接口", passed: false, detail: error.message }]
    });
    if (showResultToast) showToast("连接自检失败", error.message);
    return null;
  }
}

function refreshConnectionSelfCheckIfVisible() {
  const page = $("#connectionCheckPage");
  if (!page?.classList.contains("active")) return;
  if (Date.now() - lastConnectionSelfCheckAt < 2000) return;
  runConnectionSelfCheck(false);
}

function updateTelemetry(data) {
  if (!data || data.stale || data.connected === false) {
    const packetAgeMs = finite(data?.ageSeconds) ? Number(data.ageSeconds) * 1000 : null;
    const recentLivePacket = lastTelemetryAt && Date.now() - lastTelemetryAt < TELEMETRY_OFFLINE_GRACE_MS;
    const transientOffline = recentLivePacket && (packetAgeMs === null || packetAgeMs < TELEMETRY_OFFLINE_GRACE_MS);
    if (transientOffline) return;
    setTelemetryOffline(data?.stale ? "MAVLink 数据超时" : "未连接");
    return;
  }
  setTelemetryOffline.lastMessage = "";
  latestTelemetry = data;
  const nowMs = Date.now();
  const heavyDue = nowMs - lastHeavyTelemetryUiAt >= 250;
  const readinessDue = nowMs - lastReadinessTelemetryUiAt >= 500;
  const diagnosticDue = nowMs - lastDiagnosticTelemetryUiAt >= 1000;
  const liveTextDue = nowMs - lastLiveTelemetryTextAt >= LIVE_NUMERIC_COMMIT_INTERVAL_MS;
  const chartDue = nowMs - lastChartUiAt >= CHART_REDRAW_INTERVAL_MS;
  const mapDue = nowMs - lastMapVisualAt >= MAP_VISUAL_UPDATE_INTERVAL_MS;
  renderMissionProgress(data);
  if (heavyDue) {
    lastHeavyTelemetryUiAt = nowMs;
    updateTopThrottle(data);
    updateArmButtonState(data);
    updateTopFlightStatusBar(data, true);
  }
  if (readinessDue) {
    lastReadinessTelemetryUiAt = nowMs;
    renderRcLinkStatus(data.rcLink || {}, "gcsRc");
    updateFlightReadiness(data, true);
  }
  if (diagnosticDue) {
    lastDiagnosticTelemetryUiAt = nowMs;
    renderPerformanceDiagnostic(data);
    renderMavlinkMonitor(data.messageStats, data);
    updateRealtimeAlerts(data.warnings || []);
    if ($("#connectionCheckPage")?.classList.contains("active")) {
      window.GCSConnectionDiagnostics?.renderPage(null, data);
    }
  }
  const hasCoordinates = finite(data.lat) && finite(data.lon)
    && Number(data.lat) !== 0 && Number(data.lon) !== 0;
  const hasGpsFix = !finite(data.fixType) || Number(data.fixType) >= 3;
  const altitude = finite(data.relativeAlt) ? Number(data.relativeAlt)
    : finite(data.alt) ? Number(data.alt) : null;
  const speed = finite(data.speed) ? Number(data.speed) : null;
  const airspeed = finite(data.airspeed) ? Number(data.airspeed) : null;
  const heading = finite(data.heading) ? Number(data.heading) : 0;
  const vehicleId = data.vehicleId || "UAV";
  attitudeRender.target.speed = speed;
  if (!attitudeRender.initialized && speed !== null) attitudeRender.current.speed = speed;

  lastTelemetryAt = nowMs;
  const flightState = data.armed ? "已解锁" : "未解锁";
  const modeText = data.mode ? ` · ${data.mode}` : "";
  const demoPrefix = currentOperationMode === "demo" || vehicleId.startsWith("DEMO")
    ? "演示模式：非真实 GPS · " : "";
  if (heavyDue) {
    setTextIfChanged("#telemetryState", `${demoPrefix}PX4 在线 · ${vehicleId} · ${flightState}${modeText}`);
    $("#telemetryState").classList.remove("offline");
    $("#telemetryState").classList.add("online");
    setTextIfChanged("#hudStatus", `${vehicleId} · PX4 在线`);
    setTextIfChanged("#hudMode", data.mode || "UNKNOWN");
    updateFlightModeButtons(data.mode);
    updateFlightModePanel(data, true);
    if ($("#flightModeCommandState") && !$("#flightModeCommandState").textContent.includes("正在请求")) {
      setTextIfChanged("#flightModeCommandState", currentOperationMode === "real_command"
        ? "实机指令模式：可切换飞控实际模式"
        : "当前非实机指令模式，模式切换会被安全层拦截");
    }
    if ($("#hudArmed")) {
      setTextIfChanged("#hudArmed", flightState);
      $("#hudArmed").classList.toggle("armed", !!data.armed);
      $("#hudArmed").classList.toggle("locked", !data.armed);
    }
  }
  if (readinessDue) {
    updateSystemsPanel(data, true);
    updateRealMetricFooters(data, altitude, speed);
  }
  updateAttitude(data);

  if (liveTextDue) {
    lastLiveTelemetryTextAt = nowMs;
    setTextIfChanged("#altitude", altitude !== null ? altitude.toFixed(1) : "--");
    if (speed !== null && !attitudeRender.initialized) setTextIfChanged("#speed", formatLiveSpeed(speed));
    setTextIfChanged("#hudAltitude", altitude !== null ? altitude.toFixed(1) : "--");
    if (speed === null) setTextIfChanged("#hudGroundSpeed", "--");
    if (airspeed !== null) {
      const displayedAirspeed = Math.max(0, airspeed);
      const airspeedState = airspeed < -0.3 ? "空速管需校零" : displayedAirspeed > 0.5 ? "空速管在线" : "低空速";
      setTextIfChanged("#airspeed", displayedAirspeed.toFixed(1));
      setTextIfChanged("#airspeedState", airspeedState);
      setTextIfChanged("#hudAirspeed", displayedAirspeed.toFixed(1));
      setTextIfChanged("#hudAirspeedState", airspeedState);
    } else {
      setTextIfChanged("#airspeed", "--");
      setTextIfChanged("#airspeedState", "等待空速管");
      setTextIfChanged("#hudAirspeed", "--");
      setTextIfChanged("#hudAirspeedState", "等待空速管");
    }
  }
  if (heavyDue) {
    if (finite(data.battery)) setTextIfChanged("#battery", Math.round(Number(data.battery)));
    if (finite(data.satellites)) setTextIfChanged("#satellites", Math.round(Number(data.satellites)));
    if (finite(data.rssi)) setTextIfChanged("#signal", Math.round(Number(data.rssi)));
    setTextIfChanged("#hudBattery", finite(data.battery) ? Math.round(Number(data.battery)) : "--");
  }

  if (!hasCoordinates || !hasGpsFix) {
    if (heavyDue) {
      const satellites = finite(data.satellites) ? Math.round(Number(data.satellites)) : "--";
      const fixType = finite(data.fixType) ? Number(data.fixType) : 0;
      setTextIfChanged("#coordinateText", `GPS 未定位 · ${satellites} 颗卫星 · Fix ${fixType}`);
      setTextIfChanged("#hudFix", `Fix ${fixType} · ${satellites} 星`);
      setTextIfChanged("#hudCoordinate", "--");
      setTextIfChanged("#mapLat", "--");
      setTextIfChanged("#mapLon", "--");
      setTextIfChanged("#mapHeading", `${String(Math.round(heading)).padStart(3, "0")}°`);
      setTextIfChanged("#mapAltitude", altitude !== null ? `${altitude.toFixed(1)} m` : "-- m");
      $("#mapWaiting").classList.remove("hidden");
      setTextIfChanged("#mapWaiting strong", "PX6C 已连接，等待 GPS 定位");
      setTextIfChanged("#mapWaiting small", "请将 GPS 天线移至室外开阔区域");
    }
    if (missionMap && heavyDue) updateMissionVehicleLayer(data);
    if (heavyDue && $("#riskPage")?.classList.contains("active")) renderRiskPage();
    if (chartDue && chartDirty && typeof drawChart === "function") {
      lastChartUiAt = nowMs;
      drawChart();
    }
    return;
  }

  const lat = Number(data.lat);
  const lon = Number(data.lon);
  latestPosition = [lat, lon];
  if (heavyDue) {
    setTextIfChanged("#coordinateText", `${lat.toFixed(6)}°, ${lon.toFixed(6)}°`);
    if ($("#hudFix")) {
      const satellites = finite(data.satellites) ? Math.round(Number(data.satellites)) : "--";
      const fixType = finite(data.fixType) ? Number(data.fixType) : "--";
      setTextIfChanged("#hudFix", `Fix ${fixType} · ${satellites} 星`);
    }
    setTextIfChanged("#hudCoordinate", `${lat.toFixed(6)}, ${lon.toFixed(6)}`);
    setTextIfChanged("#mapLat", lat.toFixed(6));
    setTextIfChanged("#mapLon", lon.toFixed(6));
    setTextIfChanged("#mapHeading", `${String(Math.round(heading)).padStart(3, "0")}°`);
    setTextIfChanged("#mapAltitude", altitude !== null ? `${altitude.toFixed(1)} m` : "-- m");
    $("#mapWaiting").classList.add("hidden");
  }
  if (missionMap && mapDue) updateMissionVehicleLayer(data);

  if (gpsMap && mapDue) {
    lastMapVisualAt = nowMs;
    const icon = getUavMapIcon();
    const label = `${vehicleId} · ${finite(altitude) ? altitude.toFixed(1) + " m" : "--"} · ${speed !== null ? speed.toFixed(1) + " m/s" : "--"}`;
    const previous = trackPoints.at(-1);
    const moved = !previous || Math.abs(previous[0] - lat) > 0.000001 || Math.abs(previous[1] - lon) > 0.000001;

    if (!uavMarker) {
      uavMarker = L.marker(latestPosition, { icon }).addTo(gpsMap);
      uavMarker.bindTooltip(label, {
        permanent: true,
        direction: "right",
        className: "uav-tooltip",
        offset: [14, 0]
      });
    } else {
      uavMarker.setLatLng(latestPosition);
      if (label !== lastMapLabel) uavMarker.setTooltipContent(label);
    }
    lastMapLabel = label;
    updateUavMarkerHeading(heading);

    if (moved) {
      trackPoints.push(latestPosition);
      if (trackPoints.length > 3000) trackPoints.shift();
      flightTrack.setLatLngs(trackPoints);
    }

    if (firstGpsFix) {
      gpsMap.setView(latestPosition, 17);
      firstGpsFix = false;
      showToast("已收到无人机定位", `${vehicleId} 已连接`);
      lastMapFollowAt = nowMs;
    } else if (autoFollow && moved && nowMs - lastMapFollowAt >= MAP_AUTO_FOLLOW_INTERVAL_MS) {
      gpsMap.panTo(latestPosition, { animate: false });
      lastMapFollowAt = nowMs;
    }
    if (data.home_position && finite(data.home_position.latitude) && finite(data.home_position.longitude)) {
      const homePoint = [Number(data.home_position.latitude), Number(data.home_position.longitude)];
      if (!homeMarker) {
        homeMarker = L.circleMarker(homePoint, {
          radius: 7, color: "#f2b84b", fillColor: "#342c1c", fillOpacity: 1, weight: 2
        }).addTo(gpsMap).bindTooltip("Home 点", { permanent: false });
      } else {
        homeMarker.setLatLng(homePoint);
      }
    }
  }

  if (chartDue && altitude !== null && typeof altitudeData !== "undefined") {
    altitudeData = [...altitudeData, altitude].slice(-31);
    chartDirty = true;
  }
  if (chartDue && speed !== null && typeof speedData !== "undefined") {
    speedData = [...speedData, speed].slice(-31);
    chartDirty = true;
  }
  if (heavyDue && $("#riskPage")?.classList.contains("active")) renderRiskPage();
  if (heavyDue && $("#feasibilityPage")?.classList.contains("active")) renderFeasibilityPage();
  if (chartDue && chartDirty && typeof drawChart === "function") {
    lastChartUiAt = nowMs;
    drawChart();
  }
}

function setTelemetryOffline(message = "未连接") {
  const nowMs = Date.now();
  if (setTelemetryOffline.lastMessage === message && nowMs - setTelemetryOffline.lastAt < 1000) return;
  setTelemetryOffline.lastMessage = message;
  setTelemetryOffline.lastAt = nowMs;
  lastTelemetryAt = 0;
  previousTelemetryReceivedAt = 0;
  latestTelemetry = {};
  renderMissionProgress({});
  attitudeRender.initialized = false;
  attitudeRender.target.speed = null;
  updateAttitude.onlineStateApplied = false;
  ensureRealtimeInstruments()?.setOffline();
  updateTopThrottle({});
  updateArmButtonState({ connected: false, armed: false });
  renderRcLinkStatus({ rc_status_text: "Unknown", rc_signal_quality: "unknown", ui_level: "grey", warnings: ["未连接飞控，RC 链路状态不可用"] }, "gcsRc");
  renderPerformanceDiagnostic({ connected: false, messageStats: null, rcLink: { activeFixedProfile: "default" } });
  renderMavlinkMonitor(null, { heartbeat: false, connected: false, rateHz: 0 });
  updateFlightReadiness({ connected: false, heartbeat: false, rateHz: 0, messageStats: null }, false);
  updateTopFlightStatusBar({ connected: false, heartbeat: false, rateHz: 0 }, false);
  updateSystemsPanel(null, false);
  $("#linkFreshness").textContent = "等待遥测";
  updateSignalBars("offline");
  setTrend("altitudeState", "等待高度", "neutral");
  setText("altitudeDetail", "真实遥测");
  setTrend("speedState", "等待地速", "neutral");
  setText("speedDetail", "真实遥测");
  setTrend("batteryState", "等待电池", "neutral");
  setText("batteryDetail", "真实遥测");
  setTrend("gpsState", "等待 GPS", "neutral");
  setText("gpsDetail", "真实遥测");
  setTrend("signalState", "等待链路", "neutral");
  setText("signalDetail", "MAVLink");
  $("#telemetryState").textContent = message;
  $("#telemetryState").classList.remove("online");
  $("#telemetryState").classList.add("offline");
  $("#attitudeState").textContent = "ATTITUDE 离线";
  $("#attitudeState").classList.remove("online");
  $("#attitudeState").classList.add("offline");
  $("#compassState").textContent = "罗盘离线";
  $("#compassState").classList.remove("online");
  $("#compassState").classList.add("offline");
  const instrumentDom = getFlightDisplayDom();
  instrumentDom.smoothAttitudeOffline?.classList.add("visible");
  instrumentDom.smoothCompassOffline?.classList.add("visible");
  if (instrumentDom.smoothAttitudeDebug) instrumentDom.smoothAttitudeDebug.textContent = `age -- · fps -- · ${telemetryTransportMode}`;
  if (instrumentDom.smoothCompassDebug) instrumentDom.smoothCompassDebug.textContent = `age -- · fps -- · ${telemetryTransportMode}`;
  if ($("#hudStatus")) $("#hudStatus").textContent = message;
  if ($("#hudMode")) $("#hudMode").textContent = "UNKNOWN";
  updateFlightModeButtons("");
  updateFlightModePanel({}, false);
  if ($("#flightModeCommandState")) $("#flightModeCommandState").textContent = "等待 MAVLink 连接后才能切换飞行模式";
  if ($("#hudArmed")) {
    $("#hudArmed").textContent = "未解锁";
    $("#hudArmed").classList.remove("armed");
    $("#hudArmed").classList.add("locked");
  }
  if ($("#hudPitch")) $("#hudPitch").textContent = "--.-°";
  if ($("#hudRoll")) $("#hudRoll").textContent = "--.-°";
  if ($("#hudYaw")) $("#hudYaw").textContent = "---.-°";
  if ($("#hudHeading")) $("#hudHeading").textContent = "---°";
  if ($("#hudHeadingCardinal")) $("#hudHeadingCardinal").textContent = "---";
  if ($("#hudAltitude")) $("#hudAltitude").textContent = "--";
  if ($("#hudGroundSpeed")) $("#hudGroundSpeed").textContent = "--";
  if ($("#hudAirspeed")) $("#hudAirspeed").textContent = "--";
  if ($("#hudAirspeedState")) $("#hudAirspeedState").textContent = "等待空速管";
  if ($("#hudFix")) $("#hudFix").textContent = "未定位";
  if ($("#hudBattery")) $("#hudBattery").textContent = "--";
  if ($("#hudCoordinate")) $("#hudCoordinate").textContent = "--";
  if ($("#mapLat")) $("#mapLat").textContent = "--";
  if ($("#mapLon")) $("#mapLon").textContent = "--";
  if ($("#mapHeading")) $("#mapHeading").textContent = "---°";
  if ($("#mapAltitude")) $("#mapAltitude").textContent = "-- m";
  $("#connectionRoll").textContent = "--.-°";
  $("#connectionPitch").textContent = "--.-°";
  $("#connectionYaw").textContent = "---.-°";
  if ($("#riskPage")?.classList.contains("active")) renderRiskPage();
}

async function pollTelemetry() {
  if (pollTelemetry.streamActive) return;
  if (typeof fetch !== "function") return;
  if (pollTelemetry.inFlight) return;
  pollTelemetry.inFlight = true;
  try {
    const response = await fetch(`/api/telemetry?t=${Date.now()}`, {
      cache: "no-store"
    });
    if (!response.ok) return;
    const data = await response.json();
    if (!data || !data.vehicleId) {
      setTelemetryOffline("未连接");
      pollTelemetry.lastReceivedAt = null;
      return;
    }
    handleTelemetryPacket(data);
  } catch (error) {
    if (!lastTelemetryAt) {
      $("#telemetryState").textContent = "UI 服务连接失败";
    }
  } finally {
    pollTelemetry.inFlight = false;
  }
}
pollTelemetry.lastReceivedAt = null;
pollTelemetry.lastPacketKey = null;
pollTelemetry.inFlight = false;
pollTelemetry.streamActive = false;
let telemetryEventSource = null;
let telemetryFallbackTimer = null;
let pendingTelemetryFrame = null;
let telemetryFrameScheduled = false;

function handleTelemetryPacket(data) {
  if (!data || !data.vehicleId) {
    if (lastTelemetryAt && Date.now() - lastTelemetryAt < TELEMETRY_OFFLINE_GRACE_MS) return;
    setTelemetryOffline("未连接");
    pollTelemetry.lastReceivedAt = null;
    pollTelemetry.lastPacketKey = null;
    return;
  }
  const packetKey = finite(data.packetSeq) ? `seq:${data.packetSeq}` : `time:${data.receivedAt}`;
  if (packetKey === pollTelemetry.lastPacketKey) return;
  pollTelemetry.lastPacketKey = packetKey;
  pollTelemetry.lastReceivedAt = data.receivedAt;
  scheduleTelemetryFrame(data);
}

function scheduleTelemetryFrame(data) {
  pendingTelemetryFrame = data;
  if (telemetryFrameScheduled) return;
  telemetryFrameScheduled = true;
  requestAnimationFrame(() => {
    telemetryFrameScheduled = false;
    const latest = pendingTelemetryFrame;
    pendingTelemetryFrame = null;
    if (latest) updateTelemetry(latest);
  });
}

function startTelemetryFallbackPolling() {
  if (telemetryFallbackTimer || typeof fetch !== "function") return;
  telemetryTransportMode = "poll";
  window.__telemetryTransport = "polling";
  pollTelemetry();
  telemetryFallbackTimer = setInterval(pollTelemetry, TELEMETRY_FETCH_INTERVAL_MS);
}

function stopTelemetryFallbackPolling() {
  if (!telemetryFallbackTimer) return;
  clearInterval(telemetryFallbackTimer);
  telemetryFallbackTimer = null;
}

function startTelemetryStream() {
  if (typeof EventSource !== "function") {
    startTelemetryFallbackPolling();
    return;
  }
  telemetryEventSource = new EventSource("/api/telemetry/stream");
  telemetryEventSource.onopen = () => {
    pollTelemetry.streamActive = true;
    telemetryTransportMode = "sse";
    window.__telemetryTransport = "sse";
    stopTelemetryFallbackPolling();
  };
  telemetryEventSource.onmessage = (event) => {
    try {
      telemetryTransportMode = "sse";
      window.__telemetryTransport = "sse";
      handleTelemetryPacket(JSON.parse(event.data));
    } catch (_) {}
  };
  telemetryEventSource.onerror = () => {
    pollTelemetry.streamActive = false;
    telemetryTransportMode = "fallback";
    startTelemetryFallbackPolling();
  };
}

startTelemetryStream();

window.__receiveTelemetry = (data) => {
  handleTelemetryPacket(data);
};

function pollTelemetryScript() {
  const previous = $("#telemetryScriptPoller");
  if (previous) previous.remove();
  const script = document.createElement("script");
  script.id = "telemetryScriptPoller";
  script.src = `/api/telemetry.js?t=${Date.now()}`;
  script.onerror = () => {
    if (!lastTelemetryAt) $("#telemetryState").textContent = "UI 服务连接失败";
  };
  document.head.appendChild(script);
}
if (typeof fetch !== "function") {
  pollTelemetryScript();
  setInterval(pollTelemetryScript, TELEMETRY_SCRIPT_INTERVAL_MS);
}

let gcsSessionToken = "";

async function ensureGcsSessionToken() {
  if (gcsSessionToken) return gcsSessionToken;
  const response = await fetch("/api/system/status", { cache: "no-store" });
  const data = await response.json();
  gcsSessionToken = data.sessionToken || "";
  return gcsSessionToken;
}

async function readJsonResponse(response, fallbackMessage = "请求失败") {
  const text = await response.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch (parseError) {
    data = null;
    const sanitized = text.replace(/(:|,|\[)\s*(NaN|-?Infinity)(?=\s*[,}\]])/g, "$1 null");
    if (sanitized !== text) {
      try {
        data = JSON.parse(sanitized);
      } catch (_) {
        data = null;
      }
    }
    if (data === null) {
      const snippet = text.trim().slice(0, 180);
      const isHtml = snippet.startsWith("<!DOCTYPE") || snippet.startsWith("<html") || snippet.startsWith("<");
      throw new Error(isHtml
        ? "当前地址返回的是网页，不是后端 JSON 接口。请确认使用 start-ui.cmd 启动 Python 后端，并访问 http://127.0.0.1:8080/"
        : `后端返回非 JSON 内容：${snippet || "空响应"}`);
    }
  }
  if (!response.ok) throw new Error(data.error || data.message || fallbackMessage);
  return data;
}

let currentUser = { authenticated: false, role: "guest", label: "未登录" };

function applyAuthStatus(user = currentUser) {
  currentUser = user || { authenticated: false, role: "guest", label: "未登录" };
  if (!$("#authStatus")) return;
  const label = currentUser.authenticated
    ? `${currentUser.label || currentUser.role} · ${currentUser.username || ""}`.trim()
    : "未登录";
  $("#authStatus").textContent = label;
  $("#authStatus").classList.toggle("admin", currentUser.role === "admin");
  $("#authStatus").classList.toggle("operator", currentUser.role === "operator");
  $("#loginButton").hidden = !!currentUser.authenticated;
  $("#logoutButton").hidden = !currentUser.authenticated;
}

async function refreshAuthStatus() {
  try {
    const response = await fetch("/api/system/status", { cache: "no-store" });
    const status = await response.json();
    if (status.sessionToken) gcsSessionToken = status.sessionToken;
    applyAuthStatus(status.user);
  } catch (_) {
    applyAuthStatus({ authenticated: false, role: "guest", label: "未登录" });
  }
}

function openLoginModal() {
  if (!$("#loginModal")) return;
  $("#loginModal").hidden = false;
  setTimeout(() => $("#loginPassword")?.focus(), 0);
}

function closeLoginModal() {
  if (!$("#loginModal")) return;
  $("#loginModal").hidden = true;
}

$("#loginButton")?.addEventListener("click", openLoginModal);
$("#accountButton")?.addEventListener("click", openLoginModal);
$$(".close-login").forEach((button) => button.addEventListener("click", closeLoginModal));
$("#loginForm")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const result = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({
        username: $("#loginUsername").value.trim(),
        password: $("#loginPassword").value
      })
    });
    applyAuthStatus(result.user);
    closeLoginModal();
    $("#loginPassword").value = "";
    showToast("登录成功", `${result.user.label}权限已启用`);
    refreshSafetyState();
  } catch (error) {
    showToast("登录失败", error.message);
  }
});
$("#logoutButton")?.addEventListener("click", async () => {
  try {
    await api("/api/auth/logout", { method: "POST", body: "{}" });
  } catch (_) {}
  applyAuthStatus({ authenticated: false, role: "guest", label: "未登录" });
  showToast("已退出登录", "危险操作已锁定");
  refreshSafetyState();
});
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const commandEvidenceLabels = new Map();

function commandEvidenceLabel(commandId, fallback = "实机命令") {
  return commandEvidenceLabels.get(commandId) || fallback;
}

function trackCommandEvidence(commandId, label, initialMessage = "命令已加入本地队列，等待发送到飞控") {
  if (!commandId) return;
  commandEvidenceLabels.set(commandId, label || "实机命令");
  renderCommandEvidence({
    status: "queued",
    message: initialMessage,
    commandId,
    results: [],
  }, commandEvidenceLabel(commandId));
}

function latestTelemetryStatusText() {
  const texts = Array.isArray(latestTelemetry?.statustexts) ? latestTelemetry.statustexts : [];
  if (texts.length) return texts.at(-1);
  const warnings = Array.isArray(latestTelemetry?.warnings) ? latestTelemetry.warnings : [];
  if (!warnings.length) return null;
  const text = normalizeWarningItem(warnings.at(-1));
  return text ? { text, severity: "WARNING", source: "PX4 STATUSTEXT" } : null;
}

function commandEvidenceFromStatus(status = {}) {
  const results = Array.isArray(status.results) ? status.results : [];
  const first = results[0] || {};
  const ack = first.ack || first.commandAck || status.ack || null;
  const ackText = ack
    ? `${ack.resultText || ack.result || status.status || "--"}${ack.command !== undefined ? " · CMD " + ack.command : ""}`
    : first.resultText || status.resultText || "--";
  const statustext = first.latestStatustext || first.evidenceText || status.latestStatustext || status.evidenceText;
  const telemetryText = latestTelemetryStatusText();
  return {
    ackText,
    statustext: statustext || telemetryText?.text || "--",
  };
}

function normalizeCommandStatusClass(statusText = "") {
  const normalized = String(statusText || "").toLowerCase().replaceAll("_", "-");
  if (normalized === "sent-no-ack") return "sent-no-ack";
  return normalized || "queued";
}

function renderCommandEvidence(status = {}, label = "实机命令") {
  const panel = $("#commandEvidencePanel");
  if (!panel) return;
  const normalizedClass = normalizeCommandStatusClass(status.status);
  panel.classList.remove("queued", "running", "accepted", "partial", "sent-no-ack", "rejected", "failed", "timeout", "expired", "missing");
  panel.classList.add(normalizedClass);
  const evidence = commandEvidenceFromStatus(status);
  const updated = status.updatedAt || Date.now();
  if ($("#commandEvidenceTitle")) $("#commandEvidenceTitle").textContent = label || "实机命令闭环";
  if ($("#commandEvidenceMessage")) $("#commandEvidenceMessage").textContent = status.message || "等待飞控回执";
  if ($("#commandEvidenceStatus")) $("#commandEvidenceStatus").textContent = status.status || "--";
  if ($("#commandEvidenceAck")) $("#commandEvidenceAck").textContent = evidence.ackText;
  if ($("#commandEvidenceText")) $("#commandEvidenceText").textContent = evidence.statustext;
  if ($("#commandEvidenceTime")) $("#commandEvidenceTime").textContent = updated
    ? new Date(Number(updated)).toLocaleTimeString("zh-CN", { hour12: false })
    : "--";
}

async function waitForCommandStatus(commandId, attempts = 18, intervalMs = 350) {
  if (!commandId) {
    const missing = { status: "missing", message: "缺少命令编号" };
    renderCommandEvidence(missing, "实机命令");
    return missing;
  }
  let commandStatus = null;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    await sleep(intervalMs);
    commandStatus = await api(`/api/command/status?id=${encodeURIComponent(commandId)}`);
    renderCommandEvidence(commandStatus, commandEvidenceLabel(commandId));
    if (!["queued", "running"].includes(commandStatus.status)) break;
  }
  return commandStatus || { status: "queued", message: "仍在等待飞控回执" };
}

async function waitForCommandBatch(commands = []) {
  const statuses = [];
  for (const command of commands) {
    if (command.id && !commandEvidenceLabels.has(command.id)) {
      const label = command.name ? `写入参数 ${command.name}` : command.label || "批量实机命令";
      trackCommandEvidence(command.id, label);
    }
    statuses.push({ ...command, status: await waitForCommandStatus(command.id, 20, 300) });
  }
  return statuses;
}

async function api(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (method !== "GET") {
    const token = await ensureGcsSessionToken();
    if (token) headers["X-GCS-Token"] = token;
  }
  const response = await fetch(path, {
    cache: "no-store",
    ...options,
    headers
  });
  return readJsonResponse(response, "请求失败");
}

async function uploadForm(path, formData) {
  const headers = {};
  const token = await ensureGcsSessionToken();
  if (token) headers["X-GCS-Token"] = token;
  const response = await fetch(path, {
    method: "POST",
    cache: "no-store",
    headers,
    body: formData
  });
  return readJsonResponse(response, "上传失败");
}

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(index ? 1 : 0)} ${units[index]}`;
}

function formatDuration(seconds) {
  if (!Number.isFinite(Number(seconds)) || Number(seconds) < 0) return "--";
  const total = Math.round(Number(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}

function formatDownloadProgress(result = {}) {
  const progress = Number.isFinite(Number(result.progress)) ? `${Number(result.progress).toFixed(1)}%` : "";
  const downloaded = formatBytes(result.downloaded || 0);
  const size = result.size ? formatBytes(result.size) : "--";
  const speed = result.bytesPerSecond ? `${formatBytes(result.bytesPerSecond)}/s` : "--";
  const eta = result.etaSeconds !== null && result.etaSeconds !== undefined ? formatDuration(result.etaSeconds) : "--";
  return `${progress} · ${downloaded}/${size} · ${speed} · 剩余 ${eta}`.trim();
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  }[char]));
}

function markdownPreview(markdown) {
  return markdown.split("\n").slice(0, 260).map((line) => {
    if (line.startsWith("# ")) return `<h1>${escapeHtml(line.slice(2))}</h1>`;
    if (line.startsWith("## ")) return `<h2>${escapeHtml(line.slice(3))}</h2>`;
    if (line.startsWith("### ")) return `<h3>${escapeHtml(line.slice(4))}</h3>`;
    if (line.startsWith("* ")) return `<p class="preview-bullet">• ${escapeHtml(line.slice(2))}</p>`;
    if (line.startsWith("| ")) return `<pre>${escapeHtml(line)}</pre>`;
    return line.trim() ? `<p>${escapeHtml(line)}</p>` : "";
  }).join("");
}

async function refreshSerialPorts() {
  const select = $("#serialPort");
  try {
    const ports = await api("/api/serial-ports");
    select.innerHTML = ports.length
      ? ports.map((port) => `<option value="${port.device}">${port.device} · ${port.description}</option>`).join("")
      : `<option value="">未发现串口</option>`;
    const usbPort = ports.find((port) => /USB|PX4|Pixhawk|串行设备/i.test(port.description));
    if (usbPort) select.value = usbPort.device;
  } catch (error) {
    select.innerHTML = `<option value="">串口扫描失败</option>`;
  }
}

function fillSerialSelect(select, ports, emptyLabel = "未发现串口") {
  if (!select) return;
  select.innerHTML = ports.length
    ? ports.map((port) => `<option value="${port.device}">${port.device} · ${port.description}</option>`).join("")
    : `<option value="">${emptyLabel}</option>`;
  const usbPort = ports.find((port) => /USB|PX4|Pixhawk|串行设备|Serial/i.test(port.description));
  if (usbPort) select.value = usbPort.device;
}

refreshSerialPorts = async function refreshSerialPortsEnhanced() {
  try {
    const ports = await api("/api/serial-ports");
    fillSerialSelect($("#serialPort"), ports);
    fillSerialSelect($("#usbLogPort"), ports);
    if ($("#usbLogStatus")) {
      $("#usbLogStatus").textContent = ports.length ? `发现 ${ports.length} 个串口` : "未发现飞控 USB 串口";
    }
  } catch (error) {
    fillSerialSelect($("#serialPort"), [], "串口扫描失败");
    fillSerialSelect($("#usbLogPort"), [], "串口扫描失败");
    if ($("#usbLogStatus")) $("#usbLogStatus").textContent = "串口扫描失败";
  }
};

let localNetworkIps = [];

function updateUdpListenHint() {
  const hint = $("#udpListenHint");
  const list = $("#localIpList");
  if (!hint) return;
  const localText = localNetworkIps.length ? localNetworkIps.join(" / ") : "未读取到本机 IPv4";
  if (list) list.textContent = `本机 IP：${localText}`;
  hint.classList.remove("warning");
  hint.textContent = `WiFi UDP 连接会先监听本机端口，再主动向目标飞控发送 GCS 心跳。目标飞控 IP 填飞机/数传地址，本机监听地址一般填 0.0.0.0；本机 IP 参考：${localText}。`;
  return true;
}

async function refreshNetworkInterfaces() {
  try {
    const result = await api("/api/network-interfaces");
    localNetworkIps = result.localIps || [];
  } catch (_) {
    localNetworkIps = [];
  }
  updateUdpListenHint();
}

function updateConnectionFields() {
  const type = $("#connectionType").value;
  const serialMode = type === "serial";
  $("#serialPort").disabled = !serialMode;
  $("#baudRate").disabled = !serialMode;
  $$(".udp-fields input").forEach((input) => input.disabled = serialMode || type === "demo");
  $$(".udp-fields").forEach((field) => {
    field.hidden = serialMode || type === "demo";
  });
  updateUdpListenHint();
}
$("#connectionType").addEventListener("change", updateConnectionFields);
updateConnectionFields();
refreshNetworkInterfaces();

$("#listenAddress").addEventListener("input", updateUdpListenHint);
$("#useAnyUdpAddress")?.addEventListener("click", () => {
  $("#listenAddress").value = "0.0.0.0";
  updateUdpListenHint();
});
$("#useDetectedUdpAddress")?.addEventListener("click", () => {
  if (!localNetworkIps.length) return showToast("未读取到本机 IP", "可以先使用 0.0.0.0 监听所有网卡");
  const preferred = localNetworkIps[0];
  $("#listenAddress").value = preferred;
  updateUdpListenHint();
});

function connectionPayload() {
  return {
    type: $("#connectionType").value,
    serialPort: $("#serialPort").value,
    baud: Number($("#baudRate").value),
    listenAddress: $("#listenAddress").value,
    listenPort: Number($("#listenPort").value),
    targetIp: $("#targetIp").value,
    targetPort: Number($("#targetPort").value)
  };
}

async function updateConnectionStatus() {
  try {
    const status = await api("/api/connection/status");
    $("#connectionStatusText").textContent = status.status;
    $("#heartbeatStatus").textContent = status.heartbeat ? "已接收飞控心跳" : "等待心跳";
    $("#receiveRate").textContent = `${status.rateHz || 0} Hz`;
    $("#lastDataTime").textContent = status.lastDataTime
      ? new Date(status.lastDataTime).toLocaleTimeString("zh-CN", { hour12: false })
      : "--";
    $("#packetAge").textContent = status.ageSeconds === null ? "--" : `${status.ageSeconds} s`;
    $("#heartbeatBadge").textContent = status.heartbeat ? "在线" : "无心跳";
    $("#heartbeatBadge").classList.toggle("offline", !status.heartbeat);
    $("#currentModeBadge").textContent = {
      demo: "演示模式", simulation: "仿真模式",
      real_readonly: "实机只读模式", real_command: "实机指令模式"
    }[status.mode] || status.mode;
    currentOperationMode = status.mode;
    $("#operationMode").value = status.mode;
    const diagnosticPageVisible = ["connection", "gcsDiagnostic", "connectionCheck"].includes(activePageName);
    if (diagnosticPageVisible) renderMavlinkMonitor(status.messageStats, status);
    const connectionInfo = window.GCSConnectionDiagnostics?.render(status);
    if (diagnosticPageVisible) window.GCSConnectionDiagnostics?.renderPage(status, latestTelemetry);
    refreshConnectionSelfCheckIfVisible();
    const udpBindNote = status.type === "udp"
      ? `目标飞控 ${status.targetIp || "--"}:${status.targetPort || status.listenPort || "--"} · ${status.effectiveConnection || "等待启动"}`
      : status.type === "udp_listen"
        ? `实际监听 ${status.effectiveListenAddress || status.listenAddress || "0.0.0.0"}:${status.listenPort}`
        : status.status;
    $("#connectionNotice").textContent = status.heartbeat ? status.status : (connectionInfo?.summary || udpBindNote);
    $("#inlineConnectionState").textContent = status.heartbeat
      ? `${status.serialPort || "MAVLink"} 已连接 · ${status.rateHz || 0} Hz`
      : udpBindNote;
    $("#inlineConnectionState").classList.toggle("online", status.heartbeat);
    $("#connectButton").textContent = status.heartbeat ? "重新连接" : "开始连接";
    const onboardModeBadge = $("#onboardLogModeBadge");
    const onboardLinkState = $("#onboardLogLinkState");
    if (onboardModeBadge) {
      onboardModeBadge.textContent = status.heartbeat ? "可读取日志" : "等待飞控心跳";
      onboardModeBadge.classList.toggle("offline", !status.heartbeat);
    }
    if (onboardLinkState) {
      onboardLinkState.textContent = status.heartbeat ? `${status.rateHz || 0} Hz` : "未连接";
      onboardLinkState.classList.toggle("offline", !status.heartbeat);
    }
    if ($("#usbLogStatus") && status.type === "serial") {
      $("#usbLogStatus").textContent = status.heartbeat
        ? `USB 已连接：${status.serialPort || "飞控"} · ${status.rateHz || 0} Hz`
        : `等待 USB 心跳：${status.serialPort || "未选择串口"}`;
    }
  } catch (error) {
    $("#connectionStatusText").textContent = "UI 服务连接失败";
    if ($("#onboardLogModeBadge")) $("#onboardLogModeBadge").textContent = "UI 服务离线";
    if ($("#onboardLogLinkState")) $("#onboardLogLinkState").textContent = "离线";
    if ($("#usbLogStatus")) $("#usbLogStatus").textContent = "UI 服务离线";
  }
}

$("#connectButton").addEventListener("click", async () => {
  try {
    const payload = connectionPayload();
    const status = await api("/api/connection/start", {
      method: "POST", body: JSON.stringify(payload)
    });
    showToast("连接进程已启动", status.type === "serial" ? `正在连接 ${status.serialPort}` : "正在等待 MAVLink 心跳");
    updateConnectionStatus();
  } catch (error) {
    showToast("连接失败", error.message);
  }
});
$("#disconnectButton").addEventListener("click", async () => {
  await api("/api/connection/stop", { method: "POST", body: "{}" });
  showToast("连接已断开", "后台接收进程已停止");
  updateConnectionStatus();
});
$("#refreshPorts").addEventListener("click", refreshSerialPorts);
refreshSerialPorts();
updateConnectionStatus();
setInterval(updateConnectionStatus, CONNECTION_STATUS_INTERVAL_MS);

async function updateLoggingStatus() {
  try {
    const status = await api("/api/logging/status");
    $("#recordingBadge").textContent = status.active ? "正在记录" : "未记录";
    $("#recordingBadge").classList.toggle("active", status.active);
    $("#logFolder").textContent = status.folder || "logs";
    $("#logRows").textContent = `${status.rows || 0} 条`;
    $("#logLastUpdate").textContent = status.lastUpdate
      ? new Date(status.lastUpdate).toLocaleTimeString("zh-CN", { hour12: false }) : "--";
  } catch (_) {}
}
async function startBlackboxRecording() {
  try {
    await api("/api/logging/start", {
      method: "POST", body: JSON.stringify({ name: $("#sessionName").value })
    });
    showToast("黑匣子记录已开始", "遥测、告警、事件、参数和任务将同步保存");
    updateLoggingStatus();
  } catch (error) {
    showToast("无法开始记录", error.message);
  }
}
$("#startLogging").addEventListener("click", startBlackboxRecording);
$("#startBlackboxFromPreflight").addEventListener("click", startBlackboxRecording);
$("#stopLogging").addEventListener("click", async () => {
  await api("/api/logging/stop", { method: "POST", body: "{}" });
  showToast("实时日志已停止", "会话文件已安全关闭");
  updateLoggingStatus();
});
updateLoggingStatus();
setInterval(updateLoggingStatus, LOGGING_STATUS_INTERVAL_MS);

let onboardLogs = [];

async function waitForConnectorCommand(commandId, onUpdate, options = {}) {
  const attempts = options.attempts || 120;
  const delayMs = options.delayMs || 1000;
  let status = null;
  if (commandId && !commandEvidenceLabels.has(commandId)) {
    trackCommandEvidence(commandId, options.label || "实机命令");
  }
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    await sleep(delayMs);
    status = await api(`/api/command/status?id=${encodeURIComponent(commandId)}`);
    renderCommandEvidence(status, commandEvidenceLabel(commandId, options.label || "实机命令"));
    if (onUpdate) onUpdate(status);
    if (!["queued", "running"].includes(status.status)) break;
  }
  return status;
}

function renderOnboardLogOptions(logs) {
  onboardLogs = logs || [];
  const select = $("#onboardLogSelect");
  if (!onboardLogs.length) {
    select.innerHTML = `<option value="">未读取到飞控日志</option>`;
    $("#downloadOnboardLog").disabled = true;
    return;
  }
  select.innerHTML = onboardLogs.map((log) => {
    const sizeText = formatBytes(log.size || 0);
    const timeText = log.timeUtc ? new Date(log.timeUtc * 1000).toLocaleString("zh-CN", { hour12: false }) : "无时间戳";
    return `<option value="${log.id}" data-size="${log.size || 0}" data-time-utc="${log.timeUtc || 0}">#${log.id} · ${sizeText} · ${timeText}</option>`;
  }).join("");
  $("#downloadOnboardLog").disabled = false;
}

async function waitForHeartbeat(timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  let status = null;
  while (Date.now() < deadline) {
    await sleep(800);
    status = await api("/api/connection/status");
    if (status.heartbeat) return status;
  }
  return status;
}

$("#refreshUsbLogPorts")?.addEventListener("click", refreshSerialPorts);

$("#connectUsbForLogs")?.addEventListener("click", async () => {
  const port = $("#usbLogPort")?.value || "";
  const baud = Number($("#usbLogBaud")?.value || 57600);
  if (!port) return showToast("请选择 USB 串口", "先插入飞控 USB 线并刷新串口");
  $("#usbLogStatus").textContent = `正在通过 ${port} 连接飞控...`;
  $("#onboardLogStatus").textContent = "正在切换到 USB 直连模式...";
  try {
    if ($("#connectionType")) $("#connectionType").value = "serial";
    if ($("#serialPort")) $("#serialPort").value = port;
    if ($("#baudRate")) $("#baudRate").value = String(baud);
    updateConnectionFields();
    const status = await api("/api/connection/start", {
      method: "POST",
      body: JSON.stringify({
        type: "serial",
        serialPort: port,
        baud,
        listenAddress: $("#listenAddress")?.value || "0.0.0.0",
        listenPort: Number($("#listenPort")?.value || 14550),
        targetIp: $("#targetIp")?.value || "127.0.0.1",
        targetPort: Number($("#targetPort")?.value || 14550)
      })
    });
    $("#usbLogStatus").textContent = `USB 连接已启动：${status.serialPort || port}`;
    showToast("USB 连接已启动", `正在等待 ${port} 的 MAVLink 心跳`);
    const heartbeatStatus = await waitForHeartbeat();
    updateConnectionStatus();
    if (!heartbeatStatus?.heartbeat) {
      $("#onboardLogStatus").textContent = "USB 已启动，但尚未收到飞控心跳";
      return showToast("USB 未收到心跳", "请确认飞控已上电、串口没有被 QGC 占用");
    }
    $("#usbLogStatus").textContent = `USB 已连接 · ${heartbeatStatus.rateHz || 0} Hz`;
    $("#refreshOnboardLogs").click();
  } catch (error) {
    $("#usbLogStatus").textContent = error.message;
    $("#onboardLogStatus").textContent = error.message;
    showToast("USB 日志连接失败", error.message);
  }
});

$("#refreshOnboardLogs").addEventListener("click", async () => {
  $("#onboardLogStatus").textContent = "正在读取飞控日志列表...";
  $("#downloadOnboardLogLink").hidden = true;
  try {
    const response = await api("/api/flight-logs/list", { method: "POST", body: "{}" });
    if (!response.accepted) {
      $("#onboardLogStatus").textContent = response.reason;
      return showToast("飞控日志读取失败", response.reason);
    }
    trackCommandEvidence(response.commandId, "读取飞控日志列表");
    const status = await waitForConnectorCommand(response.commandId, (item) => {
      $("#onboardLogStatus").textContent = item.message || "正在读取飞控日志列表...";
    }, { attempts: 15, delayMs: 800 });
    if (status?.status !== "accepted") {
      renderOnboardLogOptions([]);
      $("#onboardLogStatus").textContent = status?.message || "未读取到飞控日志";
      return showToast("飞控日志读取失败", $("#onboardLogStatus").textContent);
    }
    renderOnboardLogOptions(status.results || []);
    $("#onboardLogStatus").textContent = `读取到 ${onboardLogs.length} 条飞控 .ulg 日志`;
    showToast("飞控日志列表已更新", `${onboardLogs.length} 条 .ulg`);
  } catch (error) {
    $("#onboardLogStatus").textContent = error.message;
    showToast("飞控日志读取失败", error.message);
  }
});

$("#downloadOnboardLog").addEventListener("click", async () => {
  const option = $("#onboardLogSelect").selectedOptions[0];
  if (!option || option.value === "") return showToast("请选择飞控日志", "请先读取飞控日志列表");
  $("#downloadOnboardLog").disabled = true;
  $("#downloadOnboardLogLink").hidden = true;
  $("#onboardLogStatus").textContent = "正在请求下载飞控 .ulg...";
  try {
    const response = await api("/api/flight-logs/download", {
      method: "POST",
      body: JSON.stringify({
        id: Number(option.value),
        size: Number(option.dataset.size || 0),
        timeUtc: Number(option.dataset.timeUtc || 0)
      })
    });
    if (!response.accepted) {
      $("#onboardLogStatus").textContent = response.reason;
      $("#downloadOnboardLog").disabled = false;
      return showToast("飞控日志下载失败", response.reason);
    }
    trackCommandEvidence(response.commandId, "下载飞控 ULG 日志");
    const status = await waitForConnectorCommand(response.commandId, (item) => {
      const result = item.results?.[0] || {};
      const progress = result.progress !== undefined ? formatDownloadProgress(result) : "";
      $("#onboardLogStatus").textContent = `${item.message || "正在下载飞控 .ulg"} ${progress}`.trim();
    }, { attempts: 7200, delayMs: 1000 });
    const result = status?.results?.[0] || {};
    if (status?.status === "interrupted") {
      $("#onboardLogStatus").textContent = `${status.message || "下载中断"} · 已下载 ${formatBytes(result.downloaded || 0)}，再次点击下载可断点续传`;
      $("#downloadOnboardLog").disabled = false;
      return showToast("飞控日志下载中断", "已保留断点，再次点击下载将继续");
    }
    if (status?.status !== "accepted" || !result.url) {
      $("#onboardLogStatus").textContent = status?.message || "飞控日志下载失败";
      $("#downloadOnboardLog").disabled = false;
      return showToast("飞控日志下载失败", $("#onboardLogStatus").textContent);
    }
    $("#downloadOnboardLogLink").href = result.url;
    $("#downloadOnboardLogLink").download = result.filename || "";
    $("#downloadOnboardLogLink").hidden = false;
    $("#downloadOnboardLog").disabled = false;
    $("#onboardLogStatus").textContent = `下载完成：${result.filename || "flight.ulg"} · ${formatBytes(result.downloaded || result.size || 0)}`;
    showToast("飞控 .ulg 下载完成", "点击“保存 .ulg”即可保存文件");
  } catch (error) {
    $("#onboardLogStatus").textContent = error.message;
    $("#downloadOnboardLog").disabled = false;
    showToast("飞控日志下载失败", error.message);
  }
});

function renderPreflight(result) {
  $("#preflightBadge").textContent = result.allowed ? "检查通过" : "检查未通过";
  $("#preflightBadge").classList.toggle("active", result.allowed);
  $("#preflightSummary").textContent = `${result.summary} · ${result.score}/${result.total}`;
  $("#preflightGrid").innerHTML = (result.checks || []).map((item) => `
    <div class="preflight-item ${item.passed ? "passed" : "failed"}">
      <span>${item.passed ? "通过" : item.blocking ? "阻止" : "提醒"}</span>
      <strong>${escapeHtml(item.label)}</strong>
      <small>${escapeHtml(item.detail)}</small>
    </div>
  `).join("");
}

async function runPreflightCheck(showMessage = true) {
  try {
    const result = await api("/api/preflight/check");
    renderPreflight(result);
    if (showMessage) showToast(result.allowed ? "飞前检查通过" : "飞前检查未通过", result.summary);
    return result;
  } catch (error) {
    if (showMessage) showToast("飞前检查失败", error.message);
    return null;
  }
}
$("#runPreflight").addEventListener("click", () => runPreflightCheck(true));
setInterval(() => {
  if (activePageName !== "tuning" || document.hidden) return;
  runPreflightCheck(false);
}, 2000);

$("#operationMode").addEventListener("change", async (event) => {
  const mode = event.target.value;
  if (mode === "real_command") {
    const accepted = window.confirm(
      "进入实机指令模式后，软件将允许发送解锁、参数写入、任务上传、电机测试等指令。请确认无人机处于安全环境。"
    );
    if (!accepted) {
      event.target.value = "real_readonly";
      return;
    }
  }
  try {
    await api("/api/mode", { method: "POST", body: JSON.stringify({ mode }) });
    showToast("运行模式已切换", event.target.options[event.target.selectedIndex].text);
    refreshSafetyState();
  } catch (error) {
    showToast("模式切换失败", error.message);
  }
});

async function safetyCheck(action, confirmation = "", extra = {}) {
  return api("/api/safety/check", {
    method: "POST",
    body: JSON.stringify({ action, confirmation, ...extra })
  });
}

async function refreshSafetyState() {
  try {
    const status = await api("/api/system/status");
    const calibration = status.safety.calibration;
    $("#calibrationSafety").textContent = calibration.allowed ? "允许" : "禁止";
    $("#calibrationSafety").classList.toggle("offline", !calibration.allowed);
    const servo = status.safety.servoTest;
    $("#servoSafety").textContent = servo.allowed ? "允许" : "禁止";
    $("#servoSafety").classList.toggle("offline", !servo.allowed);
    $("#sendServoTest").disabled = !servo.allowed;
    $("#holdServoTest").disabled = !servo.allowed;
    $("#centerServos").disabled = !servo.allowed;
  } catch (_) {}
}

const calibrationHints = {
  gyro: "陀螺仪校准时请将飞机静置在稳定水平面，校准过程中不要移动。",
  accelerometer: "加速度计校准会要求按方向摆放飞机，请根据飞控提示依次放置。",
  magnetometer: "磁罗盘校准需要远离金属和强磁环境，并按提示旋转飞机。",
  level: "水平姿态校准前请把飞机放在你希望作为水平基准的平面上。",
  radio: "遥控器校准前请打开遥控器，并按提示移动所有摇杆和开关。",
  esc: "电调校准属于高风险操作，必须拆除螺旋桨后再执行。"
};

function updateCalibrationHint() {
  const type = $("#calibrationType").value;
  $("#calibrationHint").textContent = calibrationHints[type] || "请按照 PX4 提示完成校准。";
  $("#calibrationConfirmation").placeholder = type === "esc" ? "请输入：已拆除螺旋桨" : "请输入：已确认安全";
}

$("#calibrationType").addEventListener("change", updateCalibrationHint);
updateCalibrationHint();

function renderCalibrationCommandStatus(status, fallback = "") {
  const result = Array.isArray(status?.results) ? status.results[0] : null;
  const progress = Number.isFinite(Number(result?.progress)) ? Number(result.progress) : null;
  const detail = result?.statusText || result?.text || "";
  const base = status?.message || fallback || "等待飞控返回校准状态";
  $("#calibrationStatus").textContent = progress !== null
    ? `${base} · ${progress}%${detail ? " · " + detail : ""}`
    : `${base}${detail ? " · " + detail : ""}`;
}

$("#startCalibration").addEventListener("click", async () => {
  const type = $("#calibrationType").value;
  const label = $("#calibrationType").selectedOptions[0].textContent;
  const confirmation = $("#calibrationConfirmation").value.trim();
  const result = await safetyCheck("calibration", confirmation, { type });
  if (!result.allowed) return showToast("校准被阻止", result.reason);
  if (!window.confirm(`确认开始${label}？请确认飞控未解锁，飞机处于安全环境。`)) return;
  try {
    const response = await api("/api/calibration/start", {
      method: "POST",
      body: JSON.stringify({ type, confirmation })
    });
    $("#calibrationStatus").textContent = response.reason;
    if (response.allowed && response.commandId) {
      trackCommandEvidence(response.commandId, `${label}校准`);
      const waitOptions = type === "magnetometer"
        ? { attempts: 240, delayMs: 500 }
        : { attempts: 30, delayMs: 500 };
      const status = await waitForConnectorCommand(response.commandId, (item) => {
        renderCalibrationCommandStatus(item, response.reason);
      }, waitOptions);
      renderCalibrationCommandStatus(status, response.reason);
      showToast(status?.status === "accepted" ? "校准已被飞控接收" : "校准未确认", $("#calibrationStatus").textContent);
    } else {
      showToast("校准指令已发送", label);
    }
  } catch (error) {
    showToast("校准失败", error.message);
  }
});

let servoChannelSignature = "";
let servoTestEnabled = false;
let servoMappingRepairs = [];
let servoHoldTimer = null;
let servoHoldActive = false;
let servoHoldChannels = null;
let servoHoldToken = 0;
let motorHoldTimer = null;
let motorHoldActive = false;
let motorHoldToken = 0;
let motorHoldAckChecked = false;

function buildServoControls(channels) {
  const previousValues = new Map($$("[data-servo]").map((input) => [input.dataset.servo, input.value]));
  const normalized = channels.length ? channels : [1, 2, 3, 4].map((channel) => ({ channel, pwm: 1500 }));
  const signature = normalized.map((servo) => servo.channel).join(",");
  if (signature === servoChannelSignature && $("#servoGrid").children.length) return;
  servoChannelSignature = signature;
  $("#servoGrid").innerHTML = normalized.map((servo) => {
    const pwm = Number(previousValues.get(String(servo.channel)) || servo.pwm || 1500);
    const outputFunction = servo.outputFunction ?? "";
    const mappingText = outputFunction === "" ? `${servo.param || "PWM_FUNC"} 未读取` : `${servo.param || "PWM_FUNC"} = ${outputFunction}`;
    return `
      <div class="servo-control">
        <div><strong>舵机 ${servo.channel}</strong><span id="servoValue${servo.channel}">${pwm} μs</span></div>
        <small class="servo-map">${mappingText}</small>
        <input type="range" min="1000" max="2000" value="${pwm}" data-servo="${servo.channel}" data-output-function="${outputFunction}">
        <button class="ghost-button servo-test-one" type="button" data-servo-test="${servo.channel}" data-output-function="${outputFunction}">测试本路</button>
      </div>`;
  }).join("");
  $$("[data-servo]").forEach((input) => input.addEventListener("input", () => {
    $(`#servoValue${input.dataset.servo}`).textContent = `${input.value} μs`;
  }));
  $$("[data-servo-test]").forEach((button) => button.addEventListener("click", () => {
    startServoHold([Number(button.dataset.servoTest)], `舵机 ${button.dataset.servoTest}`);
  }));
}

async function refreshServoLayout(force = false) {
  if (servoTestEnabled && !force) return;
  try {
    const layout = await api("/api/servo/layout");
    buildServoControls(layout.channels || []);
    servoMappingRepairs = layout.recommended || [];
    $("#repairServoMapping").disabled = servoMappingRepairs.length === 0;
    const duplicateText = layout.duplicates?.length
      ? `；注意：功能 ${layout.duplicates.map((item) => `${item.outputFunction} 同时绑定 ${item.channels.join("/")}`).join("，")}`
      : "";
    const repairText = servoMappingRepairs.length
      ? `；建议修正 ${servoMappingRepairs.map((item) => `${item.name}:${item.current || "--"}→${item.recommended}`).join("，")}`
      : "";
    $("#servoDetection").textContent = `${layout.message || `检测到 ${layout.count || 0} 路舵机输出`}${duplicateText}${repairText}`;
  } catch (error) {
    buildServoControls([]);
    servoMappingRepairs = [];
    $("#repairServoMapping").disabled = true;
    $("#servoDetection").textContent = `舵机检测失败：${error.message}`;
  }
}

buildServoControls([]);
refreshServoLayout(true);
setInterval(() => {
  if (activePageName !== "tuning" || document.hidden) return;
  refreshServoLayout(false);
}, 1500);

let motorLayoutSignature = "";

function buildMotorOptions(motors) {
  const motorSelect = $("#motorIndex");
  const normalized = Array.isArray(motors) && motors.length
    ? motors
    : [{ motor: 1, param: "", outputFunction: "", output: "" }];
  const signature = normalized.map((motor) => `${motor.motor}:${motor.param}:${motor.outputFunction}`).join("|");
  if (signature === motorLayoutSignature && motorSelect.children.length) return;
  const previous = motorSelect.value;
  motorLayoutSignature = signature;
  motorSelect.innerHTML = normalized.map((motor) => {
    const outputFunction = motor.outputFunction ?? "";
    const outputText = outputFunction === ""
      ? "等待飞控映射"
      : `${motor.param || motor.output || "PWM_FUNC"}=${outputFunction}`;
    return `<option value="${motor.motor}" data-output-function="${outputFunction}" data-output="${escapeAttribute(motor.output || motor.param || "")}">电机 ${motor.motor} · ${outputText}</option>`;
  }).join("");
  if ([...motorSelect.options].some((option) => option.value === previous)) {
    motorSelect.value = previous;
  }
}

async function refreshMotorLayout() {
  try {
    const layout = await api("/api/motor/layout");
    buildMotorOptions(layout.motors || []);
    const duplicateText = layout.duplicates?.length
      ? `；注意：功能 ${layout.duplicates.map((item) => `${item.outputFunction} 同时绑定 ${item.params.join("/")}`).join("；")}`
      : "";
    $("#motorDetection").textContent = `${layout.message || "正在检测电机输出映射"}${duplicateText}`;
  } catch (error) {
    buildMotorOptions([]);
    $("#motorDetection").textContent = `电机映射检测失败：${error.message}`;
  }
}

buildMotorOptions([]);
refreshMotorLayout();
setInterval(() => {
  if (activePageName !== "tuning" || document.hidden) return;
  refreshMotorLayout();
}, 1500);

$("#enableServoTest").addEventListener("click", async () => {
  const result = await safetyCheck("servo");
  if (!result.allowed) return showToast("舵机测试被阻止", result.reason);
  servoTestEnabled = true;
  await refreshServoLayout(true);
  $("#sendServoTest").disabled = false;
  $("#holdServoTest").disabled = false;
  $("#centerServos").disabled = false;
  showToast("舵机测试已启用", "发送前请确认舵面周围安全");
});

function currentServoOutputs(center = false) {
  return $$("[data-servo]").map((input) => ({
    channel: Number(input.dataset.servo),
    pwm: center ? 1500 : Number(input.value),
    outputFunction: input.dataset.outputFunction || undefined
  }));
}

function servoOutputsForChannels(channels = null, center = false) {
  const selected = channels ? new Set(channels.map(String)) : null;
  return $$("[data-servo]")
    .filter((input) => !selected || selected.has(input.dataset.servo))
    .map((input) => ({
      channel: Number(input.dataset.servo),
      pwm: center ? 1500 : Number(input.value),
      outputFunction: input.dataset.outputFunction || undefined
    }));
}

async function postServoOutputs(outputs) {
  return api("/api/servo/test", {
    method: "POST",
    body: JSON.stringify({ outputs })
  });
}

function updateServoHoldUi() {
  $("#stopServoHold").disabled = !servoHoldActive;
  $("#holdServoTest").textContent = servoHoldActive ? "保持中" : "保持当前输出";
  $$(".servo-test-one").forEach((button) => {
    const active = servoHoldActive && servoHoldChannels?.includes(Number(button.dataset.servoTest));
    button.textContent = active ? "保持中" : "测试本路";
    button.classList.toggle("active", active);
  });
}

async function servoHoldTick(token) {
  if (!servoHoldActive || token !== servoHoldToken) return;
  try {
    const outputs = servoOutputsForChannels(servoHoldChannels, false);
    if (!outputs.length) throw new Error("没有可保持的舵机输出");
    await postServoOutputs(outputs);
    servoHoldTimer = setTimeout(() => servoHoldTick(token), 850);
  } catch (error) {
    servoHoldActive = false;
    clearTimeout(servoHoldTimer);
    updateServoHoldUi();
    showToast("舵机保持已停止", error.message);
  }
}

async function startServoHold(channels = null, label = "当前舵机输出") {
  const result = await safetyCheck("servo");
  if (!result.allowed) return showToast("舵机测试被阻止", result.reason);
  servoTestEnabled = true;
  servoHoldActive = true;
  servoHoldChannels = channels;
  servoHoldToken += 1;
  clearTimeout(servoHoldTimer);
  updateServoHoldUi();
  showToast("舵机保持测试已开始", `${label} 将持续输出，直到点击停止`);
  servoHoldTick(servoHoldToken);
}

async function stopServoHold(center = true) {
  servoHoldActive = false;
  servoHoldChannels = null;
  servoHoldToken += 1;
  clearTimeout(servoHoldTimer);
  updateServoHoldUi();
  if (center) {
    $$("[data-servo]").forEach((input) => {
      input.value = "1500";
      input.dispatchEvent(new Event("input"));
    });
    try {
      await postServoOutputs(currentServoOutputs(true));
    } catch (_) {}
  }
  showToast("舵机保持已停止", center ? "已发送 1500us 中位输出" : "保持循环已停止");
}

async function sendServoOutputs(outputs, label) {
  try {
    const result = await postServoOutputs(outputs);
    if (!result.accepted) return showToast("舵机指令被阻止", result.reason);
    showToast("舵机指令已发送", "等待飞控确认...");
    trackCommandEvidence(result.commandId, label || "舵机测试");
    const commandStatus = await waitForCommandStatus(result.commandId);
    if (commandStatus?.message) {
      showToast("舵机测试回执", commandStatus.message);
    } else {
      showToast("舵机测试回执", result.reason);
    }
    refreshSafetyState();
  } catch (error) {
    showToast("舵机指令失败", error.message);
  }
}

$("#sendServoTest").addEventListener("click", () => {
  sendServoOutputs(currentServoOutputs(false), "确认发送当前舵机 PWM 测试指令");
});
$("#holdServoTest").addEventListener("click", () => {
  startServoHold(null, "全部舵机当前输出");
});
$("#stopServoHold").addEventListener("click", () => {
  stopServoHold(true);
});
$("#centerServos").addEventListener("click", () => {
  if (servoHoldActive) {
    stopServoHold(true);
    return;
  }
  $$("[data-servo]").forEach((input) => {
    input.value = "1500";
    input.dispatchEvent(new Event("input"));
  });
  sendServoOutputs(currentServoOutputs(true), "确认恢复所有舵机到 1500us 中位");
});
$("#repairServoMapping").addEventListener("click", async () => {
  if (!servoMappingRepairs.length) return showToast("无需修正", "当前没有检测到舵机映射建议");
  const confirmation = $("#servoMappingConfirmation").value.trim();
  if (confirmation !== "确认写入参数") return showToast("映射修正被阻止", "请输入：确认写入参数");
  if (!window.confirm("确认写入舵机输出映射参数？写入后建议重启飞控或重新连接数传。")) return;
  try {
    const response = await api("/api/servo/repair-mapping", {
      method: "POST",
      body: JSON.stringify({ confirmation })
    });
    if (!response.accepted) return showToast("映射修正被阻止", response.reason);
    showToast("映射修正已加入队列", response.reason);
    await sleep(1800);
    await refreshServoLayout(true);
  } catch (error) {
    showToast("映射修正失败", error.message);
  }
});
$("#motorThrottle").addEventListener("input", (event) => {
  $("#motorThrottleValue").textContent = `${event.target.value}%`;
});

function selectedMotorCommand(throttleOverride = null, duration = 1.4) {
  const motor = Number($("#motorIndex").value);
  const selectedMotor = $("#motorIndex").selectedOptions[0];
  return {
    motor,
    outputFunction: selectedMotor?.dataset.outputFunction || undefined,
    output: selectedMotor?.dataset.output || undefined,
    throttlePercent: throttleOverride === null ? Number($("#motorThrottle").value) : Number(throttleOverride),
    duration,
    confirmation: $("#propellerConfirmation").value.trim()
  };
}

async function postMotorTest(body) {
  return api("/api/motor/test", {
    method: "POST",
    body: JSON.stringify(body)
  });
}

function updateMotorHoldUi() {
  $("#motorTestButton").textContent = motorHoldActive ? "电机测试保持中" : "开始电机测试";
  $("#stopMotorHold").disabled = !motorHoldActive;
  const confirmed = $("#propellerConfirmation").value.trim() === "已拆除螺旋桨";
  $("#motorTestButton").disabled = !motorHoldActive && !confirmed;
}

async function motorHoldTick(token) {
  if (!motorHoldActive || token !== motorHoldToken) return;
  try {
    const body = selectedMotorCommand(null, 1.4);
    const response = await postMotorTest(body);
    if (!response.accepted) throw new Error(response.reason || "电机测试被阻止");
    if (!motorHoldAckChecked && response.commandId) {
      trackCommandEvidence(response.commandId, "电机测试");
      const commandStatus = await waitForCommandStatus(response.commandId, 12, 250);
      motorHoldAckChecked = true;
      const result = commandStatus.results?.[0] || {};
      const ackText = result.ack?.resultText || commandStatus?.status;
      const methodText = result.method ? ` · ${result.method}` : "";
      if (!["accepted", "partial"].includes(commandStatus?.status)) {
        throw new Error(ackText ? `飞控未确认电机测试：${ackText}${methodText}` : (commandStatus?.message || "飞控未确认电机测试"));
      }
      showToast("电机测试回执", `飞控回执 ${ackText}${methodText}`);
    }
    motorHoldTimer = setTimeout(() => motorHoldTick(token), 850);
  } catch (error) {
    motorHoldActive = false;
    clearTimeout(motorHoldTimer);
    updateMotorHoldUi();
    showToast("电机保持已停止", error.message);
  }
}

async function startMotorHold() {
  const result = await safetyCheck("motor", $("#propellerConfirmation").value.trim());
  if (!result.allowed) return showToast("电机测试被阻止", result.reason);
  motorHoldActive = true;
  motorHoldToken += 1;
  motorHoldAckChecked = false;
  clearTimeout(motorHoldTimer);
  updateMotorHoldUi();
  const selectedMotor = $("#motorIndex").selectedOptions[0];
  showToast("电机持续测试已开始", `${selectedMotor?.textContent || "当前电机"} 将保持输出，直到点击停止`);
  motorHoldTick(motorHoldToken);
}

async function stopMotorHold(sendZero = true) {
  motorHoldActive = false;
  motorHoldToken += 1;
  clearTimeout(motorHoldTimer);
  $("#motorThrottle").value = 0;
  $("#motorThrottleValue").textContent = "0%";
  updateMotorHoldUi();
  if (sendZero && $("#propellerConfirmation").value.trim() === "已拆除螺旋桨") {
    try {
      await postMotorTest(selectedMotorCommand(0, 0.3));
    } catch (_) {}
  }
  showToast("电机测试已停止", "已发送 0% 油门停止输出");
}

$("#propellerConfirmation").addEventListener("input", (event) => {
  if (motorHoldActive && event.target.value.trim() !== "已拆除螺旋桨") {
    stopMotorHold(true);
    return;
  }
  updateMotorHoldUi();
});
$("#motorTestButton").addEventListener("click", async () => {
  if (motorHoldActive) return;
  startMotorHold();
});
$("#stopMotorHold").addEventListener("click", () => stopMotorHold(true));
$("#emergencyStop").addEventListener("click", () => {
  stopMotorHold(true);
});

updateServoHoldUi();
updateMotorHoldUi();

const parameterRows = [
  { name: "MC_ROLLRATE_P", current: 0.15, next: 0.15, group: "横滚", state: "未修改" },
  { name: "MC_ROLLRATE_I", current: 0.2, next: 0.2, group: "横滚", state: "未修改" },
  { name: "MC_ROLLRATE_D", current: 0.003, next: 0.003, group: "横滚", state: "未修改" },
  { name: "MC_PITCHRATE_P", current: 0.15, next: 0.15, group: "俯仰", state: "未修改" },
  { name: "MC_PITCHRATE_I", current: 0.2, next: 0.2, group: "俯仰", state: "未修改" },
  { name: "MC_PITCHRATE_D", current: 0.003, next: 0.003, group: "俯仰", state: "未修改" },
  { name: "MC_YAWRATE_P", current: 0.2, next: 0.2, group: "航向", state: "未修改" },
  { name: "MC_YAWRATE_I", current: 0.1, next: 0.1, group: "航向", state: "未修改" },
  { name: "MC_YAWRATE_D", current: 0.0, next: 0.0, group: "航向", state: "未修改" },
  { name: "FW_RR_P", current: 0.05, next: 0.05, group: "固定翼横滚", state: "未修改" },
  { name: "FW_RR_I", current: 0.05, next: 0.05, group: "固定翼横滚", state: "未修改" },
  { name: "FW_RR_D", current: 0.001, next: 0.001, group: "固定翼横滚", state: "未修改" },
  { name: "FW_PR_P", current: 0.05, next: 0.05, group: "固定翼俯仰", state: "未修改" },
  { name: "FW_PR_I", current: 0.05, next: 0.05, group: "固定翼俯仰", state: "未修改" },
  { name: "FW_PR_D", current: 0.001, next: 0.001, group: "固定翼俯仰", state: "未修改" },
  { name: "FW_YR_P", current: 0.08, next: 0.08, group: "固定翼航向", state: "未修改" },
  { name: "FW_YR_I", current: 0.03, next: 0.03, group: "固定翼航向", state: "未修改" },
  { name: "FW_YR_D", current: 0.0, next: 0.0, group: "固定翼航向", state: "未修改" },
  { name: "MPC_Z_VEL_P_ACC", current: 4.0, next: 4.0, group: "高度", state: "未修改" },
  { name: "MPC_Z_VEL_I_ACC", current: 2.0, next: 2.0, group: "高度", state: "未修改" },
  { name: "MPC_Z_VEL_D_ACC", current: 0.0, next: 0.0, group: "高度", state: "未修改" }
];
let lastAiPidRecommendation = null;

function formatParameterValue(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  return number >= 1 ? number.toFixed(3).replace(/0+$/, "").replace(/\.$/, "") : number.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
}

function renderParameters() {
  const query = $("#parameterSearch").value.trim().toUpperCase();
  $("#parameterTable").innerHTML = parameterRows.filter((row) => row.name.includes(query)).map((row) => `
    <tr data-param="${row.name}">
      <td>${row.name}</td>
      <td>${formatParameterValue(row.current)}</td>
      <td><input type="number" step="0.001" value="${formatParameterValue(row.next)}" data-param-value></td>
      <td>${row.group}</td>
      <td class="queue-state">${row.state}</td>
      <td><button class="ghost-button queue-param">加入队列</button></td>
    </tr>`).join("");
  updateParameterQueueSummary();
}
renderParameters();

function queuedParameters() {
  return parameterRows
    .filter((row) => row.state === "待写入" || row.state === "AI 建议" || Math.abs(Number(row.next) - Number(row.current)) > 0.000001)
    .map((row) => ({
      name: row.name,
      value: Number(row.next),
      oldValue: Number(row.current),
      current: Number(row.current),
      currentSource: row.fromAircraft ? "aircraft" : "ui"
    }));
}

function updateParameterQueueSummary() {
  const target = $("#parameterQueueSummary");
  if (!target) return;
  target.textContent = `${queuedParameters().length} 个参数待写入`;
}

$("#parameterSearch").addEventListener("input", renderParameters);
$("#parameterTable").addEventListener("click", (event) => {
  const button = event.target.closest(".queue-param");
  if (!button) return;
  const row = button.closest("tr");
  const model = parameterRows.find((item) => item.name === row.dataset.param);
  if (model) {
    model.next = Number($("[data-param-value]", row).value);
    model.state = "待写入";
  }
  $(".queue-state", row).textContent = "待写入";
  updateParameterQueueSummary();
});
$("#parameterTable").addEventListener("input", (event) => {
  if (!event.target.matches("[data-param-value]")) return;
  const row = event.target.closest("tr");
  const model = parameterRows.find((item) => item.name === row.dataset.param);
  if (!model) return;
  model.next = Number(event.target.value);
  model.state = "已修改";
  $(".queue-state", row).textContent = "已修改";
  updateParameterQueueSummary();
});
$("#requestParameters").addEventListener("click", async () => {
  try {
    $("#requestParameters").disabled = true;
    showToast("正在读取参数", "已向飞控请求 PID 与输出映射参数");
    const response = await api("/api/parameters/request", {
      method: "POST",
      body: JSON.stringify({ names: parameterRows.map((row) => row.name) })
    });
    if (!response.accepted) return showToast("参数读取未开始", response.reason);
    trackCommandEvidence(response.commandId, "请求 PX4 参数列表");
    const status = await waitForConnectorCommand(response.commandId, null, { attempts: 45, delayMs: 400 });
    const values = status?.results || [];
    values.forEach((item) => {
      const row = parameterRows.find((entry) => entry.name === item.name);
      if (!row) return;
      row.current = Number(item.value);
      row.next = Number(item.value);
      row.fromAircraft = true;
      row.state = "飞控读回";
    });
    renderParameters();
    if (status?.status === "accepted" || status?.status === "partial") {
      showToast("参数读取完成", `${values.length} 个参数已从飞控回填`);
    } else {
      showToast("参数读取超时", status?.message || "未收到飞控参数回执");
    }
  } catch (error) {
    showToast("参数读取失败", error.message);
  } finally {
    $("#requestParameters").disabled = false;
  }
});
async function runAiPidLogAnalysisFromSelectedFile() {
  const file = $("#aiPidLogFile")?.files?.[0];
  if (!file) return false;
  const form = new FormData();
  form.append("file", file);
  form.append("axis", $("#aiPidAxis").value);
  form.append("symptom", $("#aiPidSymptom").value);
  form.append("aggressiveness", $("#aiPidAggressiveness").value);
  form.append("provider", selectedAiPidProvider());
  form.append("modelMode", selectedAiPidModelMode());
  form.append("useSimilarCases", $("#aiUseSimilarCases")?.checked ? "true" : "false");
  $("#aiPidConfidence").textContent = "日志特征提取中";
  const result = await uploadForm("/api/ai/pid/analyze", form);
  renderAiPidAdvisor(result);
  showToast("日志 PID 分析完成", "已按上传的 .ulg 日志生成建议");
  return true;
}

$("#runAiPid").addEventListener("click", async (event) => {
  try {
    if ($("#aiPidLogFile")?.files?.[0]) {
      event.stopImmediatePropagation();
      await runAiPidLogAnalysisFromSelectedFile();
      return;
    }
    const result = await api("/api/pid/ai", {
      method: "POST",
      body: JSON.stringify({
        axis: $("#aiPidAxis").value,
        symptom: $("#aiPidSymptom").value,
        aggressiveness: Number($("#aiPidAggressiveness").value),
        provider: selectedAiPidProvider(),
        modelMode: selectedAiPidModelMode(),
        useSimilarCases: $("#aiUseSimilarCases")?.checked !== false
      })
    });
    lastAiPidRecommendation = result;
    $("#aiPidConfidence").textContent = `置信度 ${Math.round(result.confidence * 100)}%`;
    $("#applyAiPid").disabled = false;
    $("#aiPidResult").innerHTML = `
      <div class="ai-summary"><strong>${result.axisLabel} PID 建议</strong><span>${result.warning}</span></div>
      <div class="ai-param-list">
        ${result.recommendations.map((item) => `
          <div><strong>${item.name}</strong><span>${formatParameterValue(item.current)} → ${formatParameterValue(item.suggested)}</span><small>${item.deltaPercent >= 0 ? "+" : ""}${item.deltaPercent}%</small></div>
        `).join("")}
      </div>
      <ul>${result.reasons.map((reason) => `<li>${reason}</li>`).join("")}</ul>`;
    showToast("AI 分析完成", `${result.axisLabel} PID 建议已生成`);
  } catch (error) {
    showToast("AI 分析失败", error.message);
  }
});
$("#runUlgPid").addEventListener("click", async () => {
  const file = $("#aiPidLogFile").files[0];
  if (!file) return showToast("请选择 ULG 日志", "需要上传 .ulg 文件才能离线分析 PID");
  const form = new FormData();
  form.append("file", file);
  form.append("axis", $("#aiPidAxis").value);
  form.append("symptom", $("#aiPidSymptom").value);
  form.append("aggressiveness", $("#aiPidAggressiveness").value);
  $("#aiPidConfidence").textContent = "日志分析中";
  try {
    const result = await uploadForm("/api/ulg/pid", form);
    lastAiPidRecommendation = result;
    $("#aiPidConfidence").textContent = `日志置信度 ${Math.round(result.confidence * 100)}%`;
    $("#applyAiPid").disabled = false;
    $("#aiPidResult").innerHTML = `
      <div class="ai-summary"><strong>${result.source} · ${result.axisLabel} PID 建议</strong><span>${result.warning}</span></div>
      <div class="ai-param-list">
        ${result.recommendations.map((item) => `
          <div><strong>${item.name}</strong><span>${formatParameterValue(item.current)} → ${formatParameterValue(item.suggested)}</span><small>${item.deltaPercent >= 0 ? "+" : ""}${item.deltaPercent}%</small></div>
        `).join("")}
      </div>
      <ul>${result.reasons.map((reason) => `<li>${reason}</li>`).join("")}</ul>`;
    showToast("日志 PID 分析完成", "建议值已生成，可填入参数表");
  } catch (error) {
    $("#aiPidConfidence").textContent = "分析失败";
    showToast("日志分析失败", error.message);
  }
});
$("#applyAiPid").addEventListener("click", () => {
  if (!lastAiPidRecommendation) return;
  lastAiPidRecommendation.recommendations.forEach((suggestion) => {
    const row = parameterRows.find((item) => item.name === suggestion.name);
    if (!row) return;
    row.current = suggestion.current;
    row.next = suggestion.suggested;
    row.state = "AI 建议";
  });
  renderParameters();
  showToast("已填入参数表", "请检查后再加入队列并应用参数");
});

["runAiPid", "runUlgPid", "applyAiPid", "runAiPidMock", "safeApplyAiPid", "showPidHistory"].forEach((id) => {
  const node = $(`#${id}`);
  if (!node) return;
  const clean = node.cloneNode(true);
  node.replaceWith(clean);
});

function aiMetric(value, unit = "", digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  return `${number.toFixed(digits)}${unit}`;
}

function selectedAiPidProvider() {
  const value = $("#aiPidProvider")?.value || "local";
  return value.startsWith("openai__") ? "openai" : value;
}

function selectedAiPidModelMode() {
  const value = $("#aiPidProvider")?.value || "";
  if (value.startsWith("openai__")) return value.replace("openai__", "");
  return $("#aiModelMode")?.value || "standard_analysis";
}

function syncAiPidModeFromProvider() {
  const modeSelect = $("#aiModelMode");
  if (!modeSelect) return;
  modeSelect.value = selectedAiPidModelMode();
}

function formatUsd(value) {
  const number = Number(value || 0);
  if (!Number.isFinite(number)) return "$--";
  return `$${number.toFixed(number < 0.01 ? 5 : 3)}`;
}

function renderAiCostPanel(targetSelector, usage, summary) {
  const target = $(targetSelector);
  if (!target) return;
  const current = usage || {};
  const totalTokens = current.total_tokens || current.total || "--";
  const cost = current.estimated_cost_usd;
  const today = summary?.today_cost_usd;
  const month = summary?.month_cost_usd;
  target.innerHTML = `
    <div><small>本次模型</small><strong>${escapeHtml(current.model || current.provider || "--")}</strong></div>
    <div><small>本次 tokens</small><strong>${escapeHtml(totalTokens)}</strong></div>
    <div><small>本次成本</small><strong>${formatUsd(cost)}</strong></div>
    <div><small>今日累计</small><strong>${formatUsd(today)}</strong></div>
    <div><small>本月累计</small><strong>${formatUsd(month)}</strong></div>
    <p>最终成本以 API 平台账单为准，此处为估算值。</p>
  `;
}

function renderSimilarCases(targetSelector, cases) {
  const target = $(targetSelector);
  if (!target) return;
  if (!cases || !cases.length) {
    target.innerHTML = `<strong>相似案例</strong><p>未检索到可参考历史案例，本次仅基于当前 verified summary 分析。</p>`;
    return;
  }
  target.innerHTML = `
    <strong>相似案例 Top ${cases.length}</strong>
    <div class="similar-case-list">
      ${cases.map((item) => `
        <div>
          <small>${escapeHtml(item.case_id || "--")} · ${escapeHtml(item.aircraft_type || "unknown")} · ${escapeHtml(item.human_review_status || "unreviewed")}</small>
          <b>${escapeHtml(item.main_issue || "--")}</b>
          <span>${escapeHtml(item.human_confirmed_root_cause || "未人工确认根因")}</span>
        </div>
      `).join("")}
    </div>
  `;
}

function renderAiPidSafety(gate) {
  const target = $("#aiPidSafety");
  if (!target) return;
  if (!gate) {
    target.className = "ai-pid-safety";
    target.textContent = "安全门等待 AI PID Advisor 输出。";
    return;
  }
  target.className = `ai-pid-safety ${gate.allowed ? "pass" : "block"}`;
  const checks = (gate.checks || []).map((item) => `
    <div><small>${item.passed ? "通过" : "阻止"}</small><strong>${escapeHtml(item.message)}</strong></div>
  `).join("");
  const rejected = (gate.rejected || []).map((item) => `
    <div><small>${escapeHtml(item.name)}</small><strong>${escapeHtml((item.checks || []).map((check) => check.message).join("；"))}</strong></div>
  `).join("");
  target.innerHTML = `
    <strong>${escapeHtml(gate.summary || "安全门检查完成")}</strong>
    <div class="ai-safety-checks">${checks}${rejected}</div>
  `;
}

function renderAiPidAdvisor(result) {
  lastAiPidRecommendation = result;
  const providerText = result.providerLabel || (result.provider === "openai" ? "ChatGPT / OpenAI" : "本地工程规则");
  $("#aiPidConfidence").textContent = `${providerText} · 置信度 ${Math.round((result.confidence || 0) * 100)}% · 数据质量 ${Math.round((result.dataQuality || 0) * 100)}%`;
  $("#applyAiPid").disabled = !(result.recommendations || []).length;
  $("#safeApplyAiPid").disabled = !(result.recommendations || []).length;
  const feature = result.featureSummary || {};
  const axis = feature.axis || {};
  $("#aiPidResult").innerHTML = `
    <div class="ai-summary"><strong>${escapeHtml(result.axisLabel || result.axis || "PID")} AI PID Advisor</strong><span>${escapeHtml(providerText)} · ${escapeHtml(result.warning || "")}</span></div>
    <div class="ai-feature-grid">
      <div><small>日志/数据源</small><strong>${escapeHtml(result.source || "--")}</strong></div>
      <div><small>分析时长</small><strong>${aiMetric(feature.duration_s, "s", 1)}</strong></div>
      <div><small>RMS 误差</small><strong>${aiMetric(axis.error_rms, "deg", 2)}</strong></div>
      <div><small>峰值误差</small><strong>${aiMetric(axis.error_peak, "deg", 2)}</strong></div>
      <div><small>振荡</small><strong>${feature.oscillation_detected ? "检测到" : "未确认"}</strong></div>
      <div><small>舵机饱和</small><strong>${aiMetric(feature.servo_output_saturation_percent, "%", 1)}</strong></div>
      <div><small>最低电压</small><strong>${aiMetric(feature.battery_voltage_min, "V", 2)}</strong></div>
      <div><small>缺失数据</small><strong>${(result.missing || []).length ? escapeHtml(result.missing.slice(0, 3).join("、")) : "无关键缺失"}</strong></div>
    </div>
    <table class="ai-param-table">
      <thead><tr><th>参数</th><th>当前值</th><th>建议值</th><th>变化</th><th>依据</th></tr></thead>
      <tbody>${(result.recommendations || []).map((item) => `
        <tr>
          <td>${escapeHtml(item.name)}</td>
          <td>${formatParameterValue(item.current)}</td>
          <td>${formatParameterValue(item.suggested)}</td>
          <td>${item.deltaPercent >= 0 ? "+" : ""}${aiMetric(item.deltaPercent, "%", 2)}</td>
          <td>${escapeHtml(item.reason || "")}</td>
        </tr>
      `).join("") || `<tr><td colspan="5">未生成可写入建议，请查看上方缺失数据和风险说明。</td></tr>`}</tbody>
    </table>
    <ul>${[...(result.reasons || []), ...(result.risks || [])].map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
  `;
  renderAiPidSafety(result.safetyPreview);
  renderAiCostPanel("#aiPidCostPanel", result.tokenUsage || result.token_usage, result.usageSummary);
  renderSimilarCases("#aiPidSimilarCasesPanel", result.similarCases || result.similar_cases);
}

async function loadAiPidStatus() {
  try {
    const status = await api("/api/ai/status");
    const select = $("#aiPidMockCase");
    if (select) {
      select.innerHTML = (status.mockCases || []).map((item) => `<option value="${escapeAttribute(item.id)}">${escapeHtml(item.label || item.id)}</option>`).join("");
    }
    const providerSelect = $("#aiPidProvider");
    if (providerSelect) {
      const openaiOption = providerSelect.querySelector('option[value="openai"]');
      if (openaiOption) {
        const llm = status.llm || {};
        openaiOption.textContent = llm.openaiConfigured
          ? `ChatGPT / OpenAI (${llm.openaiModel || "已配置"})`
          : "ChatGPT / OpenAI（未配置 Key）";
      }
      if ((status.llm || {}).provider === "openai" && (status.llm || {}).openaiConfigured) {
        providerSelect.value = "openai";
      }
    }
  } catch (_) {
    if ($("#aiPidMockCase")) $("#aiPidMockCase").innerHTML = `<option value="">Mock 不可用</option>`;
  }
}

$("#runAiPid").addEventListener("click", async () => {
  try {
    $("#aiPidConfidence").textContent = "实时数据分析中";
    const result = await api("/api/ai/pid/analyze", {
      method: "POST",
      body: JSON.stringify({
        source: "live",
        axis: $("#aiPidAxis").value,
        symptom: $("#aiPidSymptom").value,
        aggressiveness: Number($("#aiPidAggressiveness").value),
        provider: selectedAiPidProvider(),
        modelMode: selectedAiPidModelMode(),
        useSimilarCases: $("#aiUseSimilarCases")?.checked !== false
      })
    });
    renderAiPidAdvisor(result);
    showToast("AI PID Advisor 完成", `${result.axisLabel} 建议已生成`);
  } catch (error) {
    $("#aiPidConfidence").textContent = "分析失败";
    showToast("AI PID 分析失败", error.message);
  }
});

$("#runAiPidMock")?.addEventListener("click", async () => {
  try {
    $("#aiPidConfidence").textContent = "Mock 分析中";
    const result = await api("/api/ai/pid/analyze", {
      method: "POST",
      body: JSON.stringify({
        source: "mock",
        caseId: $("#aiPidMockCase")?.value || "",
        axis: $("#aiPidAxis").value,
        symptom: $("#aiPidSymptom").value,
        aggressiveness: Number($("#aiPidAggressiveness").value),
        provider: selectedAiPidProvider(),
        modelMode: selectedAiPidModelMode(),
        useSimilarCases: $("#aiUseSimilarCases")?.checked !== false
      })
    });
    renderAiPidAdvisor(result);
    showToast("Mock 分析完成", "可用于演示 AI PID Advisor 输出格式");
  } catch (error) {
    showToast("Mock 分析失败", error.message);
  }
});

$("#runUlgPid").addEventListener("click", async () => {
  const file = $("#aiPidLogFile").files[0];
  if (!file) return showToast("请选择 ULG 日志", "需要上传 .ulg 文件才能离线分析 PID");
  const form = new FormData();
  form.append("file", file);
  form.append("axis", $("#aiPidAxis").value);
  form.append("symptom", $("#aiPidSymptom").value);
  form.append("aggressiveness", $("#aiPidAggressiveness").value);
  form.append("provider", selectedAiPidProvider());
  form.append("modelMode", selectedAiPidModelMode());
  $("#aiPidConfidence").textContent = "日志特征提取中";
  try {
    const result = await uploadForm("/api/ai/pid/analyze", form);
    renderAiPidAdvisor(result);
    showToast("日志 PID 分析完成", "已按结构化特征生成建议");
  } catch (error) {
    $("#aiPidConfidence").textContent = "分析失败";
    showToast("日志分析失败", error.message);
  }
});

$("#applyAiPid").addEventListener("click", () => {
  if (!lastAiPidRecommendation) return;
  (lastAiPidRecommendation.recommendations || []).forEach((suggestion) => {
    let row = parameterRows.find((item) => item.name === suggestion.name);
    if (!row) {
      row = { name: suggestion.name, current: suggestion.current, next: suggestion.current, group: "AI PID", state: "未修改" };
      parameterRows.push(row);
    }
    row.current = suggestion.current;
    row.next = suggestion.suggested;
    row.state = "AI 建议";
  });
  renderParameters();
  showToast("已填入参数表", "请检查后再应用，或使用安全写入队列");
});

$("#safeApplyAiPid")?.addEventListener("click", async () => {
  if (!lastAiPidRecommendation) return;
  try {
    const result = await api("/api/ai/pid/apply", {
      method: "POST",
      body: JSON.stringify({
        source: lastAiPidRecommendation.source || "AI PID Advisor",
        recommendations: lastAiPidRecommendation.recommendations || [],
        confirmation: $("#parameterConfirmation").value.trim()
      })
    });
    renderAiPidSafety(result.safetyGate);
    if (!result.accepted) return showToast("AI PID 写入被阻止", result.reason);
    showToast("AI PID 已加入队列", `回滚快照：${result.rollbackId}`);
    const statuses = await waitForCommandBatch(result.queued || []);
    const accepted = statuses.filter((item) => item.status?.status === "accepted").length;
    showToast("AI PID 写入回执", `${accepted}/${statuses.length} 个参数已被飞控确认`);
  } catch (error) {
    showToast("AI PID 写入失败", error.message);
  }
});

$("#showPidHistory")?.addEventListener("click", async () => {
  try {
    const result = await api("/api/ai/pid/history");
    const history = result.history || [];
    $("#aiPidResult").innerHTML = `
      <div class="ai-summary"><strong>AI PID 回滚历史</strong><span>${history.length} 条最近记录</span></div>
      <table class="ai-param-table">
        <thead><tr><th>快照</th><th>来源</th><th>参数</th><th>状态</th></tr></thead>
        <tbody>${history.map((item) => `
          <tr>
            <td>${escapeHtml(item.id)}</td>
            <td>${escapeHtml(item.source || "--")}</td>
            <td>${escapeHtml((item.items || []).map((param) => param.name).join("、"))}</td>
            <td>${item.rolledBack ? "已回滚" : item.applied ? "已应用" : "未应用"}</td>
          </tr>
        `).join("") || `<tr><td colspan="4">暂无回滚快照。</td></tr>`}</tbody>
      </table>
    `;
  } catch (error) {
    showToast("回滚历史读取失败", error.message);
  }
});

async function refreshAiPidProviderOptions() {
  const providerSelect = $("#aiPidProvider");
  if (!providerSelect) return;
  try {
    const status = await api("/api/ai/status");
    const modes = status.aiConfig?.modes || [];
    const openaiConfigured = !!status.llm?.openaiConfigured;
    providerSelect.innerHTML = `
      <option value="local">本地工程规则</option>
      ${modes.map((mode) => `
        <option value="openai__${escapeAttribute(mode.mode)}" ${mode.mode === "standard_analysis" ? "selected" : ""}>
          ChatGPT / OpenAI - ${escapeHtml(mode.label || mode.mode)} (${escapeHtml(mode.model || "--")})
        </option>
      `).join("")}
    `;
    providerSelect.value = openaiConfigured ? "openai__standard_analysis" : "local";
    providerSelect.onchange = syncAiPidModeFromProvider;
    syncAiPidModeFromProvider();
  } catch (_) {}
}

loadAiPidStatus().then(refreshAiPidProviderOptions);
let lastGeneratedReportPath = "";

$("#generateUlgReport").addEventListener("click", async () => {
  const file = $("#ulgReportFile").files[0];
  if (!file) return showToast("请选择飞行日志", "需要上传 .ulg / .csv / .json / .txt 文件生成报告");
  const form = new FormData();
  form.append("file", file);
  form.append("language", $("#reportLanguage").value);
  form.append("detailLevel", $("#reportDetailLevel").value);
  form.append("includeCharts", $("#reportIncludeCharts").checked);
  form.append("includePid", $("#reportIncludePid").checked);
  form.append("includeBattery", $("#reportIncludeBattery").checked);
  form.append("includeGps", $("#reportIncludeGps").checked);
  form.append("includeSensors", $("#reportIncludeSensors").checked);
  form.append("includeEvents", $("#reportIncludeEvents").checked);
  form.append("includeRecommendations", $("#reportIncludeRecommendations").checked);
  $("#ulgReportBadge").textContent = "生成中";
  $("#ulgReportStatus").textContent = "正在解析日志 → 计算指标 → 检测异常 → 生成图表 → 生成报告...";
  $("#reportPreview").textContent = "正在生成报告，请稍候...";
  $("#downloadUlgReport").hidden = true;
  $("#downloadPdfReport").hidden = true;
  $("#downloadMarkdownReport").hidden = true;
  $("#downloadHtmlReport").hidden = true;
  try {
    const result = await uploadForm("/api/ulg/report", form);
    $("#ulgReportBadge").textContent = "已生成";
    $("#ulgReportBadge").classList.add("active");
    $("#ulgReportStatus").textContent = `报告完成：${result.summary.durationSeconds}s，${result.summary.topics} 个字段/Topic，${result.summary.charts.length} 张图表，风险 ${result.summary.risk}`;
    $("#reportCardGrid").innerHTML = Object.entries(result.cards || {}).map(([key, value]) => `
      <div><small>${escapeHtml(key)}</small><strong>${escapeHtml(value)}</strong></div>
    `).join("");
    if (result.reportData?.metadata) {
      const data = result.reportData;
      const verified = data.verified_analysis_summary || {};
      const dataQuality = verified.data_quality || data.metadata.data_quality || {};
      const flightEvents = verified.flight_events || data.metadata.effective_flight || {};
      const unreliable = verified.unreliable_data || [];
      const extraCards = {
        "结构化底稿": data.schemaVersion,
        "机型识别置信度": `${Math.round((data.metadata.airframe?.confidence || 0) * 100)}%`,
        "数据质量等级": `${dataQuality.level || "--"} ${dataQuality.score ? `· ${dataQuality.score}` : ""}`,
        "有效飞行段": flightEvents.effective_airborne_time !== null && flightEvents.effective_airborne_time !== undefined ? `${Number(flightEvents.effective_airborne_time).toFixed(1)}s` : "N/A",
        "阶段分析条目": `${data.attitude_phase_analysis?.axis_metrics?.length || 0} 条`,
        "缺失数据项": `${data.missing_data?.length || 0} 项`,
        "不可信数据": unreliable.length ? unreliable.map((item) => item.name).join("、") : "未标记",
        "人工复核": (dataQuality.limitations || []).length ? "需要" : "常规复核",
        "告警/事件": `${data.flight_summary?.warnings || 0}/${data.flight_summary?.events || 0}`,
      };
      $("#reportCardGrid").innerHTML += Object.entries(extraCards).map(([key, value]) => `
        <div><small>${escapeHtml(key)}</small><strong>${escapeHtml(value)}</strong></div>
      `).join("");
    }
    $("#reportChartPreview").innerHTML = (result.chartUrls || []).map((chart) => `
      <figure><img src="${chart.url}" alt="${escapeHtml(chart.caption)}"><figcaption>${escapeHtml(chart.caption)}</figcaption></figure>
    `).join("");
    $("#reportPreview").innerHTML = markdownPreview(result.preview || "报告预览为空");
    $("#downloadUlgReport").href = result.reportUrl;
    $("#downloadPdfReport").href = result.pdfUrl;
    $("#downloadMarkdownReport").href = result.markdownUrl;
    $("#downloadHtmlReport").href = result.htmlUrl;
    lastGeneratedReportPath = result.reportPath || "";
    if ($("#openReportFolder")) $("#openReportFolder").disabled = !lastGeneratedReportPath;
    $("#downloadUlgReport").hidden = false;
    $("#downloadPdfReport").hidden = false;
    $("#downloadMarkdownReport").hidden = false;
    $("#downloadHtmlReport").hidden = false;
    showToast("飞行报告已生成", "可预览并导出 Word / PDF / Markdown / HTML");
  } catch (error) {
    $("#ulgReportBadge").textContent = "失败";
    $("#ulgReportStatus").textContent = error.message;
    $("#reportPreview").textContent = `报告生成失败：${error.message}`;
    $("#reportChartPreview").innerHTML = "";
    showToast("报告生成失败", error.message);
  }
});

$("#ulgReportFile").addEventListener("change", () => {
  const file = $("#ulgReportFile").files[0];
  $("#reportFileInfo").textContent = file
    ? `已选择：${file.name} · ${formatBytes(file.size)} · 等待解析`
    : "尚未选择文件";
});

$("#openReportFolder")?.addEventListener("click", async () => {
  if (!lastGeneratedReportPath) return showToast("暂无报告路径", "请先生成飞行日志分析报告");
  try {
    await navigator.clipboard.writeText(lastGeneratedReportPath);
    showToast("报告路径已复制", lastGeneratedReportPath);
  } catch (_) {
    showToast("报告保存路径", lastGeneratedReportPath);
  }
});

async function loadAiReportStatus() {
  if (!$("#aiReportStatus")) return;
  try {
    const status = await api("/api/ai-report/status");
    const modeSelect = $("#aiReportModelMode");
    if (modeSelect && Array.isArray(status.modes) && status.modes.length) {
      modeSelect.innerHTML = status.modes.map((mode) => `
        <option value="${escapeAttribute(mode.mode)}" ${mode.mode === status.mode ? "selected" : ""}>
          ChatGPT / OpenAI - ${escapeHtml(mode.label || mode.mode)} (${escapeHtml(mode.model || "--")})
        </option>
      `).join("");
      if ([...modeSelect.options].some((option) => option.value === status.mode)) {
        modeSelect.value = status.mode;
      }
    }
    const label = `${status.provider || "openai"} / ${status.model || "--"} / ${status.engine || "openai_chat_completions"}`;
    const fallbackText = (status.fallback_models || []).length
      ? `；备用模型：${(status.fallback_models || []).join(" / ")}`
      : "";
    const warnings = status.configuration_warnings || [];
    $("#aiReportBadge").textContent = status.available ? "AI 可用" : "AI 未配置";
    $("#aiReportBadge").classList.toggle("active", !!status.available);
    $("#aiReportStatus").textContent = status.available
      ? `OpenAI 报告引擎配置完成：${label}${fallbackText}`
      : "OpenAI 报告引擎未就绪，请检查 .env 中的 OPENAI_API_KEY、AI_REPORT_MODEL 和 AI_REPORT_PROVIDER";
    if ($("#aiReportNotice")) {
      $("#aiReportNotice").textContent = warnings.length
        ? `AI 报告安全边界：只做分析，不控制飞控，不自动改 PID。配置提示：${warnings.join(" ")}`
        : "AI 报告基于算法分析结果生成，仅用于辅助分析，不能替代人工工程判断；AI 不直接控制飞控，不在飞行中自动修改 PID。";
    }
  } catch (error) {
    $("#aiReportStatus").textContent = `AI 状态读取失败：${error.message}`;
  }
}

function renderAiReportCards(result) {
  const quality = result.data_quality || {};
  const tokenUsage = result.token_usage || {};
  const cards = {
    "报告类型": "AI Report",
    "日志文件": result.source || "--",
    "AI 引擎": result.provider === "openai" ? "OpenAI" : (result.provider || "--"),
    "AI 模型": result.model || "--",
    "生成时间": result.generated_at || "--",
    "数据质量": `${quality.level || "--"} ${quality.score ? `/ ${quality.score}` : ""}`,
    "缺失数据": `${(result.missing_data || []).length} 项`,
    "人工复核": (result.warnings || []).length ? "需要" : "常规",
    "Token": tokenUsage.total_tokens || tokenUsage.total || "--",
  };
  $("#aiReportCardGrid").innerHTML = Object.entries(cards).map(([key, value]) => `
    <div><small>${escapeHtml(key)}</small><strong>${escapeHtml(value)}</strong></div>
  `).join("");
}

$("#generateAiReport")?.addEventListener("click", async () => {
  const file = $("#ulgReportFile").files[0];
  if (!file) return showToast("请选择飞行日志", "AI 报告使用上方同一个 .ulg / .csv / .json / .txt 日志文件");
  const form = new FormData();
  form.append("file", file);
  form.append("language", $("#reportLanguage")?.value || "zh");
  form.append("detailLevel", $("#aiReportDetailLevel")?.value || "standard");
  form.append("audience", $("#aiReportAudience")?.value || "engineering");
  form.append("includePidAdvice", $("#aiReportIncludePid")?.checked ? "true" : "false");
  form.append("modelMode", $("#aiReportModelMode")?.value || "standard_analysis");
  form.append("useSimilarCases", $("#aiReportUseSimilarCases")?.checked ? "true" : "false");

  $("#aiReportBadge").textContent = "生成中";
  $("#aiReportBadge").classList.add("active");
  $("#aiReportStatus").textContent = "正在解析日志 → 构建 verified summary → 调用 AI → 导出 AI 报告...";
  $("#aiReportPreview").textContent = "AI 报告正在生成，请稍候。算法报告功能不受影响。";
  $("#downloadAiReportDocx").hidden = true;
  $("#downloadAiReportMarkdown").hidden = true;
  $("#downloadAiReportHtml").hidden = true;
  $("#aiReportCardGrid").innerHTML = "";

  try {
    const result = await uploadForm("/api/ai-report/generate", form);
    const usedFallback = result.engine === "local_fallback_after_openai_failure" || result.openai_success === false;
    $("#aiReportBadge").textContent = usedFallback ? "本地回退完成" : "AI 完成";
    $("#aiReportStatus").textContent = result.engine === "local_fallback_after_openai_failure"
      ? `OpenAI 调用未完成，但已基于 verified summary 生成可导出的本地工程报告 · ${result.generated_at || "--"}`
      : `AI 报告完成：${result.model || "--"} · ${result.generated_at || "--"}`;
    renderAiReportCards(result);
    $("#aiReportPreview").innerHTML = markdownPreview(result.preview || result.report_markdown || "AI 报告预览为空");
    $("#downloadAiReportDocx").href = result.files?.docxUrl || "#";
    $("#downloadAiReportMarkdown").href = result.files?.markdownUrl || "#";
    $("#downloadAiReportHtml").href = result.files?.htmlUrl || "#";
    $("#downloadAiReportDocx").hidden = !result.files?.docxUrl;
    $("#downloadAiReportMarkdown").hidden = !result.files?.markdownUrl;
    $("#downloadAiReportHtml").hidden = !result.files?.htmlUrl;
    renderAiCostPanel("#aiReportCostPanel", result.token_usage, result.usage_summary);
    renderSimilarCases("#aiSimilarCasesPanel", result.similar_cases || result.similarCases);
    showToast(usedFallback ? "本地回退报告已生成" : "AI 报告已生成", "可单独导出 Word / Markdown / HTML，原算法报告未被覆盖");
  } catch (error) {
    $("#aiReportBadge").textContent = "AI 失败";
    $("#aiReportStatus").textContent = error.message;
    $("#aiReportPreview").textContent = `AI 报告生成失败：${error.message}\n\n算法报告功能仍可正常使用。`;
    showToast("AI 报告生成失败", error.message);
  }
});

loadAiReportStatus();

function optionalMetric(value, unit = "", digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  return `${Number(value).toFixed(digits)}${unit}`;
}

function selectedFilesLabel(files) {
  const list = [...files];
  if (!list.length) return "尚未选择文件";
  const total = list.reduce((sum, file) => sum + file.size, 0);
  return `${list.length} 份日志 · ${formatBytes(total)}`;
}

function appendLogFiles(form, files) {
  [...files].forEach((file) => form.append("files", file));
}

function renderCardGrid(target, cards) {
  target.innerHTML = Object.entries(cards || {}).map(([key, value]) => `
    <div><small>${escapeHtml(key)}</small><strong>${escapeHtml(value)}</strong></div>
  `).join("");
}

$("#compareLogFiles")?.addEventListener("change", () => {
  $("#compareFileInfo").textContent = selectedFilesLabel($("#compareLogFiles").files);
});

$("#runLogCompare")?.addEventListener("click", async () => {
  const files = $("#compareLogFiles").files;
  if (files.length < 2) return showToast("请选择至少 2 份日志", "多日志对比需要基准日志和对比日志");
  const form = new FormData();
  appendLogFiles(form, files);
  $("#compareBadge").textContent = "分析中";
  $("#compareRecommendations").textContent = "正在解析日志并计算对比指标...";
  try {
    const result = await uploadForm("/api/ulg/compare", form);
    $("#compareBadge").textContent = "已完成";
    $("#compareBadge").classList.add("active");
    renderCardGrid($("#compareCardGrid"), result.cards);
    $("#compareTable").innerHTML = (result.logs || []).map((item, index) => `
      <tr>
        <td>${index === 0 ? "基准 · " : ""}${escapeHtml(item.source)}</td>
        <td>${optionalMetric(item.durationSeconds, "s", 1)}</td>
        <td>${optionalMetric(item.maxSpeed, "m/s")}</td>
        <td>${optionalMetric(item.maxAltitude, "m")}</td>
        <td>${optionalMetric(item.attitudeRms, "deg")}</td>
        <td>${optionalMetric(item.gyroStd)}</td>
        <td>${optionalMetric(item.minSatellites, "", 0)}</td>
        <td>${optionalMetric(item.minVoltage, "V")}</td>
        <td><strong>${item.sensorHealthScore ?? "--"}</strong></td>
        <td>${item.eventCount ?? 0}</td>
      </tr>
    `).join("");
    $("#compareRecommendations").innerHTML = `<ul>${(result.recommendations || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
    showToast("日志对比完成", `${result.count} 份日志已完成横向分析`);
  } catch (error) {
    $("#compareBadge").textContent = "失败";
    $("#compareRecommendations").textContent = error.message;
    showToast("日志对比失败", error.message);
  }
});

$("#sensorHealthFiles")?.addEventListener("change", () => {
  $("#sensorHealthFileInfo").textContent = selectedFilesLabel($("#sensorHealthFiles").files);
});

$("#runSensorHealth")?.addEventListener("click", async () => {
  const files = $("#sensorHealthFiles").files;
  if (!files.length) return showToast("请选择飞行日志", "支持上传单份或多份日志进行健康评分");
  const form = new FormData();
  appendLogFiles(form, files);
  $("#sensorHealthBadge").textContent = "评分中";
  $("#sensorHealthList").textContent = "正在解析传感器、GPS、电源、姿态和执行器数据...";
  try {
    const result = await uploadForm("/api/ulg/sensor-health", form);
    $("#sensorHealthBadge").textContent = "已完成";
    $("#sensorHealthBadge").classList.add("active");
    renderCardGrid($("#sensorHealthSummary"), result.summary);
    $("#sensorHealthList").innerHTML = (result.reports || []).map((report) => `
      <article class="sensor-health-card">
        <div class="sensor-health-head">
          <div><h3>${escapeHtml(report.source)}</h3><p>风险：${escapeHtml(report.risk)} · 异常 ${report.eventCount} 条</p></div>
          <strong>${report.overallScore}<span>/100</span></strong>
        </div>
        <div class="score-bar"><span style="width:${Math.max(0, Math.min(100, report.overallScore))}%"></span></div>
        <div class="sensor-health-items">
          ${report.items.map((item) => `
            <div>
              <strong>${escapeHtml(item.name)} <em>${item.score}</em></strong>
              <small>${escapeHtml(item.level)} · ${escapeHtml(item.evidence)}</small>
              <p>${escapeHtml(item.advice)}</p>
            </div>
          `).join("")}
        </div>
      </article>
    `).join("");
    showToast("传感器健康评分完成", `${result.count} 份日志已完成评分`);
  } catch (error) {
    $("#sensorHealthBadge").textContent = "失败";
    $("#sensorHealthList").textContent = error.message;
    showToast("健康评分失败", error.message);
  }
});
$("#applyParameters").addEventListener("click", async () => {
  const queued = queuedParameters();
  if (!queued.length) return showToast("没有待写入参数", "请先将修改加入队列");
  if ($("#parameterConfirmation").value.trim() !== "确认写入参数") {
    return showToast("缺少确认文本", "请输入：确认写入参数");
  }
  if (!window.confirm(`确认写入 ${queued.length} 个 PX4 参数？`)) return;
  try {
    const result = await api("/api/parameters/apply", {
      method: "POST",
      body: JSON.stringify({ confirmation: $("#parameterConfirmation").value.trim(), parameters: queued })
    });
    if (!result.accepted) return showToast("参数写入被阻止", result.reason);
    result.queued.forEach((item) => {
      const row = parameterRows.find((entry) => entry.name === item.name);
      if (!row) return;
      row.current = item.value;
      row.next = item.value;
      row.fromAircraft = true;
      row.state = "已入队";
    });
    $("#parameterConfirmation").value = "";
    renderParameters();
    showToast("参数已加入命令队列", "正在等待飞控写入确认...");
    const statuses = await waitForCommandBatch(result.queued || []);
    statuses.forEach((item) => {
      const row = parameterRows.find((entry) => entry.name === item.name);
      if (!row) return;
      if (item.status?.status === "accepted") {
        row.current = item.value;
        row.next = item.value;
        row.fromAircraft = true;
        row.state = "已确认";
      } else if (item.status?.status === "sent_no_ack") {
        row.state = "未确认";
      } else if (item.status?.status === "rejected") {
        row.state = "失败";
      } else {
        row.state = "等待回执";
      }
    });
    renderParameters();
    const accepted = statuses.filter((item) => item.status?.status === "accepted").length;
    showToast("参数写入回执", `${accepted}/${statuses.length} 个参数已被飞控确认`);
  } catch (error) {
    showToast("参数写入失败", error.message);
  }
});
refreshSafetyState();

let missionMap = null;
let missionLine = null;
let missionBaseLayer = null;
let missionUavMarker = null;
let missionTrackLine = null;
let missionHomeMarker = null;
let missionLastMarkerLabel = "";
let missionLoadedFromBackend = false;
const missionTrackPoints = [];
const waypoints = [];

function updateMissionMapStatus(title, detail = "", visible = true) {
  const status = $("#missionMapStatus");
  if (!status) return;
  setTextIfChanged($("strong", status), title);
  setTextIfChanged($("small", status), detail);
  status.classList.toggle("hidden", !visible);
}

function missionMapCenter() {
  if (latestPosition) return latestPosition;
  if (finite(latestTelemetry?.lat) && finite(latestTelemetry?.lon)) {
    return [Number(latestTelemetry.lat), Number(latestTelemetry.lon)];
  }
  return [31.2304, 121.4737];
}

function refreshMissionMapSize(delay = 80) {
  if (!missionMap) return;
  window.setTimeout(() => {
    missionMap.invalidateSize({ animate: false });
    if (waypoints.length) {
      const bounds = L.latLngBounds(waypoints.map((point) => [point.lat, point.lon]));
      if (bounds.isValid()) missionMap.fitBounds(bounds.pad(0.25), { animate: false });
    }
  }, delay);
}

function missionTelemetryPosition(data = latestTelemetry || {}) {
  const lat = finite(data.lat) ? Number(data.lat) : Array.isArray(latestPosition) ? Number(latestPosition[0]) : null;
  const lon = finite(data.lon) ? Number(data.lon) : Array.isArray(latestPosition) ? Number(latestPosition[1]) : null;
  const fixType = finite(data.fixType) ? Number(data.fixType) : null;
  const hasCoordinates = Number.isFinite(lat) && Number.isFinite(lon) && lat !== 0 && lon !== 0;
  const hasGpsFix = fixType === null || fixType >= 3;
  return { lat, lon, fixType, hasCoordinates, hasGpsFix };
}

function updateMissionVehicleLayer(data = latestTelemetry || {}, options = {}) {
  if (!missionMap || !window.L) return;
  const position = missionTelemetryPosition(data);
  if (!position.hasCoordinates || !position.hasGpsFix) {
    if ($("#missionPage")?.classList.contains("active")) {
      const sats = finite(data.satellites) ? Math.round(Number(data.satellites)) : "--";
      updateMissionMapStatus("等待飞机 GPS 定位", `任务规划需要真实定位；当前 Fix ${position.fixType ?? "--"}，卫星 ${sats}。`, true);
    }
    return;
  }

  const latLng = [position.lat, position.lon];
  const heading = finite(data.heading) ? Number(data.heading) : finite(data.yaw) ? Number(data.yaw) : 0;
  const altitude = finite(data.relativeAlt) ? Number(data.relativeAlt) : finite(data.alt) ? Number(data.alt) : null;
  const speed = finite(data.speed) ? Number(data.speed) : null;
  const vehicleId = data.vehicleId || "UAV";
  const label = `${vehicleId} · ${altitude !== null ? altitude.toFixed(1) + " m" : "-- m"} · ${speed !== null ? speed.toFixed(1) + " m/s" : "-- m/s"}`;

  if (!missionTrackLine) {
    missionTrackLine = L.polyline([], {
      color: "#f2b84b",
      weight: 2,
      opacity: 0.9,
      dashArray: "6 7"
    }).addTo(missionMap);
  }
  const lastPoint = missionTrackPoints.at(-1);
  const moved = !lastPoint || Math.abs(lastPoint[0] - position.lat) > 0.000001 || Math.abs(lastPoint[1] - position.lon) > 0.000001;
  if (moved) {
    missionTrackPoints.push(latLng);
    if (missionTrackPoints.length > 1500) missionTrackPoints.shift();
    missionTrackLine.setLatLngs(missionTrackPoints);
  }

  if (!missionUavMarker) {
    missionUavMarker = L.marker(latLng, { icon: getUavMapIcon(), zIndexOffset: 1000 }).addTo(missionMap);
    missionUavMarker.bindTooltip(label, {
      permanent: true,
      direction: "right",
      className: "uav-tooltip",
      offset: [14, 0]
    });
  } else {
    missionUavMarker.setLatLng(latLng);
    if (label !== missionLastMarkerLabel) missionUavMarker.setTooltipContent(label);
  }
  missionLastMarkerLabel = label;
  updateMapMarkerHeading(missionUavMarker, heading);

  if (data.home_position && finite(data.home_position.latitude) && finite(data.home_position.longitude)) {
    const homePoint = [Number(data.home_position.latitude), Number(data.home_position.longitude)];
    if (!missionHomeMarker) {
      missionHomeMarker = L.circleMarker(homePoint, {
        radius: 8,
        color: "#f2b84b",
        fillColor: "#342c1c",
        fillOpacity: 1,
        weight: 2
      }).addTo(missionMap).bindTooltip("Home 点", { permanent: false });
    } else {
      missionHomeMarker.setLatLng(homePoint);
    }
  }

  if (options.center || (options.initial && !waypoints.length)) {
    missionMap.setView(latLng, Math.max(missionMap.getZoom(), 16), { animate: false });
  }
  if ($("#missionPage")?.classList.contains("active")) {
    updateMissionMapStatus("任务地图已连接飞机", "卫星底图、飞机位置、航向和实际轨迹正在显示。", false);
  }
}

function activateMissionPlanner() {
  initializeMissionMap();
  refreshMissionMapSize(80);
  refreshMissionMapSize(320);
  updateMissionVehicleLayer(latestTelemetry, { initial: true });
  loadSavedMissionFromBackend();
}

function initializeMissionMap() {
  if (missionMap) {
    refreshMissionMapSize(40);
    return;
  }
  if (!window.L) {
    updateMissionMapStatus("任务地图组件未加载", "请强制刷新软件，或检查 vendor/leaflet 文件是否存在。", true);
    return;
  }
  updateMissionMapStatus("任务地图加载中", "正在加载卫星底图，航点功能即将可用。", true);
  missionMap = L.map("missionMap", { zoomControl: true }).setView(missionMapCenter(), latestPosition ? 16 : 14);
  missionBaseLayer = L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    { attribution: "Tiles © Esri", maxZoom: 19 }
  );
  missionBaseLayer.on("load", () => updateMissionMapStatus("任务地图已加载", "点击地图添加航点，拖动航点调整位置。", false));
  missionBaseLayer.on("tileerror", () => {
    updateMissionMapStatus("底图瓦片加载较慢", "网络底图暂不可用时仍可添加航点；建议检查网络或稍后刷新。", true);
  });
  missionBaseLayer.addTo(missionMap);
  missionLine = L.polyline([], { color: "#24c9d9", weight: 3 }).addTo(missionMap);
  missionMap.on("click", (event) => addWaypoint(event.latlng.lat, event.latlng.lng));
  updateMissionVehicleLayer(latestTelemetry, { initial: true });
  refreshMissionMapSize(120);
  refreshMissionMapSize(500);
}

async function loadSavedMissionFromBackend() {
  if (missionLoadedFromBackend || waypoints.length) return;
  missionLoadedFromBackend = true;
  try {
    const saved = await api("/api/mission");
    const items = Array.isArray(saved) ? saved : Array.isArray(saved?.waypoints) ? saved.waypoints : [];
    if (!items.length) return;
    initializeMissionMap();
    rebuildWaypoints(items);
    missionOverview.status = "已加载本地航线";
    updateMissionOverview();
    setTextIfChanged("#missionValidation", `已加载本地保存的 ${items.length} 个航点，请执行上传前检查。`);
    refreshMissionMapSize(120);
  } catch (_) {
    missionLoadedFromBackend = false;
  }
}
function addWaypoint(lat, lon, command = null) {
  const waypoint = {
    command: command || (waypoints.length === 0 ? "TAKEOFF" : "WAYPOINT"),
    lat, lon, altitude: waypoints.length === 0 ? 30 : 50, hold: 0, speed: 8, marker: null
  };
  waypoint.marker = L.marker([lat, lon], { draggable: true }).addTo(missionMap);
  waypoint.marker.bindTooltip(String(waypoints.length + 1), { permanent: true, direction: "center" });
  waypoint.marker.on("drag", () => {
    const point = waypoint.marker.getLatLng();
    waypoint.lat = point.lat; waypoint.lon = point.lng;
    renderWaypoints();
  });
  waypoints.push(waypoint);
  renderWaypoints();
}
function renderWaypoints() {
  $("#waypointCount").textContent = waypoints.length;
  $("#waypointTable").innerHTML = waypoints.map((point, index) => `
    <tr data-waypoint="${index}">
      <td>${index + 1}</td>
      <td><select data-field="command"><option value="TAKEOFF" ${point.command === "TAKEOFF" ? "selected" : ""}>起飞</option><option value="WAYPOINT" ${point.command === "WAYPOINT" ? "selected" : ""}>航点</option><option value="LOITER" ${point.command === "LOITER" ? "selected" : ""}>盘旋</option><option value="LAND" ${point.command === "LAND" ? "selected" : ""}>降落</option><option value="RTL" ${point.command === "RTL" ? "selected" : ""}>返航</option></select></td>
      <td>${point.lat.toFixed(6)}</td><td>${point.lon.toFixed(6)}</td>
      <td><input type="number" data-field="altitude" value="${point.altitude}"></td>
      <td><input type="number" data-field="hold" value="${point.hold}"></td>
      <td><input type="number" data-field="speed" value="${point.speed}"></td>
      <td><button class="table-action delete-waypoint">×</button></td>
    </tr>`).join("");
  if (missionLine) missionLine.setLatLngs(waypoints.map((point) => [point.lat, point.lon]));
  renderOverviewMissionRoute();
  updateRouteEstimate();
  if ($("#feasibilityPage")?.classList.contains("active")) renderFeasibilityPage();
  if ($("#geofencePage")?.classList.contains("active")) renderGeofencePage();
  if (["任务已保存", "等待上传服务", "飞控任务已上传"].includes(missionOverview.status)) missionOverview.status = "";
  updateMissionOverview();
  $("#uploadMission").disabled = true;
  $("#missionValidation").classList.remove("valid");
  $("#missionValidation").textContent = "任务已修改，请重新执行上传前检查";
}
$("#waypointTable").addEventListener("change", (event) => {
  const row = event.target.closest("[data-waypoint]");
  if (!row) return;
  const point = waypoints[Number(row.dataset.waypoint)];
  point[event.target.dataset.field] = event.target.type === "number" ? Number(event.target.value) : event.target.value;
  renderWaypoints();
});
$("#waypointTable").addEventListener("click", (event) => {
  const button = event.target.closest(".delete-waypoint");
  if (!button) return;
  const index = Number(button.closest("[data-waypoint]").dataset.waypoint);
  missionMap.removeLayer(waypoints[index].marker);
  waypoints.splice(index, 1);
  renderWaypoints();
});
function clearAllWaypoints() {
  waypoints.forEach((point) => missionMap.removeLayer(point.marker));
  waypoints.length = 0;
  renderWaypoints();
}

$("#clearWaypoints").addEventListener("click", clearAllWaypoints);
function serializedWaypoints() {
  return waypoints.map(({ marker, ...point }) => point);
}

function serializedPx4MissionWaypoints() {
  return waypoints.map(({ marker, speed, ...point }) => {
    const command = String(point.command || "WAYPOINT").toUpperCase();
    return {
      ...point,
      command,
      hold: Number(point.hold) || 0,
    };
  });
}

function haversineMeters(a, b) {
  const radius = 6371000;
  const toRad = (value) => Number(value) * Math.PI / 180;
  const dLat = toRad(b.lat - a.lat);
  const dLon = toRad(b.lon - a.lon);
  const lat1 = toRad(a.lat);
  const lat2 = toRad(b.lat);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
  return 2 * radius * Math.asin(Math.sqrt(h));
}

function updateRouteEstimate() {
  const target = $("#routeEstimate");
  if (!target) return;
  const { distance, minutes } = missionRouteStats();
  target.textContent = `距离 ${(distance / 1000).toFixed(2)} km · 预计 ${minutes.toFixed(1)} min`;
}

function missionRouteStats() {
  let distance = 0;
  for (let index = 1; index < waypoints.length; index += 1) {
    distance += haversineMeters(waypoints[index - 1], waypoints[index]);
  }
  const avgSpeed = waypoints.reduce((sum, point) => sum + (Number(point.speed) || 8), 0) / Math.max(1, waypoints.length);
  const minutes = distance && avgSpeed ? distance / avgSpeed / 60 : 0;
  return { distance, minutes, avgSpeed };
}

function missionProgressFromTelemetry(data = latestTelemetry || {}) {
  const current = data.missionCurrent || data.mission_current || {};
  const reached = data.missionReached || data.mission_reached || {};
  const currentSeq = finite(current.seq) ? Number(current.seq) : null;
  const reachedSeq = finite(reached.seq) ? Number(reached.seq) : null;
  const total = finite(current.total) && Number(current.total) > 0 ? Number(current.total) : null;
  const ageMs = finite(current.timeMs) ? Math.max(0, Date.now() - Number(current.timeMs)) : null;
  const active = currentSeq !== null && (ageMs === null || ageMs <= 5000);
  return { current, reached, currentSeq, reachedSeq, total, ageMs, active };
}

function renderMissionProgress(data = latestTelemetry || {}) {
  const progress = missionProgressFromTelemetry(data);
  const currentText = progress.active
    ? `#${progress.currentSeq}${progress.total ? ` / ${progress.total}` : ""}`
    : "未执行";
  const reachedText = progress.reachedSeq !== null ? `#${progress.reachedSeq}` : "--";
  const stateText = progress.active
    ? `MISSION_CURRENT · ${progress.ageMs !== null ? Math.round(progress.ageMs) + " ms" : "实时"}`
    : "等待 MISSION_CURRENT";
  if ($("#overviewMissionCurrent")) $("#overviewMissionCurrent").textContent = currentText;
  if ($("#missionCurrentSeq")) $("#missionCurrentSeq").textContent = currentText;
  if ($("#missionReachedSeq")) $("#missionReachedSeq").textContent = reachedText;
  if ($("#missionCurrentState")) $("#missionCurrentState").textContent = stateText;
}

function updateMissionOverview() {
  const { distance, minutes } = missionRouteStats();
  const hasRoute = waypoints.length >= 2;
  const hasAnyPoint = waypoints.length > 0;
  if (!["任务已保存", "等待上传服务", "飞控任务已上传", "已上传待校验", "上传失败", "上传被阻止"].includes(missionOverview.status)) {
    missionOverview.status = hasRoute ? "航线已规划" : hasAnyPoint ? "继续添加航点" : "任务待规划";
  }
  $("#missionId").textContent = missionOverview.id;
  $("#missionFleet").textContent = missionOverview.fleet;
  $("#currentMissionTitle").textContent = `${missionOverview.name} · ${missionOverview.area}`;
  $(".status-label").textContent = missionOverview.status;
  $(".live-dot").style.background = hasRoute ? "var(--green)" : hasAnyPoint ? "var(--amber)" : "var(--blue)";
  $("#overviewWaypointCount").textContent = `${waypoints.length} 个`;
  $("#overviewMissionDistance").textContent = `${(distance / 1000).toFixed(2)} km`;
  $("#overviewMissionEta").textContent = `${minutes.toFixed(1)} min`;
  renderMissionProgress();
  const progress = hasRoute ? 100 : hasAnyPoint ? 45 : 0;
  $("#missionProgressBar").style.width = `${progress}%`;
}

function rebuildWaypoints(items) {
  waypoints.forEach((point) => point.marker && missionMap.removeLayer(point.marker));
  waypoints.length = 0;
  (items || []).forEach((item) => {
    addWaypoint(Number(item.lat), Number(item.lon), item.command || "WAYPOINT");
    const point = waypoints.at(-1);
    point.altitude = Number(item.altitude) || point.altitude;
    point.hold = Number(item.hold) || 0;
    point.speed = Number(item.speed) || 8;
  });
  renderWaypoints();
}

function offsetMetersToLatLon(origin, northMeters, eastMeters) {
  const originLat = Array.isArray(origin) ? Number(origin[0]) : Number(origin.lat);
  const originLon = Array.isArray(origin) ? Number(origin[1]) : Number(origin.lng ?? origin.lon);
  const metersPerDegreeLat = 111320;
  const metersPerDegreeLon = Math.max(1, 111320 * Math.cos(originLat * Math.PI / 180));
  return {
    lat: originLat + northMeters / metersPerDegreeLat,
    lon: originLon + eastMeters / metersPerDegreeLon,
  };
}

function missionTemplateOrigin() {
  initializeMissionMap();
  if (latestPosition) return { lat: latestPosition[0], lon: latestPosition[1] };
  const center = missionMap?.getCenter?.();
  return { lat: Number(center?.lat || 31.2304), lon: Number(center?.lng || 121.4737) };
}

function buildMissionTemplate(type) {
  const origin = missionTemplateOrigin();
  const fixedWing = [
    { command: "TAKEOFF", north: 0, east: 0, altitude: 60, speed: 12, hold: 0 },
    { command: "WAYPOINT", north: 320, east: 120, altitude: 90, speed: 15, hold: 0 },
    { command: "WAYPOINT", north: 860, east: 420, altitude: 120, speed: 18, hold: 0 },
    { command: "LOITER", north: 1120, east: -120, altitude: 120, speed: 16, hold: 20 },
    { command: "WAYPOINT", north: 480, east: -380, altitude: 80, speed: 14, hold: 0 },
    { command: "LAND", north: 80, east: -80, altitude: 20, speed: 11, hold: 0 },
  ];
  const multirotor = [
    { command: "TAKEOFF", north: 0, east: 0, altitude: 30, speed: 4, hold: 0 },
    { command: "WAYPOINT", north: 80, east: 0, altitude: 35, speed: 5, hold: 0 },
    { command: "WAYPOINT", north: 80, east: 80, altitude: 35, speed: 5, hold: 0 },
    { command: "WAYPOINT", north: 0, east: 80, altitude: 35, speed: 5, hold: 0 },
    { command: "LAND", north: 0, east: 0, altitude: 0, speed: 3, hold: 0 },
  ];
  const compoundVtol = [
    { command: "TAKEOFF", north: 0, east: 0, altitude: 30, speed: 5, hold: 0 },
    { command: "WAYPOINT", north: 160, east: 60, altitude: 60, speed: 8, hold: 0 },
    { command: "WAYPOINT", north: 560, east: 260, altitude: 100, speed: 16, hold: 0 },
    { command: "WAYPOINT", north: 820, east: -180, altitude: 100, speed: 16, hold: 0 },
    { command: "WAYPOINT", north: 220, east: -120, altitude: 55, speed: 8, hold: 0 },
    { command: "LAND", north: 20, east: 20, altitude: 15, speed: 4, hold: 0 },
  ];
  const profile = type === "compound_vtol" ? compoundVtol : type === "multirotor" ? multirotor : fixedWing;
  return profile.map((item) => {
    const point = offsetMetersToLatLon(origin, item.north, item.east);
    return {
      command: item.command,
      lat: point.lat,
      lon: point.lon,
      altitude: item.altitude,
      hold: item.hold,
      speed: item.speed,
    };
  });
}

function applyMissionTemplate(type) {
  initializeMissionMap();
  if (waypoints.length && !window.confirm("当前已有航点，是否用模板覆盖当前任务规划？")) return;
  const label = type === "compound_vtol" ? "复合翼任务模板" : type === "multirotor" ? "四旋翼任务模板" : "固定翼任务模板";
  rebuildWaypoints(buildMissionTemplate(type));
  const first = waypoints[0];
  if (first && missionMap) missionMap.setView([first.lat, first.lon], 15);
  missionOverview.status = `${label}已生成`;
  updateMissionOverview();
  $("#missionValidation").classList.remove("valid");
  $("#missionValidation").textContent = `${label}已生成：请根据真实场地调整航点，再执行上传前检查。`;
  showToast(label, `${waypoints.length} 个航点已载入任务规划器`);
}

$("#saveMission").addEventListener("click", async () => {
  await api("/api/mission", { method: "POST", body: JSON.stringify({ waypoints: serializedWaypoints() }) });
  missionOverview.status = waypoints.length >= 2 ? "任务已保存" : "任务待规划";
  updateMissionOverview();
  showToast("任务已保存", `${waypoints.length} 个航点已写入本地`);
});
$("#exportMission").addEventListener("click", () => {
  const blob = new Blob([JSON.stringify(serializedWaypoints(), null, 2)], { type: "application/json" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `mission-${Date.now()}.json`;
  link.click();
  URL.revokeObjectURL(link.href);
});
function flightMissionItemsToWaypoints(items) {
  const result = [];
  let currentSpeed = 8;
  (items || []).forEach((item) => {
    const commandName = String(item.commandName || item.command || "WAYPOINT").toUpperCase();
    if (commandName === "DO_CHANGE_SPEED") {
      currentSpeed = Number(item.speed || item.param2 || currentSpeed) || currentSpeed;
      return;
    }
    const command = commandName.startsWith("CMD_") ? "WAYPOINT" : commandName;
    if (!["TAKEOFF", "WAYPOINT", "LOITER", "LAND", "RTL"].includes(command)) return;
    let lat = Number(item.lat);
    let lon = Number(item.lon);
    if ((!Number.isFinite(lat) || !Number.isFinite(lon) || (lat === 0 && lon === 0)) && result.length) {
      lat = result.at(-1).lat;
      lon = result.at(-1).lon;
    }
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
    result.push({
      command,
      lat,
      lon,
      altitude: Number(item.altitude || item.z || 50),
      hold: Number(item.param1 || 0),
      speed: currentSpeed
    });
  });
  return result;
}

function extractMissionItemsFromCommandStatus(status = {}) {
  const results = Array.isArray(status.results) ? status.results : [];
  for (const result of results) {
    if (Array.isArray(result?.verification?.items)) return result.verification.items;
    if (Array.isArray(result?.readback?.items)) return result.readback.items;
    if (Array.isArray(result?.items)) return result.items;
  }
  return [];
}

function applyFlightMissionReadback(status, sourceLabel = "飞控 Mission") {
  const missionItems = extractMissionItemsFromCommandStatus(status);
  const parsed = flightMissionItemsToWaypoints(missionItems);
  if (!parsed.length) {
    return { applied: false, missionItems, parsed };
  }
  initializeMissionMap();
  rebuildWaypoints(parsed);
  missionOverview.status = `已同步${sourceLabel}`;
  updateMissionOverview();
  return { applied: true, missionItems, parsed };
}

$("#readFlightMission")?.addEventListener("click", async () => {
  try {
    $("#missionValidation").textContent = "正在读取飞控 Mission...";
    const response = await api("/api/mission/read", { method: "POST", body: "{}" });
    if (!response.accepted) {
      $("#missionValidation").textContent = response.reason;
      return showToast("读取飞控任务失败", response.reason);
    }
    trackCommandEvidence(response.commandId, "读取飞控 Mission");
    const status = await waitForConnectorCommand(response.commandId, (item) => {
      $("#missionValidation").textContent = item.message || "正在读取飞控 Mission...";
    }, { attempts: 45, delayMs: 500 });
    const readback = applyFlightMissionReadback(status, "飞控任务");
    if (!readback.applied) {
      $("#missionValidation").textContent = status?.message || "飞控任务为空或未读到可显示航点";
      return showToast("飞控任务为空", $("#missionValidation").textContent);
    }
    missionOverview.status = "已读取飞控任务";
    updateMissionOverview();
    $("#missionValidation").textContent = `已读取飞控 Mission：${readback.missionItems.length} 条任务项，${readback.parsed.length} 个航点`;
    $("#missionValidation").classList.add("valid");
    showToast("飞控任务读取完成", `${readback.parsed.length} 个航点已载入任务规划器`);
  } catch (error) {
    $("#missionValidation").textContent = error.message;
    showToast("读取飞控任务失败", error.message);
  }
});
$("#clearFlightMission")?.addEventListener("click", async () => {
  if (!window.confirm("确认清空飞控里的 Mission？这不会删除当前界面航线。")) return;
  try {
    $("#missionValidation").textContent = "正在清空飞控 Mission...";
    const response = await api("/api/mission/clear", { method: "POST", body: "{}" });
    if (!response.accepted) {
      $("#missionValidation").textContent = response.reason;
      return showToast("清空飞控任务失败", response.reason);
    }
    trackCommandEvidence(response.commandId, "清空飞控 Mission");
    const status = await waitForConnectorCommand(response.commandId, null, { attempts: 30, delayMs: 500 });
    if (status?.status === "accepted") {
      $("#missionValidation").textContent = status.message || "飞控 Mission 已清空";
      $("#missionValidation").classList.add("valid");
      showToast("飞控任务已清空", $("#missionValidation").textContent);
    } else {
      $("#missionValidation").textContent = status?.message || "飞控未确认清空任务";
      $("#missionValidation").classList.remove("valid");
      showToast("清空飞控任务未确认", $("#missionValidation").textContent);
    }
  } catch (error) {
    $("#missionValidation").textContent = error.message;
    showToast("清空飞控任务失败", error.message);
  }
});
$("#centerMissionOnUav").addEventListener("click", () => {
  initializeMissionMap();
  if (!latestPosition) return showToast("尚未收到定位", "连接飞控并收到 GPS 后才能定位到飞机");
  missionMap.setView(latestPosition, 17);
});
$("#addTakeoffPoint").addEventListener("click", () => {
  initializeMissionMap();
  const point = latestPosition || missionMap.getCenter();
  addWaypoint(Array.isArray(point) ? point[0] : point.lat, Array.isArray(point) ? point[1] : point.lng, "TAKEOFF");
});
$("#addLandPoint").addEventListener("click", () => {
  initializeMissionMap();
  const last = waypoints.at(-1);
  const point = last || (latestPosition ? { lat: latestPosition[0], lon: latestPosition[1] } : null);
  if (!point) return showToast("无法添加降落点", "请先添加航点或收到 GPS 定位");
  addWaypoint(point.lat, point.lon, "LAND");
});
$("#addRtlPoint").addEventListener("click", () => {
  initializeMissionMap();
  const last = waypoints.at(-1);
  const point = last || (latestPosition ? { lat: latestPosition[0], lon: latestPosition[1] } : null);
  if (!point) return showToast("无法添加返航", "请先添加航点或收到 GPS 定位");
  addWaypoint(point.lat, point.lon, "RTL");
});
$("#applyMultirotorTemplate")?.addEventListener("click", () => applyMissionTemplate("multirotor"));
$("#applyFixedWingTemplate")?.addEventListener("click", () => applyMissionTemplate("fixed_wing"));
$("#applyVtolTemplate")?.addEventListener("click", () => applyMissionTemplate("compound_vtol"));
$("#importMissionFile").addEventListener("change", async () => {
  const file = $("#importMissionFile").files[0];
  if (!file) return;
  try {
    const data = JSON.parse(await file.text());
    initializeMissionMap();
    rebuildWaypoints(Array.isArray(data) ? data : data.waypoints);
    showToast("任务已导入", `${waypoints.length} 个航点`);
  } catch (error) {
    showToast("任务导入失败", error.message);
  }
});
$("#checkMission").addEventListener("click", async () => {
  const result = await api("/api/mission/check", {
    method: "POST", body: JSON.stringify({ waypoints: serializedWaypoints() })
  });
  $("#missionValidation").textContent = result.reason;
  $("#missionValidation").classList.toggle("valid", result.allowed);
  $("#missionCheckBadge").textContent = result.allowed ? "检查通过" : "检查未通过";
  $("#uploadMission").disabled = !result.allowed;
});
$("#uploadMission").addEventListener("click", async () => {
  if (!window.confirm("确认将当前任务上传到真实飞控？")) return;
  const uploadWaypoints = serializedPx4MissionWaypoints();
  missionOverview.status = "等待上传服务";
  updateMissionOverview();
  $("#missionValidation").textContent = "正在上传 PX4/QGC 兼容 Mission 到飞控，后端会按当前机型自动选择上传方案...";
  try {
    await api("/api/mission", { method: "POST", body: JSON.stringify({ waypoints: serializedWaypoints() }) });
    const response = await api("/api/mission/upload", {
      method: "POST",
      body: JSON.stringify({ waypoints: uploadWaypoints, clearExisting: true })
    });
    if (!response.accepted) {
      missionOverview.status = "上传被阻止";
      updateMissionOverview();
      $("#missionValidation").textContent = response.reason;
      return showToast("任务上传被阻止", response.reason);
    }
    showToast("Mission 上传已开始", "正在等待飞控请求航点...");
    trackCommandEvidence(response.commandId, "上传 Mission 到飞控");
    const status = await waitForConnectorCommand(response.commandId, (item) => {
      const result = item.results?.[0] || {};
      const progress = result.progress !== undefined ? ` · ${result.progress}%` : "";
      $("#missionValidation").textContent = `${item.message || "正在上传 Mission"}${progress}`;
    }, { attempts: 90, delayMs: 700 });
    const readback = applyFlightMissionReadback(status, "飞控回读任务");
    if (status?.status === "accepted") {
      missionOverview.status = "飞控任务已上传";
      $("#missionValidation").textContent = readback.applied
        ? `Mission 上传完成并自动回读校验：飞控保存 ${readback.missionItems.length} 条任务项，已同步 ${readback.parsed.length} 个航点`
        : (status.message || "Mission 上传完成，未读到可显示的回读航点");
      $("#missionValidation").classList.add("valid");
      showToast("Mission 上传完成", readback.applied ? "飞控回读校验已同步到规划器" : (status.message || "飞控已确认任务"));
    } else if (status?.status === "partial") {
      missionOverview.status = "已上传待校验";
      $("#missionValidation").textContent = readback.applied
        ? `飞控已接受 Mission，但自动回读校验存在差异；已显示飞控实际保存的 ${readback.parsed.length} 个航点`
        : (status.message || "飞控已接受 Mission，但回读校验未通过，请点击读取飞控任务确认");
      $("#missionValidation").classList.remove("valid");
      showToast("Mission 已上传待校验", $("#missionValidation").textContent);
    } else {
      missionOverview.status = "上传失败";
      $("#missionValidation").textContent = status?.message || "Mission 上传失败";
      $("#missionValidation").classList.remove("valid");
      showToast("Mission 上传失败", $("#missionValidation").textContent);
    }
    updateMissionOverview();
  } catch (error) {
    missionOverview.status = "上传失败";
    updateMissionOverview();
    $("#missionValidation").textContent = error.message;
    showToast("Mission 上传失败", error.message);
  }
});
updateMissionOverview();

function riskColor(score) {
  if (score < 35) return "var(--green)";
  if (score < 70) return "var(--amber)";
  return "var(--red)";
}

function computeRiskScore() {
  const data = latestTelemetry || {};
  const reasons = [];
  const missing = [];
  let score = 0;
  const add = (points, title, detail, level = "medium") => {
    score += points;
    reasons.push({ points, title, detail, level });
  };
  const linkAge = lastTelemetryAt ? (Date.now() - lastTelemetryAt) / 1000 : null;
  const hasFreshTelemetry = linkAge !== null && linkAge <= 5 && data.connected !== false && !data.stale;
  if (!hasFreshTelemetry) {
    return {
      available: false,
      score: null,
      confidence: 0,
      reasons: [{ points: 0, title: "无法评分", detail: "没有新鲜 MAVLink 遥测，不能生成可信风险分", level: "high" }],
      missing: ["MAVLink 遥测"],
    };
  }

  if (linkAge > 3) add(28, "数据链路超时", "超过 3 秒未收到新的 MAVLink 遥测", "high");
  const fix = finite(data.fixType) ? Number(data.fixType) : null;
  const satellites = finite(data.satellites) ? Number(data.satellites) : null;
  if (fix === null) missing.push("GPS Fix");
  else if (fix < 3) add(22, "GPS 未达到 3D Fix", `Fix Type ${fix}`, "high");
  if (satellites === null) missing.push("卫星数量");
  else if (satellites < 8) add(14, "卫星数量偏少", `${satellites} 颗卫星`, "medium");

  const battery = finite(data.battery) ? Number(data.battery) : null;
  const voltage = finite(data.voltage) ? Number(data.voltage) : null;
  if (battery === null && voltage === null) missing.push("电池/电压");
  if (battery !== null && battery < 25) add(20, "电池余量不足", `${battery.toFixed(0)}%`, "high");
  else if (battery !== null && battery < 40) add(10, "电池余量偏低", `${battery.toFixed(0)}%`, "medium");

  const roll = finite(data.roll) ? Math.abs(Number(data.roll)) : null;
  const pitch = finite(data.pitch) ? Math.abs(Number(data.pitch)) : null;
  if (roll === null || pitch === null) missing.push("姿态角");
  if (roll !== null && roll > 35) add(15, "横滚角偏大", `${roll.toFixed(1)}°`, "medium");
  if (pitch !== null && pitch > 30) add(15, "俯仰角偏大", `${pitch.toFixed(1)}°`, "medium");

  const speed = finite(data.speed) ? Number(data.speed) : null;
  if (speed === null) missing.push("地速");
  else if (speed > 18) add(10, "地速偏高", `${speed.toFixed(1)} m/s`, "medium");

  const warningCount = Array.isArray(data.warnings) ? data.warnings.length : 0;
  if (warningCount) add(Math.min(18, warningCount * 6), "飞控告警存在", `${warningCount} 条告警`, "high");
  if (waypoints.length < 2) add(6, "任务航线不足", "当前少于 2 个航点", "low");
  score = Math.max(0, Math.min(100, Math.round(score)));
  if (!reasons.length) reasons.push({ points: 0, title: "当前风险较低", detail: "关键遥测指标未发现明显异常", level: "low" });
  const confidence = Math.max(0.25, Math.min(1, 1 - missing.length * 0.12));
  if (missing.length) {
    reasons.push({
      points: 0,
      title: "评分置信度降低",
      detail: `缺少：${missing.join("、")}`,
      level: "medium",
    });
  }
  return { available: true, score, reasons, confidence, missing };
}

function renderRiskPage() {
  const { available, score, reasons, confidence, missing } = computeRiskScore();
  if (!available) {
    $("#riskBadge").textContent = "数据不足";
    $("#riskScore").textContent = "--";
    $("#riskScoreRing").style.setProperty("--risk-color", "var(--muted)");
    $("#riskScoreRing").style.setProperty("--risk-angle", "0deg");
    $("#riskLevel").textContent = "无法评分";
    $("#riskSummary").textContent = "当前没有新鲜 MAVLink 遥测，风险分不会虚构。请先确认飞控连接、心跳和遥测流。";
    $("#riskReasonList").innerHTML = reasons.map((item) => `
      <div class="risk-reason ${item.level}">
        <strong>${escapeHtml(item.title)}</strong>
        <span>--</span>
        <small>${escapeHtml(item.detail)}</small>
      </div>`).join("");
    $("#riskMetricGrid").innerHTML = [
      ["链路", "无新鲜遥测"],
      ["GPS", "--"],
      ["电池", "--"],
      ["姿态", "--"],
      ["速度", "--"],
      ["航点", `${waypoints.length} 个`],
    ].map(([key, value]) => `<div><small>${key}</small><strong>${escapeHtml(value)}</strong></div>`).join("");
    return;
  }
  const level = score < 35 ? "低风险" : score < 70 ? "中风险" : "高风险";
  const confidenceText = confidence >= 0.85 ? "高置信度" : confidence >= 0.6 ? "中置信度" : "低置信度";
  $("#riskBadge").textContent = `${level} · ${confidenceText}`;
  $("#riskScore").textContent = String(score);
  $("#riskScoreRing").style.setProperty("--risk-color", riskColor(score));
  $("#riskScoreRing").style.setProperty("--risk-angle", `${score * 3.6}deg`);
  $("#riskLevel").textContent = level;
  $("#riskSummary").textContent = `${score < 35 ? "当前规则评分较低，起飞前仍需完成飞前检查。" : score < 70 ? "存在需要关注的风险项，建议处理后再执行任务。" : "存在高风险因素，不建议起飞或继续任务。"} 本分数只基于当前 UI 收到的遥测规则计算，不是 PX4 官方 failsafe。`;
  $("#riskReasonList").innerHTML = reasons.map((item) => `
    <div class="risk-reason ${item.level}">
      <strong>${escapeHtml(item.title)}</strong>
      <span>+${item.points}</span>
      <small>${escapeHtml(item.detail)}</small>
    </div>`).join("");
  const data = latestTelemetry || {};
  $("#riskMetricGrid").innerHTML = [
    ["GPS", `Fix ${data.fixType ?? "--"} / ${data.satellites ?? "--"} 星`],
    ["电池", `${data.battery ?? "--"}%`],
    ["姿态", `R ${data.roll ?? "--"}° / P ${data.pitch ?? "--"}°`],
    ["速度", `${data.speed ?? "--"} m/s`],
    ["链路", lastTelemetryAt ? `${((Date.now() - lastTelemetryAt) / 1000).toFixed(1)} s` : "--"],
    ["航点", `${waypoints.length} 个`],
  ].map(([key, value]) => `<div><small>${key}</small><strong>${escapeHtml(value)}</strong></div>`).join("");
}

function missionDistanceMeters() {
  let distance = 0;
  for (let index = 1; index < waypoints.length; index += 1) distance += haversineMeters(waypoints[index - 1], waypoints[index]);
  return distance;
}

function renderFeasibilityPage() {
  const distance = missionDistanceMeters();
  const avgSpeed = waypoints.reduce((sum, point) => sum + (Number(point.speed) || 8), 0) / Math.max(1, waypoints.length);
  const missionMinutes = distance && avgSpeed ? distance / avgSpeed / 60 : 0;
  const battery = Number(latestTelemetry?.battery);
  const usableBattery = Number.isFinite(battery) ? Math.max(0, battery - 25) : 0;
  const availableMinutes = usableBattery * 0.65;
  const requiredWithReserve = missionMinutes * 1.35;
  const feasible = waypoints.length >= 2 && (!Number.isFinite(battery) || availableMinutes >= requiredWithReserve);
  $("#feasibilityBadge").textContent = feasible ? "可执行" : "需调整";
  $("#forecastGrid").innerHTML = [
    ["航点数量", `${waypoints.length} 个`],
    ["航线距离", `${(distance / 1000).toFixed(2)} km`],
    ["预计时间", `${missionMinutes.toFixed(1)} min`],
    ["返航余量", `${Math.max(0, availableMinutes - missionMinutes).toFixed(1)} min`],
    ["当前电量", Number.isFinite(battery) ? `${battery.toFixed(0)}%` : "未知"],
    ["建议巡航速度", `${avgSpeed.toFixed(1)} m/s`],
  ].map(([key, value]) => `<div><small>${key}</small><strong>${value}</strong></div>`).join("");
  const problems = [];
  if (waypoints.length < 2) problems.push("航点不足，无法形成完整任务。");
  if (Number.isFinite(battery) && availableMinutes < requiredWithReserve) problems.push("按当前电量估算，返航余量不足。");
  if (waypoints.some((point) => Number(point.altitude) > 120)) problems.push("存在高于 120 m 的航点，请确认当地飞行限制。");
  $("#forecastResult").innerHTML = problems.length
    ? `<strong>任务暂不建议执行</strong><ul>${problems.map((item) => `<li>${item}</li>`).join("")}</ul>`
    : `<strong>任务预测通过</strong><p>当前航线、电量和速度估算满足基础执行条件。建议起飞前仍执行飞前检查和电子围栏检查。</p>`;
}

const geofences = [];
let geofenceMap = null;
let geofenceLayer = null;
let geofenceMissionLayer = null;

function initializeGeofenceMap() {
  if (geofenceMap || !window.L) return;
  geofenceMap = L.map("geofenceMap").setView(latestPosition || [31.2304, 121.4737], 14);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { attribution: "© OpenStreetMap contributors", maxZoom: 19 }).addTo(geofenceMap);
  geofenceLayer = L.layerGroup().addTo(geofenceMap);
  geofenceMissionLayer = L.layerGroup().addTo(geofenceMap);
  geofenceMap.on("click", (event) => addGeofence(event.latlng.lat, event.latlng.lng));
  setTimeout(() => geofenceMap.invalidateSize(), 120);
}

function addGeofence(lat, lon, radius = Number($("#noFlyRadius")?.value || 120)) {
  geofences.push({ lat, lon, radius });
  renderGeofencePage();
}

function renderGeofencePage() {
  if (geofenceMap) {
    geofenceLayer.clearLayers();
    geofenceMissionLayer.clearLayers();
    geofences.forEach((zone, index) => {
      L.circle([zone.lat, zone.lon], { radius: zone.radius, color: "#ff6470", fillColor: "#4a2028", fillOpacity: 0.25, weight: 2 }).addTo(geofenceLayer).bindTooltip(`禁飞区 ${index + 1}`);
    });
    if (waypoints.length) {
      L.polyline(waypoints.map((point) => [point.lat, point.lon]), { color: "#24c9d9", weight: 3 }).addTo(geofenceMissionLayer);
      waypoints.forEach((point, index) => L.circleMarker([point.lat, point.lon], { radius: 5, color: "#24c9d9" }).addTo(geofenceMissionLayer).bindTooltip(String(index + 1)));
    }
  }
  $("#geofenceList").innerHTML = geofences.length ? geofences.map((zone, index) => `
    <div><strong>禁飞区 ${index + 1}</strong><small>${zone.lat.toFixed(6)}, ${zone.lon.toFixed(6)} · ${zone.radius} m</small></div>
  `).join("") : `<div><strong>暂无禁飞区</strong><small>点击地图或使用飞机位置添加</small></div>`;
}

function checkGeofenceMission() {
  const maxRadius = Number($("#maxMissionRadius").value || 800);
  const maxAltitude = Number($("#maxMissionAltitude").value || 120);
  const origin = waypoints[0] || (latestPosition ? { lat: latestPosition[0], lon: latestPosition[1] } : null);
  const issues = [];
  if (!waypoints.length) issues.push("当前没有任务航点。");
  waypoints.forEach((point, index) => {
    if (origin && haversineMeters(origin, point) > maxRadius) issues.push(`航点 ${index + 1} 超出最大任务半径。`);
    if (Number(point.altitude) > maxAltitude) issues.push(`航点 ${index + 1} 高度超过限制。`);
    geofences.forEach((zone, zoneIndex) => {
      if (haversineMeters(zone, point) <= zone.radius) issues.push(`航点 ${index + 1} 进入禁飞区 ${zoneIndex + 1}。`);
    });
  });
  $("#geofenceBadge").textContent = issues.length ? "存在越界" : "检查通过";
  $("#geofenceResult").innerHTML = issues.length
    ? `<strong>电子围栏检查未通过</strong><ul>${issues.map((item) => `<li>${item}</li>`).join("")}</ul>`
    : `<strong>电子围栏检查通过</strong><p>当前任务未超出半径、高度或禁飞区限制。</p>`;
}

$("#refreshForecast").addEventListener("click", renderFeasibilityPage);
$("#clearGeofences").addEventListener("click", () => {
  geofences.length = 0;
  renderGeofencePage();
});
$("#addNoFlyAtUav").addEventListener("click", () => {
  initializeGeofenceMap();
  const point = latestPosition || (geofenceMap ? geofenceMap.getCenter() : null);
  if (!point) return showToast("无法添加禁飞区", "尚未收到定位，也无法读取地图中心");
  addGeofence(Array.isArray(point) ? point[0] : point.lat, Array.isArray(point) ? point[1] : point.lng);
});
$("#checkGeofence").addEventListener("click", checkGeofenceMission);

setInterval(() => {
  if (!lastTelemetryAt || Date.now() - lastTelemetryAt < 3000) return;
  $("#telemetryState").textContent = "MAVLink 数据超时";
  $("#telemetryState").classList.remove("online");
  $("#telemetryState").classList.add("offline");
  if (updateAttitude.lastAt && Date.now() - updateAttitude.lastAt >= 3000) {
    $("#attitudeState").textContent = "姿态数据超时";
    $("#attitudeState").classList.remove("online");
    $("#attitudeState").classList.add("offline");
    $("#compassState").textContent = "罗盘数据超时";
    $("#compassState").classList.remove("online");
    $("#compassState").classList.add("offline");
  }
}, 1000);

const drawer = $("#utilityDrawer");
const drawerShade = $("#drawerShade");
function closeDrawer() {
  drawer.classList.remove("open");
  drawer.setAttribute("aria-hidden", "true");
  drawerShade.hidden = true;
}
function openDrawer(title, eyebrow, content) {
  $("#drawerTitle").textContent = title;
  $("#drawerEyebrow").textContent = eyebrow;
  $("#drawerContent").innerHTML = typeof content === "function" ? content() : content;
  drawer.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");
  drawerShade.hidden = false;
}
$("#closeDrawer").addEventListener("click", closeDrawer);
drawerShade.addEventListener("click", closeDrawer);

const notificationRows = () => `
  <div class="drawer-row"><span><strong>UAV-08 云台异常</strong><small>建议任务结束后检查俯仰轴电机</small></span><time>14:32</time></div>
  <div class="drawer-row"><span><strong>任务进度已达到 68%</strong><small>A-07 区域剩余 3 个航点</small></span><time>14:25</time></div>
  <div class="drawer-row"><span><strong>真实链路指标已启用</strong><small>侧边栏显示报文新鲜度，不再使用固定延迟数值</small></span><time>系统</time></div>`;

async function openHistoryDrawer() {
  openDrawer("飞行记录", "MISSION HISTORY", `<div class="drawer-empty">正在读取历史会话...</div>`);
  try {
    const sessions = await api("/api/logging/sessions");
    $("#drawerContent").innerHTML = sessions.length ? sessions.map((session) => `
      <div class="drawer-row replay-session-row">
        <span><strong>${session.name}</strong><small>${session.rows} 条 · 最大高度 ${session.maxAltitude === null ? "--" : session.maxAltitude.toFixed(1)} m · 最大速度 ${session.maxSpeed === null ? "--" : session.maxSpeed.toFixed(1)} m/s</small></span>
        <strong class="${session.warningCount ? "state-warn" : "state-ok"}">${session.warningCount} 告警</strong>
        <div class="drawer-actions">
          <button class="ghost-button" data-replay-session="${escapeAttribute(session.name)}">回放</button>
          <button class="ghost-button" data-export-session="${escapeAttribute(session.name)}">导出</button>
        </div>
      </div>`).join("") : `<div class="drawer-empty">暂无飞行记录</div>`;
  } catch (error) {
    $("#drawerContent").innerHTML = `<div class="drawer-empty">读取失败：${error.message}</div>`;
  }
}

let replayRows = [];
let replayIndex = 0;
let replayTimer = null;
let replaySessionName = "";

function numericRow(row, key) {
  const value = Number(row[key]);
  return Number.isFinite(value) ? value : null;
}

function replayTelemetry(row) {
  return {
    vehicleId: `REPLAY-${replaySessionName || "LOG"}`,
    roll: numericRow(row, "roll_deg"),
    pitch: numericRow(row, "pitch_deg"),
    yaw: numericRow(row, "yaw_deg"),
    alt: numericRow(row, "altitude_m"),
    relativeAlt: numericRow(row, "relative_altitude_m"),
    climb: numericRow(row, "vertical_speed_mps"),
    speed: numericRow(row, "ground_speed_mps"),
    lat: numericRow(row, "latitude"),
    lon: numericRow(row, "longitude"),
    heading: numericRow(row, "heading_deg"),
    fixType: numericRow(row, "gps_fix_type"),
    satellites: numericRow(row, "satellites_visible"),
    eph: numericRow(row, "hdop"),
    voltage: numericRow(row, "battery_voltage_v"),
    current: numericRow(row, "battery_current_a"),
    battery: numericRow(row, "battery_remaining_percent"),
    mode: row.flight_mode || "REPLAY",
    armed: String(row.armed).toLowerCase() === "true",
    receivedAt: Date.now(),
  };
}

function applyReplayFrame(index) {
  if (!replayRows.length) return;
  replayIndex = Math.max(0, Math.min(index, replayRows.length - 1));
  const slider = $("#replaySlider");
  if (slider) slider.value = replayIndex;
  showPage("overview");
  updateTelemetry(replayTelemetry(replayRows[replayIndex]));
}

function startReplayPlayback() {
  clearInterval(replayTimer);
  replayTimer = setInterval(() => {
    if (replayIndex >= replayRows.length - 1) {
      clearInterval(replayTimer);
      return;
    }
    applyReplayFrame(replayIndex + 1);
  }, 120);
}

function renderReplayPanel(detail) {
  replayRows = detail.telemetry || [];
  replayIndex = 0;
  replaySessionName = detail.name;
  const files = detail.files || {};
  $("#drawerContent").innerHTML = `
    <div class="replay-panel">
      <div class="replay-title"><strong>${escapeHtml(detail.name)}</strong><small>${replayRows.length} 帧遥测 · ${detail.warnings.length} 条告警 · ${detail.events.length} 个事件</small></div>
      <input id="replaySlider" type="range" min="0" max="${Math.max(0, replayRows.length - 1)}" value="0">
      <div class="replay-controls">
        <button class="primary-button" id="playReplay">播放</button>
        <button class="ghost-button" id="pauseReplay">暂停</button>
        <button class="ghost-button" id="stepReplay">单步</button>
        <a class="ghost-button report-download" href="${files.telemetry || "#"}">遥测 CSV</a>
      </div>
      <div class="preflight-grid">
        <div class="preflight-item passed"><span>航点</span><strong>${detail.mission.length || 0}</strong><small>mission.json</small></div>
        <div class="preflight-item ${detail.warnings.length ? "failed" : "passed"}"><span>告警</span><strong>${detail.warnings.length}</strong><small>warnings.csv</small></div>
        <div class="preflight-item passed"><span>事件</span><strong>${detail.events.length}</strong><small>events.csv</small></div>
      </div>
    </div>`;
  if (replayRows.length) applyReplayFrame(0);
}

async function loadReplaySession(name) {
  try {
    const detail = await api(`/api/logging/session?name=${encodeURIComponent(name)}`);
    renderReplayPanel(detail);
    showToast("飞行回放已加载", detail.name);
  } catch (error) {
    showToast("回放加载失败", error.message);
  }
}

async function exportBlackboxSession(name) {
  try {
    const result = await api("/api/logging/export", {
      method: "POST",
      body: JSON.stringify({ name })
    });
    window.location.href = result.url;
    showToast("黑匣子已打包", `${result.name} · ${formatBytes(result.size)}`);
  } catch (error) {
    showToast("导出失败", error.message);
  }
}

const settingsRows = () => `
  <div class="drawer-row"><span><strong>遥测声音提醒</strong><small>关键状态变化时播放提示音</small></span><button class="drawer-toggle on" data-toggle title="开关"></button></div>
  <div class="drawer-row"><span><strong>自动确认信息告警</strong><small>仅适用于低优先级提示</small></span><button class="drawer-toggle" data-toggle title="开关"></button></div>
  <div class="drawer-row"><span><strong>高对比度地图</strong><small>增强航迹和地形边界</small></span><button class="drawer-toggle on" data-toggle title="开关"></button></div>
  <div class="drawer-row"><span><strong>数据刷新频率</strong><small>遥测最高 20 Hz，兼容轮询 10 Hz</small></span><strong>高速</strong></div>`;

const accountRows = () => `
  <div class="drawer-row operator-edit-row">
    <span><strong>任务指挥员</strong><small>可自主编辑姓名，本机自动保存</small></span>
    <div class="operator-edit">
      <input id="operatorNameInput" value="${escapeAttribute(currentOperatorName())}" maxlength="18" aria-label="任务指挥员姓名">
      <button class="primary-button" id="saveOperatorName">保存</button>
    </div>
  </div>
  <div class="drawer-row"><span><strong>当前班次</strong><small>2026-06-18 08:00 至 17:00</small></span><strong>进行中</strong></div>
  <div class="drawer-row"><span><strong>操作权限</strong><small>任务创建、航线调整、告警确认</small></span><strong>L2</strong></div>`;

const allAlertsRows = () => {
  const alerts = alertHistory.map((item) => {
    const acknowledged = acknowledgedAlertIds.has(item.id);
    const time = new Date(item.lastSeen).toLocaleTimeString("zh-CN", { hour12: false });
    const state = acknowledged ? "已确认" : "待处理";
    return `<div class="drawer-row">
      <span><strong>${escapeHtml(item.text)}</strong><small>${escapeHtml(item.source)} · ${escapeHtml(item.level.toUpperCase())}${item.count > 1 ? ` · 重复 ${item.count} 次` : ""}</small></span>
      <strong class="${acknowledged ? "state-ok" : "state-warn"}">${state}</strong>
      <time>${time}</time>
    </div>`;
  }).join("");
  return alerts || `<div class="drawer-empty">当前没有飞控 STATUSTEXT 告警</div>`;
};

$("#notificationButton").addEventListener("click", () => openDrawer("通知中心", "NOTIFICATIONS", notificationRows));
$("#settingsButton").addEventListener("click", () => openDrawer("系统设置", "SETTINGS", settingsRows));
$("#accountButton").addEventListener("click", () => openDrawer("账户信息", "OPERATOR", accountRows));
$("#viewAllAlerts").addEventListener("click", () => openDrawer("全部告警", "ALERT CENTER", allAlertsRows));

drawer.addEventListener("click", (event) => {
  const saveOperatorButton = event.target.closest("#saveOperatorName");
  if (saveOperatorButton) {
    const name = applyOperatorName($("#operatorNameInput").value);
    safeStorageSet(OPERATOR_NAME_KEY, name);
    showToast("指挥员已更新", `当前任务指挥员：${name}`);
    return;
  }
  const replayButton = event.target.closest("[data-replay-session]");
  if (replayButton) {
    loadReplaySession(replayButton.dataset.replaySession);
    return;
  }
  const exportButton = event.target.closest("[data-export-session]");
  if (exportButton) {
    exportBlackboxSession(exportButton.dataset.exportSession);
    return;
  }
  if (event.target.closest("#playReplay")) {
    startReplayPlayback();
    return;
  }
  if (event.target.closest("#pauseReplay")) {
    clearInterval(replayTimer);
    return;
  }
  if (event.target.closest("#stepReplay")) {
    applyReplayFrame(replayIndex + 1);
    return;
  }
  const toggle = event.target.closest("[data-toggle]");
  if (!toggle) return;
  toggle.classList.toggle("on");
  showToast("设置已更新", toggle.classList.contains("on") ? "功能已开启" : "功能已关闭");
});

drawer.addEventListener("input", (event) => {
  if (event.target.id !== "replaySlider") return;
  clearInterval(replayTimer);
  applyReplayFrame(Number(event.target.value));
});

drawer.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" || event.target.id !== "operatorNameInput") return;
  event.preventDefault();
  $("#saveOperatorName").click();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    modal.hidden = true;
    closeDrawer();
  }
});

const canvas = $("#flightChart");
const ctx = canvas.getContext("2d");
let altitudeData = [];
let speedData = [];

function drawChart() {
  if (!canvas || !ctx) return;
  const rect = canvas.getBoundingClientRect();
  if (rect.width < 2 || rect.height < 2) return;
  const dpr = window.devicePixelRatio || 1;
  const nextSizeKey = `${Math.round(rect.width)}x${Math.round(rect.height)}@${dpr}`;
  if (chartSizeKey !== nextSizeKey) {
    canvas.width = Math.max(1, Math.floor(rect.width * dpr));
    canvas.height = Math.max(1, Math.floor(rect.height * dpr));
    chartSizeKey = nextSizeKey;
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  const width = rect.width;
  const height = rect.height;
  const pad = { top: 10, right: 14, bottom: 24, left: 36 };
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;
  ctx.clearRect(0, 0, width, height);
  ctx.font = '9px "Segoe UI", sans-serif';
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";

  for (let i = 0; i <= 4; i++) {
    const y = pad.top + innerH * i / 4;
    ctx.strokeStyle = "#223038";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(width - pad.right, y);
    ctx.stroke();
    ctx.fillStyle = "#586a73";
    ctx.fillText(String(150 - i * 15), pad.left - 8, y);
  }

  ctx.textAlign = "center";
  ["-30m", "-20m", "-10m", "现在"].forEach((label, i) => {
    const x = pad.left + innerW * i / 3;
    ctx.fillStyle = "#586a73";
    ctx.fillText(label, x, height - 7);
  });

  function line(data, color, mapValue, fill = false) {
    if (data.length < 2) return;
    ctx.beginPath();
    data.forEach((value, index) => {
      const x = pad.left + innerW * index / (data.length - 1);
      const y = pad.top + innerH * mapValue(value);
      if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    if (fill) {
      const gradient = ctx.createLinearGradient(0, pad.top, 0, pad.top + innerH);
      gradient.addColorStop(0, "rgba(36,201,217,.19)");
      gradient.addColorStop(1, "rgba(36,201,217,0)");
      ctx.lineTo(pad.left + innerW, pad.top + innerH);
      ctx.lineTo(pad.left, pad.top + innerH);
      ctx.closePath();
      ctx.fillStyle = gradient;
      ctx.fill();
      line(data, color, mapValue);
      return;
    }
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.stroke();
  }

  line(altitudeData, "#24c9d9", (value) => 1 - (value - 90) / 60, true);
  line(speedData, "#f2b84b", (value) => 1 - (value - 5) / 18);
  chartDirty = false;
}

window.addEventListener("resize", () => {
  chartDirty = true;
  chartSizeKey = "";
  requestAnimationFrame(drawChart);
});
$("#rangeSelect").addEventListener("change", (event) => {
  const label = event.target.options[event.target.selectedIndex].text;
  showToast("图表范围已更新", label);
  chartDirty = true;
  drawChart();
});
drawChart();

document.addEventListener("click", async (event) => {
  const button = event.target.closest("#runAiPid, #runUlgPid");
  if (!button) return;
  const file = $("#aiPidLogFile")?.files?.[0];
  if (!file) return;
  event.preventDefault();
  event.stopPropagation();
  event.stopImmediatePropagation();
  try {
    await runAiPidLogAnalysisFromSelectedFile();
  } catch (error) {
    $("#aiPidConfidence").textContent = "分析失败";
    showToast("日志分析失败", error.message);
  }
}, true);
