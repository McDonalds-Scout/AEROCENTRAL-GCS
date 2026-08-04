(function () {
  function describe(status = {}) {
    const diag = status.diagnostics || {};
    const phase = status.phase || diag.phase || "unknown";
    const summary = diag.summary || status.status || "等待连接状态";
    const advice = diag.advice || "请检查飞控、电源、数据链路和端口配置。";
    const endpoint = diag.endpoint || status.effectiveConnection || status.serialPort || "--";
    const level = status.faultLevel || diag.level || (status.heartbeat ? "ok" : "warning");
    return { phase, summary, advice, endpoint, level };
  }

  function render(status = {}) {
    const info = describe(status);
    const el = document.querySelector("#connectionDiagnostics");
    if (!el) return info;
    el.classList.remove("ok", "warning", "critical", "offline");
    el.classList.add(info.level);
    el.replaceChildren();
    const phase = document.createElement("div");
    const phaseLabel = document.createElement("small");
    const phaseValue = document.createElement("strong");
    phaseLabel.textContent = "诊断阶段";
    phaseValue.textContent = info.phase;
    phase.append(phaseLabel, phaseValue);

    const endpoint = document.createElement("div");
    const endpointLabel = document.createElement("small");
    const endpointValue = document.createElement("strong");
    endpointLabel.textContent = "链路端点";
    endpointValue.textContent = info.endpoint;
    endpoint.append(endpointLabel, endpointValue);

    const message = document.createElement("p");
    message.textContent = `${info.summary}。${info.advice}`;
    el.append(phase, endpoint, message);
    return info;
  }

  function fmtTime(value) {
    if (!value) return "--";
    const ms = typeof value === "number" ? value : Date.parse(value);
    if (!Number.isFinite(ms)) return "--";
    return new Date(ms).toLocaleTimeString("zh-CN", { hour12: false });
  }

  function ageMs(value) {
    if (!value || !Number.isFinite(Number(value))) return null;
    return Date.now() - Number(value);
  }

  function renderMetric(label, value, state = "") {
    return `<div class="gcs-metric ${state}"><small>${label}</small><strong>${value ?? "--"}</strong></div>`;
  }

  function renderPage(status = {}, telemetry = {}) {
    const page = document.querySelector("#gcsDiagnosticPage");
    if (!page) return;
    status = status || {};
    telemetry = telemetry || {};
    const diag = describe(status);
    const heartbeat = telemetry.gcsHeartbeat || {};
    const vehicleHeartbeatAge = ageMs(telemetry.vehicleHeartbeatAt);
    const connected = !!(status.heartbeat || telemetry.connected || telemetry.targetIdentified);
    const targetState = telemetry.targetIdentified
      ? `${telemetry.targetSystem}:${telemetry.targetComponent}`
      : connected
        ? "MAVLink target not identified"
        : "No vehicle heartbeat";
    const badge = document.querySelector("#gcsDiagnosticBadge");
    if (badge) {
      badge.textContent = connected ? "Connected" : "Disconnected";
      badge.classList.toggle("offline", !connected);
    }
    const summary = document.querySelector("#gcsDiagnosticSummary");
    if (summary) {
      summary.textContent = connected
        ? `Target ${targetState} · GCS heartbeat ${heartbeat.sending ? "sending" : "not sending"}`
        : "No vehicle heartbeat";
    }
    const grid = document.querySelector("#gcsDiagnosticGrid");
    if (grid) {
      grid.innerHTML = [
        renderMetric("Connected", connected ? "Connected" : "Disconnected", connected ? "ok" : "warn"),
        renderMetric("connection string", diag.endpoint || status.effectiveConnection || "--"),
        renderMetric("target_system", telemetry.targetSystem ?? "--", telemetry.targetIdentified ? "ok" : "warn"),
        renderMetric("target_component", telemetry.targetComponent ?? "--", telemetry.targetIdentified ? "ok" : "warn"),
        renderMetric("our_system_id", heartbeat.ourSystemId ?? "--"),
        renderMetric("our_component_id", heartbeat.ourComponentId ?? "--"),
        renderMetric("GCS heartbeat", heartbeat.sending ? `${heartbeat.rateHz || 0} Hz` : "not sending", heartbeat.sending ? "ok" : "warn"),
        renderMetric("last GCS heartbeat", fmtTime(heartbeat.lastSentMs)),
        renderMetric("last vehicle heartbeat", telemetry.vehicleHeartbeatAt ? `${fmtTime(telemetry.vehicleHeartbeatAt)} (${vehicleHeartbeatAge === null ? "--" : Math.round(vehicleHeartbeatAge / 1000) + "s"})` : "No vehicle heartbeat"),
        renderMetric("armed/disarmed", telemetry.armed ? "Armed" : "Disarmed", telemetry.armed ? "warn" : "ok"),
        renderMetric("flight mode", telemetry.mode || "--"),
        renderMetric("packet rate", `${status.rateHz || 0} Hz`),
        renderMetric("packet loss", status.packetLoss ?? telemetry.packetLoss ?? "--"),
        renderMetric("MAVLink version", telemetry.vehicleMavlinkVersion ?? "--"),
        renderMetric("vehicle type", telemetry.vehicle_type ?? telemetry.vehicleType ?? "--"),
        renderMetric("autopilot", telemetry.vehicleAutopilot ?? telemetry.autopilot ?? "--"),
        renderMetric("base_mode", telemetry.vehicleBaseMode ?? "--"),
        renderMetric("custom_mode", telemetry.vehicleCustomMode ?? "--"),
      ].join("");
    }
    renderStatusTextList(telemetry.statustexts || telemetry.statusTexts || []);
    renderCommandAckList(telemetry.commandAcks || []);
    renderArmingDiagnostic(status, telemetry, connected);
  }

  function mappedPwm(telemetry, key) {
    const item = telemetry?.rcMapped?.[key];
    return Number.isFinite(Number(item?.pwm)) ? Number(item.pwm) : null;
  }

  function mappedChannel(telemetry, key) {
    const item = telemetry?.rcMapped?.[key];
    return item?.channel ? `CH${item.channel}` : "--";
  }

  function latestArmingText(telemetry = {}) {
    const terms = ["preflight", "arming", "arm", "safety", "throttle", "rc", "ekf", "gps", "compass", "battery", "failsafe"];
    const items = [...(telemetry.statustexts || telemetry.statusTexts || [])].reverse();
    return items.find((item) => terms.some((term) => String(item.text || "").toLowerCase().includes(term)));
  }

  function renderCheck(label, value, ok, detail = "") {
    return `
      <div class="arming-check ${ok ? "ok" : "warn"}">
        <small>${label}</small>
        <strong>${value}</strong>
        <span>${detail}</span>
      </div>
    `;
  }

  function renderArmingDiagnostic(status = {}, telemetry = {}, connected = false) {
    const grid = document.querySelector("#armingDiagnosticGrid");
    const reason = document.querySelector("#armingDiagnosticReason");
    if (!grid && !reason) return;
    const throttlePwm = mappedPwm(telemetry, "throttle");
    const throttlePercent = Number.isFinite(Number(telemetry?.rcMapped?.throttle?.percent))
      ? Number(telemetry.rcMapped.throttle.percent)
      : null;
    const armPwm = mappedPwm(telemetry, "armSwitch");
    const flightModePwm = mappedPwm(telemetry, "flightMode");
    const latest = latestArmingText(telemetry);
    const targetOk = !!telemetry.targetIdentified;
    const heartbeatOk = !!(telemetry.gcsHeartbeat?.sending);
    const rcMapOk = !!telemetry.rcMapAvailable;
    const throttleLow = throttlePercent !== null && throttlePercent <= 8;
    const landed = telemetry.landedState === 1 || telemetry.landedState === "1";
    const rows = [
      renderCheck("Vehicle Heartbeat", connected ? "OK" : "No vehicle heartbeat", connected),
      renderCheck("MAVLink Target", targetOk ? `${telemetry.targetSystem}:${telemetry.targetComponent}` : "Not identified", targetOk),
      renderCheck("GCS Heartbeat", heartbeatOk ? `${telemetry.gcsHeartbeat.rateHz || 0} Hz` : "Not sending", heartbeatOk),
      renderCheck("RC_MAP", rcMapOk ? "Available" : "Unavailable", rcMapOk, rcMapOk ? "按 PX4 RC_MAP 映射" : "只能看 raw channels"),
      renderCheck("Throttle", throttlePwm === null ? "--" : `${Math.round(throttlePwm)} us`, throttleLow, throttlePercent === null ? "等待 RC_MAP_THROTTLE" : `${throttlePercent.toFixed(1)}% · ${mappedChannel(telemetry, "throttle")}`),
      renderCheck("Arm Switch", armPwm === null ? "--" : `${Math.round(armPwm)} us`, armPwm !== null, mappedChannel(telemetry, "armSwitch")),
      renderCheck("Flight Mode CH", flightModePwm === null ? "--" : `${Math.round(flightModePwm)} us`, flightModePwm !== null, mappedChannel(telemetry, "flightMode")),
      renderCheck("PX4 Armed", telemetry.armed ? "Armed" : "Disarmed", !telemetry.armed),
      renderCheck("Landed State", landed ? "On ground" : (telemetry.landedState ?? "--"), landed),
    ];
    if (grid) grid.innerHTML = rows.join("");
    if (reason) {
      reason.textContent = latest
        ? `最近解锁相关飞控提示：${latest.severity || "INFO"} · ${latest.text || ""}`
        : "没有收到 Arming denied / Preflight Fail 相关 STATUSTEXT。若 QGC 能看到原因，请保持 UI 连接后再拨动解锁开关。";
      reason.classList.toggle("warn", !!latest);
    }
  }

  function renderStatusTextList(items) {
    const host = document.querySelector("#statusTextList");
    if (!host) return;
    const rows = [...items].slice(-100).reverse();
    host.innerHTML = rows.length
      ? rows.map((item) => `
        <div class="status-text-item ${item.highlight ? "highlight" : ""}">
          <small>${fmtTime(item.timeMs)} · ${item.severity || "INFO"}${item.category ? " · " + item.category : ""}</small>
          <span>${item.text || ""}</span>
        </div>
      `).join("")
      : '<div class="empty-line">暂无 STATUSTEXT</div>';
  }

  function renderCommandAckList(items) {
    const host = document.querySelector("#commandAckList");
    if (!host) return;
    const rows = [...items].slice(-100).reverse();
    host.innerHTML = rows.length
      ? rows.map((item) => `
        <div class="command-ack-item ${item.resultText === "ACCEPTED" ? "ok" : "warn"}">
          <small>${fmtTime(item.timeMs)} · command ${item.command}</small>
          <strong>${item.resultText || item.result}</strong>
        </div>
      `).join("")
      : '<div class="empty-line">暂无 COMMAND_ACK</div>';
  }

  window.GCSConnectionDiagnostics = { describe, render, renderPage };
}());
