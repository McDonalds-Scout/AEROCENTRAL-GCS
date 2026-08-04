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
        dom.attitudeState.textContent = "ATTITUDE 在线";
        dom.attitudeState.classList.remove("offline");
        dom.attitudeState.classList.add("online");
      }
      if (dom.compassState) {
        dom.compassState.textContent = "罗盘在线";
        dom.compassState.classList.remove("offline");
        dom.compassState.classList.add("online");
      }
      state.onlineApplied = true;
    }

    function setOffline() {
      state.initialized = false;
      state.target.speed = null;
      state.onlineApplied = false;
      dom.smoothAttitudeOffline?.classList.add("visible");
      dom.smoothCompassOffline?.classList.add("visible");
      if (dom.speed) dom.speed.textContent = "--";
      if (dom.hudGroundSpeed) dom.hudGroundSpeed.textContent = "--";
      if (dom.smoothAttitudeDebug) dom.smoothAttitudeDebug.textContent = `age -- · fps -- · ${transportMode()}`;
      if (dom.smoothCompassDebug) dom.smoothCompassDebug.textContent = `age -- · fps -- · ${transportMode()}`;
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
      if (state.target.speed !== null) {
        const speedText = formatSpeed(state.current.speed);
        if (dom.speed) dom.speed.textContent = speedText;
        if (dom.hudGroundSpeed) dom.hudGroundSpeed.textContent = speedText;
      }
      const ageText = Number.isFinite(ageMs) ? `${Math.round(ageMs)}ms` : "--";
      if (dom.smoothAttitudeDebug) dom.smoothAttitudeDebug.textContent = `age ${ageText} · fps ${state.fps} · ${transportMode()}`;
      if (dom.smoothCompassDebug) dom.smoothCompassDebug.textContent = `age ${ageText} · fps ${state.fps} · ${transportMode()}`;
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
