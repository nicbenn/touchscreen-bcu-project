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
  let keypad = { title: "Shift number", next: null, mask: false, value: "", from: "idle", max: 8 };
  let draft = { shift_number: "", badge: "", pin: "" };
  let tripDraft = null;
  let pickState = { from: "shift", onMissing: null };
  let currentScreen = "splash";
  let lastGps = null;
  let lastStatus = null;

  show("splash");
  setTimeout(() => {
    if (currentScreen === "splash") runUpdateCheck();
  }, (boot.splash_seconds || 5) * 1000);
  applyDisplay();
  buildKeypad();
  poll();
  setInterval(poll, 1000);
  fetch("/api/sync", { method: "POST" }).catch(() => {});

  document.getElementById("btn-start-shift").onclick = () => {
    draft = { shift_number: "", badge: "", pin: "" };
    openKeypad("Shift number", false, (value) => {
      draft.shift_number = value;
      openKeypad("Badge number", false, (badge) => {
        draft.badge = badge;
        openKeypad("PIN number", true, (pin) => {
          draft.pin = pin;
          startShift();
        }, "idle");
      }, "idle");
    }, "idle");
  };

  document.getElementById("btn-admin").onclick = () => show("init");
  document.getElementById("btn-admin-run").onclick = () => show("init");
  document.getElementById("init-close").onclick = () => show(lastStatus && lastStatus.trip ? "running" : lastStatus && lastStatus.shift ? "shift" : "idle");
  document.getElementById("init-next").onclick = () => show("messages");
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
    renderKeypadValue();
    beep();
  };
  document.getElementById("keypad-ok").onclick = () => {
    if (!keypad.value) return;
    const done = keypad.next;
    const value = keypad.value;
    keypad.value = "";
    if (done) done(value);
  };

  document.getElementById("btn-start-trip").onclick = () => beginTripSelect("shift");
  document.getElementById("btn-change-route").onclick = () => beginTripSelect("running");
  document.getElementById("btn-end-trip").onclick = async () => {
    await post("/api/trip/end", {});
    show("shift");
    poll();
  };
  document.getElementById("btn-end-shift").onclick = async () => {
    await post("/api/shift/end", {});
    show("idle");
  };
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
    openKeypad("Route number", false, (query) => chooseRoute(query, from), from, 6);
  }

  async function chooseRoute(query, from) {
    const matches = await fetchRoutes(query);
    if (!matches.length) {
      openKeypad("Route number", false, (q) => chooseRoute(q, from), from, 6);
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

  async function runUpdateCheck() {
    show("update");
    setUpdateText("Searching for updates…");
    let check = null;
    try {
      check = await fetch("/api/update/check").then((r) => r.json());
    } catch (_) {
      setUpdateText("No network — continuing");
      setTimeout(() => show("idle"), 900);
      return;
    }
    if (!check || !check.available) {
      setUpdateText((check && check.message) || "Software is up to date");
      setTimeout(() => show("idle"), 900);
      return;
    }
    setUpdateText("Installing update…");
    let applied = null;
    try {
      applied = await post("/api/update/apply", {});
    } catch (_) {
      setUpdateText("Update failed — continuing");
      setTimeout(() => show("idle"), 1200);
      return;
    }
    if (!applied || !applied.ok) {
      setUpdateText((applied && applied.error) || "Update failed — continuing");
      setTimeout(() => show("idle"), 1200);
      return;
    }
    setUpdateText("Restarting…");
    waitForRestart();
  }

  async function waitForRestart() {
    for (let i = 0; i < 40; i++) {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      try {
        const res = await fetch("/api/status", { cache: "no-store" });
        if (res.ok) {
          window.location.reload();
          return;
        }
      } catch (_) {}
    }
    show("idle");
  }

  function show(name) {
    Object.values(screens).forEach((el) => el.classList.add("hidden"));
    screens[name].classList.remove("hidden");
    currentScreen = name;
  }

  function openKeypad(title, mask, next, from, max) {
    keypad = { title, next, mask, value: "", from, max: max || 8 };
    document.getElementById("keypad-label").textContent = title;
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
      btn.textContent = String(n);
      btn.onclick = () => {
        if (keypad.value.length >= (keypad.max || 8)) return;
        keypad.value += String(n);
        renderKeypadValue();
        beep();
      };
      grid.appendChild(btn);
    });
  }

  function renderKeypadValue() {
    const shown = keypad.mask ? "•".repeat(keypad.value.length) : keypad.value;
    document.getElementById("keypad-value").textContent = shown;
  }

  async function startShift() {
    const res = await post("/api/shift/start", draft);
    if (!res || !res.ok) {
      draft.pin = "";
      openKeypad("PIN number", true, (pin) => {
        draft.pin = pin;
        startShift();
      }, "idle");
      return;
    }
    show("shift");
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
    if (data.gps && data.gps.ok) {
      info.push(`${data.gps.lat.toFixed(5)}, ${data.gps.lon.toFixed(5)}`);
    }
    document.getElementById("shift-info").textContent = info.join("  ·  ");
    paintValidators(data.validators || []);
    paintRunning(data);

    if (data.trip && (currentScreen === "shift" || currentScreen === "idle")) show("running");
    if (!data.trip && currentScreen === "running") show("shift");
  }

  function paintValidators(list) {
    ["validators-idle", "validators-shift"].forEach((id) => {
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
    const label = document.getElementById("run-late-label");
    bar.innerHTML = "";
    let late = 0;
    if (trip && !Number(trip.trip_missing) && trip.trip_time && clock) {
      late = minutes(clock) - minutes(trip.trip_time);
    }
    const shown = Math.max(0, Math.min(10, late));
    for (let i = 1; i <= 10; i++) {
      const tick = document.createElement("div");
      tick.className = "late-tick" + (i <= shown ? " on" : "");
      tick.style.height = 8 + i * 1.4 + "px";
      bar.appendChild(tick);
    }
    label.textContent = late > 0 ? "+" + late : "";
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

  async function post(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    try {
      return await res.json();
    } catch (_) {
      return { ok: false };
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
