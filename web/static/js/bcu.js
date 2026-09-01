(() => {
  const boot = window.BCU_BOOT || {};
  const screens = {
    splash: document.getElementById("screen-splash"),
    update: document.getElementById("screen-update"),
    idle: document.getElementById("screen-idle"),
    keypad: document.getElementById("screen-keypad"),
    shift: document.getElementById("screen-shift"),
    pick: document.getElementById("screen-pick"),
    running: document.getElementById("screen-running"),
    init: document.getElementById("screen-init"),
    adjust: document.getElementById("screen-adjust"),
    messages: document.getElementById("screen-messages"),
  };

  let brightness = (boot.display && boot.display.brightness) || 3;
  let volume = (boot.display && boot.display.volume) || 4;
  let keypad = { title: "Shift", next: null, mask: false, value: "", from: "idle", max: 8, alpha: false, tapKey: null, tapCount: 0, tapTimer: null };
  let draft = { shift_number: "", badge: "", pin: "" };
  let tripDraft = null;
  let pickState = { from: "shift", onMissing: null };
  let currentScreen = "splash";
  let lastGps = null;
  let lastStatus = null;
  let netBusy = false;
  let netNote = "";
  let netNoteUntil = 0;
  let booting = true;
  const HOLD_SCREENS = ["splash", "update", "keypad", "pick", "init", "adjust", "messages"];

  show("splash");
  applyDisplay();
  buildKeypad();
  poll();
  setInterval(poll, 1000);
  bootNetworkThenContinue();

  document.getElementById("btn-start-shift").onclick = () => {
    draft = { shift_number: "", badge: "", pin: "" };
    openKeypad("Shift", false, (value) => {
      draft.shift_number = value;
      openKeypad("Badge", false, (badge) => {
        draft.badge = badge;
        openKeypad("PIN", true, (pin) => {
          draft.pin = pin;
          startShift();
        }, "idle");
      }, "idle");
    }, "idle");
  };

  document.getElementById("btn-admin").onclick = () => show("init");
  document.getElementById("btn-admin-run").onclick = () => show("init");
  const adminShift = document.getElementById("btn-admin-shift");
  if (adminShift) adminShift.onclick = () => show("init");
  document.getElementById("init-close").onclick = () => resumeWorkScreen();
  document.getElementById("init-next").onclick = () => show("messages");
  const netConnect = document.getElementById("btn-net-connect");
  if (netConnect) netConnect.onclick = () => connectNetwork(false);
  document.getElementById("btn-adjust").onclick = () => {
    renderSteps();
    show("adjust");
  };
  document.getElementById("adjust-cancel").onclick = () => show("init");
  document.getElementById("adjust-ok").onclick = async () => {
    await post("/api/settings", { brightness, volume });
    show("init");
  };
  document.getElementById("msg-close").onclick = () => show("init");
  document.getElementById("btn-mail").onclick = () => show("messages");
  document.getElementById("btn-mail-2").onclick = () => show("messages");
  document.getElementById("btn-mail-run").onclick = () => show("messages");

  document.getElementById("bright-minus").onclick = () => bump("brightness", -1);
  document.getElementById("bright-plus").onclick = () => bump("brightness", 1);
  document.getElementById("vol-minus").onclick = () => bump("volume", -1);
  document.getElementById("vol-plus").onclick = () => bump("volume", 1);

  document.getElementById("keypad-cancel").onclick = () => {
    if (keypad.from === "idle") draft = { shift_number: "", badge: "", pin: "" };
    show(keypad.from || "idle");
  };
  document.getElementById("keypad-back").onclick = () => {
    keypad.value = keypad.value.slice(0, -1);
    keypad.tapKey = null;
    keypad.tapCount = 0;
    if (keypad.tapTimer) clearTimeout(keypad.tapTimer);
    renderKeypadValue();
    beep();
  };
  document.getElementById("keypad-ok").onclick = () => {
    if (!keypad.value) return;
    const done = keypad.next;
    const value = keypad.value;
    keypad.value = "";
    keypad.tapKey = null;
    if (done) done(value);
  };
  document.getElementById("keypad-abc").onclick = () => {
    keypad.alpha = !keypad.alpha;
    keypad.tapKey = null;
    keypad.tapCount = 0;
    if (keypad.tapTimer) clearTimeout(keypad.tapTimer);
    const abc = document.getElementById("keypad-abc");
    abc.classList.toggle("on", keypad.alpha);
    abc.textContent = keypad.alpha ? "123" : "abc";
    paintKeyLabels();
    beep();
  };

  document.getElementById("btn-start-trip").onclick = () => beginTripSelect("shift");
  document.getElementById("btn-change-route").onclick = () => beginTripSelect("running");
  document.getElementById("btn-end-trip").onclick = async () => {
    await post("/api/trip/end", {});
    show("shift");
    poll();
  };
  document.getElementById("btn-end-shift").onclick = () => promptEndShiftPin();
  document.getElementById("pick-cancel").onclick = () => {
    pickState.onMissing = null;
    show(pickState.from || "shift");
  };
  document.getElementById("pick-missing").onclick = () => {
    if (pickState.onMissing) pickState.onMissing();
  };
  document.getElementById("run-swap").onclick = async () => {
    if (!lastStatus || !lastStatus.trip) return;
    const next = lastStatus.trip.direction === "In" ? "Out" : "In";
    await post("/api/trip/start", { ...tripFields(lastStatus.trip), direction: next });
    poll();
  };

  function beginTripSelect(from) {
    pickState.from = from;
    openKeypad("Route", false, (query) => chooseRoute(query, from), from, 6);
  }

  async function chooseRoute(query, from) {
    const matches = await fetchRoutes(query);
    if (!matches.length) {
      openKeypad("Route", false, (q) => chooseRoute(q, from), from, 6);
      return;
    }
    openPick("Select route", matches.map((route) => ({
      title: route.code,
      subtitle: `${route.headsign}  ·  Dir. ${route.direction}  ·  Sec. ${route.section}`,
      onClick: () => chooseTime(route, from),
    })), from, false);
  }

  function chooseTime(route, from) {
    tripDraft = route;
    const times = route.times || [];
    const rows = times.map((time) => ({
      title: time,
      time: true,
      onClick: () => commitTrip(route, time, false),
    }));
    openPick("Trip time", rows, from, true, () => commitTrip(route, "", true));
  }

  async function commitTrip(route, time, missing) {
    await post("/api/trip/start", {
      route_code: route.code,
      route_name: route.name,
      headsign: route.headsign,
      direction: route.direction,
      section: route.section,
      trip_time: time,
      trip_missing: missing,
    });
    show("running");
    poll();
  }

  async function fetchRoutes(query) {
    try {
      const res = await fetch("/api/routes?q=" + encodeURIComponent(query));
      const data = await res.json();
      return data.routes || [];
    } catch (_) {
      return [];
    }
  }

  function openPick(label, rows, from, allowMissing, onMissing) {
    pickState = { from, onMissing: allowMissing ? onMissing : null };
    document.getElementById("pick-label").textContent = label;
    document.getElementById("pick-missing").classList.toggle("hidden", !allowMissing);
    const list = document.getElementById("pick-list");
    list.innerHTML = "";
    if (!rows.length && allowMissing) {
      const empty = document.createElement("div");
      empty.className = "shift-info";
      empty.style.color = "#333";
      empty.textContent = "No scheduled times — use Trip missing";
      list.appendChild(empty);
    }
    rows.forEach((row) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "pick-row" + (row.time ? " pick-time" : "");
      if (row.time) {
        btn.textContent = row.title;
      } else {
        btn.innerHTML = `<strong>${escapeHtml(row.title)}</strong><span>${escapeHtml(row.subtitle || "")}</span>`;
      }
      btn.onclick = () => {
        beep();
        row.onClick();
      };
      list.appendChild(btn);
    });
    show("pick");
  }

  function setUpdateText(text) {
    const el = document.getElementById("update-text");
    if (el) el.textContent = text;
  }

  function setSplashNet(text, kind) {
    const el = document.getElementById("splash-net");
    if (!el) return;
    el.textContent = text;
    el.classList.toggle("warn", kind === "warn");
    el.classList.toggle("ok", kind === "ok");
  }

  function setNetStatus(text, holdMs) {
    const el = document.getElementById("net-status");
    if (el) el.textContent = text;
    if (holdMs) {
      netNote = text;
      netNoteUntil = Date.now() + holdMs;
    }
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  async function bootNetworkThenContinue() {
    const splashMs = (boot.splash_seconds || 5) * 1000;
    const started = Date.now();
    setSplashNet("Connecting to Wi-Fi…");
    const result = await requestConnect(20000);
    if (result.offline) {
      setSplashNet(result.message || "No network found — continuing offline", "warn");
      await sleep(1800);
    } else {
      setSplashNet(
        result.message || (result.ssid ? "Connected to " + result.ssid : "Connected"),
        result.connected ? "ok" : "warn"
      );
    }
    const remain = splashMs - (Date.now() - started);
    if (remain > 0) await sleep(remain);
    fetch("/api/sync", { method: "POST" }).catch(() => {});
    if (currentScreen === "splash" || currentScreen === "update") {
      await runUpdateCheck();
      return;
    }
    finishBoot();
  }

  async function connectNetwork() {
    if (netBusy) return;
    const btn = document.getElementById("btn-net-connect");
    netBusy = true;
    if (btn) btn.disabled = true;
    setNetStatus("Connecting to Wi-Fi…");
    const result = await requestConnect(20000);
    if (result.connected) {
      setNetStatus(result.message || "Connected", 8000);
      fetch("/api/sync", { method: "POST" }).catch(() => {});
    } else {
      setNetStatus(result.message || "No network found — continuing offline", 8000);
    }
    netBusy = false;
    if (btn) btn.disabled = false;
  }

  async function requestConnect(timeoutMs) {
    try {
      const ctrl = new AbortController();
      const abortTimer = setTimeout(() => ctrl.abort(), timeoutMs);
      const res = await fetch("/api/network/connect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
        signal: ctrl.signal,
        cache: "no-store",
      });
      clearTimeout(abortTimer);
      return await res.json();
    } catch (_) {
      return {
        connected: false,
        offline: true,
        message: "No network found — continuing offline",
      };
    }
  }

  async function runUpdateCheck() {
    show("update");
    setUpdateText("Searching for updates…");
    const watchdog = setTimeout(() => {
      if (currentScreen === "update" || currentScreen === "splash") finishBoot();
    }, 180000);
    try {
      const ctrl = new AbortController();
      const abortTimer = setTimeout(() => ctrl.abort(), 90000);
      let check = { available: false, message: "Could not search for updates" };
      try {
        const res = await fetch("/api/update/check", { signal: ctrl.signal, cache: "no-store" });
        check = await res.json();
      } catch (_) {
        /* keep fallback message */
      }
      clearTimeout(abortTimer);
      setUpdateText(check.message || (check.available ? "Update available" : "Software is up to date"));
      await sleep(1800);
      if (check && check.available) {
        setUpdateText("Installing update…");
        let applied = null;
        try {
          applied = await post("/api/update/apply", {}, 120000);
        } catch (_) {
          applied = { ok: false, message: "Update could not be installed" };
        }
        if (applied && applied.ok && applied.restart) {
          setUpdateText(applied.message || "Restarting…");
          await waitForRestart();
          return;
        }
        setUpdateText((applied && (applied.error || applied.message)) || "Update could not be installed");
        await sleep(2500);
      }
    } finally {
      clearTimeout(watchdog);
      if (currentScreen === "update" || currentScreen === "splash") finishBoot();
    }
  }

  function workScreen(data) {
    if (data && data.trip) return "running";
    if (data && data.shift) return "shift";
    return "idle";
  }

  function resumeWorkScreen() {
    show(workScreen(lastStatus));
  }

  function finishBoot() {
    booting = false;
    resumeWorkScreen();
  }

  async function waitForRestart() {
    for (let i = 0; i < 45; i += 1) {
      await sleep(1000);
      try {
        const res = await fetch("/api/status", { cache: "no-store" });
        if (res.ok) {
          window.location.reload();
          return;
        }
      } catch (_) {
        /* service is restarting */
      }
    }
  }

  function show(name) {
    const next = screens[name] || screens.idle;
    if (!next) return;
    next.classList.remove("hidden");
    Object.values(screens).forEach((el) => {
      if (el && el !== next) el.classList.add("hidden");
    });
    currentScreen = screens[name] ? name : "idle";
  }

  const T9 = {
    1: "1",
    2: "ABC",
    3: "DEF",
    4: "GHI",
    5: "JKL",
    6: "MNO",
    7: "PQRS",
    8: "TUV",
    9: "WXYZ",
    0: " ",
  };

  function openKeypad(title, mask, next, from, max) {
    if (keypad.tapTimer) clearTimeout(keypad.tapTimer);
    keypad = { title, next, mask, value: "", from, max: max || 8, alpha: false, tapKey: null, tapCount: 0, tapTimer: null };
    const abc = document.getElementById("keypad-abc");
    abc.classList.remove("on");
    abc.textContent = "abc";
    paintKeyLabels();
    renderKeypadValue();
    show("keypad");
  }

  function buildKeypad() {
    const grid = document.getElementById("keypad-grid");
    grid.innerHTML = "";
    [1, 2, 3, 4, 5, 6, 7, 8, 9, 0].forEach((n) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "key" + (n === 0 ? " key-zero" : "");
      btn.dataset.key = String(n);
      btn.onclick = () => pressKey(n);
      grid.appendChild(btn);
    });
    paintKeyLabels();
  }

  function paintKeyLabels() {
    document.querySelectorAll("#keypad-grid .key").forEach((btn) => {
      const n = Number(btn.dataset.key);
      if (!keypad.alpha) {
        btn.classList.remove("t9");
        btn.textContent = String(n);
        return;
      }
      btn.classList.add("t9");
      const letters = n === 0 ? "␣" : T9[n];
      btn.innerHTML = `<span class="t9-num">${n}</span><span class="t9-let">${letters}</span>`;
    });
  }

  function pressKey(n) {
    if (keypad.alpha) {
      appendT9(n);
      return;
    }
    if (keypad.value.length >= (keypad.max || 8)) return;
    keypad.value += String(n);
    renderKeypadValue();
    beep();
  }

  function appendT9(n) {
    const chars = T9[n] || String(n);
    if (keypad.tapKey === n && keypad.tapTimer) {
      clearTimeout(keypad.tapTimer);
      keypad.tapCount = (keypad.tapCount + 1) % chars.length;
      keypad.value = keypad.value.slice(0, -1) + chars[keypad.tapCount];
    } else {
      if (keypad.value.length >= (keypad.max || 8)) return;
      keypad.tapKey = n;
      keypad.tapCount = 0;
      keypad.value += chars[0];
    }
    keypad.tapTimer = setTimeout(() => {
      keypad.tapKey = null;
    }, 900);
    renderKeypadValue();
    beep();
  }

  function renderKeypadValue() {
    const shown = keypad.mask ? "•".repeat(keypad.value.length) : keypad.value;
    document.getElementById("keypad-prompt").textContent = keypad.title + (shown ? " " + shown : "");
  }

  async function startShift() {
    const res = await post("/api/shift/start", draft);
    if (!res || !res.ok) {
      draft.pin = "";
      openKeypad("PIN", true, (pin) => {
        draft.pin = pin;
        startShift();
      }, "idle");
      return;
    }
    show("shift");
    poll();
  }

  function promptEndShiftPin() {
    openKeypad("PIN", true, (pin) => endShift(pin), "shift");
  }

  async function endShift(pin) {
    const res = await post("/api/shift/end", { pin });
    if (!res || !res.ok) {
      promptEndShiftPin();
      return;
    }
    draft = { shift_number: "", badge: "", pin: "" };
    show("idle");
    poll();
  }

  async function poll() {
    try {
      const res = await fetch("/api/status");
      const data = await res.json();
      lastStatus = data;
      paintStatus(data);
      if (data.trip) {
        const track = await fetch("/api/trip/track?trip_id=" + data.trip.id).then((r) => r.json());
        drawTrace(track.points || []);
      } else {
        drawTrace([]);
      }
    } catch (_) {
      /* offline kiosk still shows last clock tick from previous payload */
    }
  }

  function paintStatus(data) {
    const gpsOk = !!(data.gps && data.gps.ok);
    lastGps = data.gps;
    setClock("hdr-datetime", "footer-clock", data);
    setClock("shift-datetime", "shift-clock", data);
    setClock("run-datetime", "run-clock", data);
    setGps("loc-icon", "loc-text", "hdr-gps", gpsOk);
    setGps("shift-loc-icon", "shift-loc-text", "shift-gps", gpsOk);
    const runGps = document.getElementById("run-gps");
    if (runGps) {
      runGps.textContent = "GPS";
      runGps.className = gpsOk ? "gps-ok" : "gps-bad";
    }
    document.getElementById("unit-line").textContent =
      `${data.unit_id} ${data.software_version}`;
    document.getElementById("last-update").textContent = data.last_update || boot.last_update;

    const info = [];
    if (data.shift) {
      info.push("Shift " + data.shift.shift_number);
      info.push("Badge " + data.shift.badge);
    }
    if (data.trip) info.push("Trip running — GPS logging");
    else if (data.shift) info.push("Ready to start trip");
    if (data.gps && data.gps.ok && Number.isFinite(data.gps.lat) && Number.isFinite(data.gps.lon)) {
      info.push(`${data.gps.lat.toFixed(5)}, ${data.gps.lon.toFixed(5)}`);
    }
    document.getElementById("shift-info").textContent = info.join("  ·  ");
    paintValidators(data.validators || []);
    paintRunning(data);
    if (!netBusy) {
      if (Date.now() < netNoteUntil && netNote) {
        setNetStatus(netNote);
      } else {
        setNetStatus(data.ssid ? "Connected to " + data.ssid : "Wi-Fi: offline");
      }
    }

    if (booting || HOLD_SCREENS.includes(currentScreen)) return;
    const want = workScreen(data);
    if (currentScreen !== want) show(want);
  }

  function paintValidators(list) {
    ["validators-idle", "validators-shift", "validators-run"].forEach((id) => {
      const row = document.getElementById(id);
      if (!row) return;
      row.replaceChildren();
      (list.length ? list : [{ id: 1, ok: false }]).forEach((item) => {
        const box = document.createElement("span");
        box.className = "validator " + (item.ok ? "ok" : "bad");
        box.textContent = String(item.id);
        box.title = item.ok ? "Validator " + item.id + " connected" : "Validator " + item.id + " not connected";
        row.appendChild(box);
      });
    });
  }

  function paintRunning(data) {
    const trip = data.trip || {};
    const shiftNo = data.shift ? data.shift.shift_number : "";
    document.getElementById("run-shift").textContent = shiftNo ? "Shift " + shiftNo : "Shift";
    document.getElementById("run-route").textContent = trip.route_code ? "Route " + trip.route_code : "Route";
    document.getElementById("run-sec").textContent = "Sec. " + (trip.section || "");
    document.getElementById("run-dir").textContent = "Dir. " + (trip.direction || "Out");
    document.getElementById("run-dest").textContent = trip.headsign || trip.route_name || "";
    paintLate(trip, data.clock);
  }

    function paintLate(trip, clock) {
    const bar = document.getElementById("run-late-bar");
    if (!bar) return;
    bar.replaceChildren();
    let late = 0;
    if (trip && !Number(trip.trip_missing) && trip.trip_time && clock) {
      late = minutes(clock) - minutes(trip.trip_time);
    }
    const pos = Math.max(-3, Math.min(3, Math.round(late / 2)));
    for (let i = -3; i <= 3; i++) {
      const tick = document.createElement("div");
      tick.className = "late-tick";
      if (i === pos) {
        tick.classList.add(late > 1 ? "late" : late < -1 ? "early" : "ontime");
      }
      bar.appendChild(tick);
    }
  }

  function minutes(hhmm) {
    const parts = String(hhmm).split(":");
    return Number(parts[0]) * 60 + Number(parts[1] || 0);
  }

  function tripFields(trip) {
    return {
      route_code: trip.route_code,
      route_name: trip.route_name,
      headsign: trip.headsign,
      direction: trip.direction,
      section: trip.section,
      trip_time: trip.trip_time,
      trip_missing: !!Number(trip.trip_missing),
    };
  }

  function setClock(dtId, clockId, data) {
    const dt = document.getElementById(dtId);
    const ck = document.getElementById(clockId);
    if (dt) dt.textContent = data.datetime;
    if (ck) ck.textContent = data.clock;
  }

  function setGps(iconId, textId, gpsId, ok) {
    const icon = document.getElementById(iconId);
    const text = document.getElementById(textId);
    const gps = document.getElementById(gpsId);
    if (!icon) return;
    icon.classList.toggle("ok", ok);
    icon.textContent = ok ? "" : "!";
    text.textContent = ok ? "Location OK" : "Location failure";
    gps.textContent = "GPS";
    gps.className = ok ? "gps-ok" : "gps-bad";
  }

  function drawTrace(points) {
    const canvas = document.getElementById("trace");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const w = canvas.width;
    const h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "rgba(20,28,36,0.35)";
    ctx.fillRect(0, 0, w, h);
    if (points.length < 1) {
      ctx.fillStyle = "#dce3ea";
      ctx.font = "16px Arial";
      ctx.fillText("No trip trace yet", 16, h / 2);
      return;
    }
    const lats = points.map((p) => p.lat);
    const lons = points.map((p) => p.lon);
    const minLa = Math.min(...lats);
    const maxLa = Math.max(...lats);
    const minLo = Math.min(...lons);
    const maxLo = Math.max(...lons);
    const pad = 16;
    const spanLa = Math.max(maxLa - minLa, 0.0002);
    const spanLo = Math.max(maxLo - minLo, 0.0002);
    const xy = (p) => [
      pad + ((p.lon - minLo) / spanLo) * (w - pad * 2),
      h - pad - ((p.lat - minLa) / spanLa) * (h - pad * 2),
    ];
    ctx.strokeStyle = "#5ad17a";
    ctx.lineWidth = 3;
    ctx.beginPath();
    points.forEach((p, i) => {
      const [x, y] = xy(p);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
    const [lx, ly] = xy(points[points.length - 1]);
    ctx.fillStyle = "#e05a2b";
    ctx.beginPath();
    ctx.arc(lx, ly, 5, 0, Math.PI * 2);
    ctx.fill();
  }

  function bump(which, delta) {
    if (which === "brightness") brightness = clamp(brightness + delta, 1, 5);
    else volume = clamp(volume + delta, 1, 5);
    applyDisplay();
    renderSteps();
    beep();
  }

  function renderSteps() {
    paintSteps("bright-steps", brightness);
    paintSteps("vol-steps", volume);
  }

  function paintSteps(id, value) {
    const el = document.getElementById(id);
    el.innerHTML = "";
    for (let i = 1; i <= 5; i++) {
      const s = document.createElement("div");
      s.className = "step" + (i <= value ? " on" : "");
      s.style.height = 12 + i * 8 + "px";
      el.appendChild(s);
    }
  }

  function applyDisplay() {
    document.body.dataset.brightness = String(brightness);
  }

  function beep() {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.frequency.value = 880;
      gain.gain.value = 0.02 * volume;
      osc.connect(gain).connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + 0.04);
    } catch (_) {}
  }

  async function post(url, body, timeoutMs) {
    const ctrl = new AbortController();
    const abortTimer = timeoutMs ? setTimeout(() => ctrl.abort(), timeoutMs) : null;
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {}),
        signal: ctrl.signal,
        cache: "no-store",
      });
      try {
        return await res.json();
      } catch (_) {
        return { ok: false };
      }
    } catch (_) {
      return { ok: false, error: "Request failed" };
    } finally {
      if (abortTimer) clearTimeout(abortTimer);
    }
  }

  function clamp(n, a, b) {
    return Math.max(a, Math.min(b, n));
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  renderSteps();
})();
