(function () {
  function finite(value) {
    return value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value));
  }

  function shortestAngleDelta(target, current) {
    return ((target - current + 540) % 360) - 180;
  }

  function defaultSignedAngle(value) {
    const number = Number(value);
    return `${number >= 0 ? "+" : ""}${number.toFixed(1)}°`;
  }

  function defaultHeadingCardinal(degrees) {
    const directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
    return directions[Math.round(((degrees % 360 + 360) % 360) / 45) % directions.length];
  }

  function setTextIfChanged(node, value) {
    if (!node) return;
    const text = value === null || value === undefined ? "" : String(value);
    if (node.textContent !== text) node.textContent = text;
  }

  function setExclusiveStateClass(node, states, state) {
    if (!node) return;
    if (node.classList.contains(state) && states.every((item) => item === state || !node.classList.contains(item))) return;
    node.classList.remove(...states);
    node.classList.add(state);
  }

  function createRealtimeInstruments(options = {}) {
    const dom = options.elements || {};
    const signedAngle = options.signedAngle || defaultSignedAngle;
    const headingCardinal = options.headingCardinal || defaultHeadingCardinal;
    const formatSpeed = options.formatSpeed || ((value) => finite(value) ? Number(value).toFixed(1) : "--");
    const transportMode = options.transportMode || (() => "");
    const alpha = finite(options.alpha) ? Number(options.alpha) : 0.92;
    const speedAlpha = finite(options.speedAlpha) ? Number(options.speedAlpha) : 0.86;
    const state = {
      initialized: false,
      current: { roll: 0, pitch: 0, yaw: 0, speed: 0 },
      target: { roll: 0, pitch: 0, yaw: 0, speed: null },
      rates: { roll: 0, pitch: 0, yaw: 0 },
      targetAt: 0,
      textAt: 0,
      frames: 0,
      fps: 0,
      fpsAt: 0,
      onlineApplied: false,
    };

    function setStatusOnline() {
      if (state.onlineApplied) return;
      if (dom.attitudeState) {
        setTextIfChanged(dom.attitudeState, "ATTITUDE 在线");
        setExclusiveStateClass(dom.attitudeState, ["offline", "online"], "online");
      }
      if (dom.compassState) {
        setTextIfChanged(dom.compassState, "罗盘在线");
        setExclusiveStateClass(dom.compassState, ["offline", "online"], "online");
      }
      state.onlineApplied = true;
    }

    function setOffline() {
      state.initialized = false;
      state.target.speed = null;
      state.onlineApplied = false;
      dom.smoothAttitudeOffline?.classList.add("visible");
      dom.smoothCompassOffline?.classList.add("visible");
      setTextIfChanged(dom.speed, "--");
      setTextIfChanged(dom.hudGroundSpeed, "--");
      setTextIfChanged(dom.smoothAttitudeDebug, `age -- · fps -- · ${transportMode()}`);
      setTextIfChanged(dom.smoothCompassDebug, `age -- · fps -- · ${transportMode()}`);
    }

    function setTarget(data = {}) {
      const hasRoll = finite(data.roll);
      const hasPitch = finite(data.pitch);
      const hasYaw = finite(data.yaw);
      if (hasRoll) state.target.roll = Number(data.roll);
      if (hasPitch) state.target.pitch = Number(data.pitch);
      if (hasYaw) state.target.yaw = (Number(data.yaw) % 360 + 360) % 360;
      state.rates.roll = finite(data.rollRate) ? Number(data.rollRate) : 0;
      state.rates.pitch = finite(data.pitchRate) ? Number(data.pitchRate) : 0;
      state.rates.yaw = finite(data.yawRate) ? Number(data.yawRate) : 0;
      state.target.speed = finite(data.speed) ? Number(data.speed) : null;
      state.targetAt = finite(data.timestamp) ? Number(data.timestamp) : Date.now();
      if (!state.initialized && (hasRoll || hasPitch || hasYaw || state.target.speed !== null)) {
        state.current.roll = hasRoll ? Number(data.roll) : state.current.roll;
        state.current.pitch = hasPitch ? Number(data.pitch) : state.current.pitch;
        state.current.yaw = hasYaw ? state.target.yaw : state.current.yaw;
        state.current.speed = state.target.speed !== null ? state.target.speed : 0;
        state.initialized = true;
      }
      if (state.initialized) setStatusOnline();
    }

    function applyVisual(roll, pitch, yaw) {
      const clampedPitch = Math.max(-35, Math.min(35, pitch));
      const pitchOffset = clampedPitch * 2.8;
      const normalizedYaw = (yaw % 360 + 360) % 360;
      if (dom.smoothAttitudeLayer) {
        dom.smoothAttitudeLayer.style.transform = `translate3d(0, ${pitchOffset}px, 0) rotate(${-roll}deg)`;
      }
      if (dom.smoothCompassLayer) {
        dom.smoothCompassLayer.style.transform = `translate3d(0, 0, 0) rotate(${-normalizedYaw}deg)`;
      }
      if (dom.hudAttitudeScene) {
        dom.hudAttitudeScene.style.setProperty("--roll-angle", `${-roll}deg`);
        dom.hudAttitudeScene.style.setProperty("--pitch-offset", `${Math.max(-30, Math.min(30, pitch)) * 2.8}px`);
      }
    }

    function updateText(now, ageMs) {
      if (now - state.textAt < 40) return;
      state.textAt = now;
      const pitchText = signedAngle(state.current.pitch);
      const rollText = signedAngle(state.current.roll);
      const yawText = `${state.current.yaw.toFixed(1)}°`;
      const headingText = `${String(Math.round(state.current.yaw)).padStart(3, "0")}°`;
      const cardinal = headingCardinal(state.current.yaw);
      setTextIfChanged(dom.pitchValue, pitchText);
      setTextIfChanged(dom.rollValue, rollText);
      setTextIfChanged(dom.hudPitch, pitchText);
      setTextIfChanged(dom.hudRoll, rollText);
      setTextIfChanged(dom.hudYaw, yawText);
      setTextIfChanged(dom.yawValue, yawText);
      setTextIfChanged(dom.headingValue, headingText);
      setTextIfChanged(dom.headingCardinal, cardinal);
      setTextIfChanged(dom.hudHeading, headingText);
      setTextIfChanged(dom.hudHeadingCardinal, cardinal);
      setTextIfChanged(dom.connectionRoll, rollText);
      setTextIfChanged(dom.connectionPitch, pitchText);
      setTextIfChanged(dom.connectionYaw, yawText);
      if (state.target.speed !== null) {
        const speedText = formatSpeed(state.current.speed);
        setTextIfChanged(dom.speed, speedText);
        setTextIfChanged(dom.hudGroundSpeed, speedText);
      }
      const ageText = Number.isFinite(ageMs) ? `${Math.round(ageMs)}ms` : "--";
      setTextIfChanged(dom.smoothAttitudeDebug, `age ${ageText} · fps ${state.fps} · ${transportMode()}`);
      setTextIfChanged(dom.smoothCompassDebug, `age ${ageText} · fps ${state.fps} · ${transportMode()}`);
    }

    function frame(timestamp = 0) {
      const now = Date.now();
      if (state.initialized) {
        const predictionSeconds = state.targetAt
          ? Math.min(0.12, Math.max(0, now - state.targetAt) / 1000)
          : 0;
        const predictedRoll = state.target.roll + state.rates.roll * predictionSeconds;
        const predictedPitch = state.target.pitch + state.rates.pitch * predictionSeconds;
        const predictedYaw = (state.target.yaw + state.rates.yaw * predictionSeconds + 360) % 360;
        state.current.roll += (predictedRoll - state.current.roll) * alpha;
        state.current.pitch += (predictedPitch - state.current.pitch) * alpha;
        state.current.yaw = (state.current.yaw + shortestAngleDelta(predictedYaw, state.current.yaw) * alpha + 360) % 360;
        if (state.target.speed !== null) {
          state.current.speed += (state.target.speed - state.current.speed) * speedAlpha;
        }
        const ageMs = state.targetAt ? Math.max(0, now - state.targetAt) : Infinity;
        const online = ageMs <= 1200;
        dom.smoothAttitudeOffline?.classList.toggle("visible", !online);
        dom.smoothCompassOffline?.classList.toggle("visible", !online);
        applyVisual(state.current.roll, state.current.pitch, state.current.yaw);
        state.frames += 1;
        if (!state.fpsAt) state.fpsAt = timestamp || performance.now();
        const elapsed = (timestamp || performance.now()) - state.fpsAt;
        if (elapsed >= 1000) {
          state.fps = Math.round(state.frames * 1000 / elapsed);
          state.frames = 0;
          state.fpsAt = timestamp || performance.now();
        }
        updateText(now, ageMs);
      }
      requestAnimationFrame(frame);
    }

    requestAnimationFrame(frame);
    return { setTarget, setOffline, state };
  }

  window.UAVRealtimeInstruments = { create: createRealtimeInstruments };
}());
