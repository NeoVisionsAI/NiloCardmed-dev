/* NiloCardmed — GUI aprovisionamiento WiFi (servida desde el Pi) */
(function () {
  "use strict";

  const API_BASE = window.location.origin;
  const NA = "—";
  const TOKEN_KEY = "nilocardmed_token";

  const TIMEOUT = {
    status: 10000,
    dashboard: 10000,
    auth: 30000,
    command: 30000,
    wifiScan: 90000,
    wifiConnect: 90000,
    cardmedTest: 90000,
    scanMinWait: 7000,
    scanRetryWait: 4000,
  };

  let token = sessionStorage.getItem(TOKEN_KEY) || "";
  let dashboard = null;
  let loadingDepth = 0;
  let toastTimer = null;

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => document.querySelectorAll(sel);

  function show(el) { el.hidden = false; }
  function hide(el) { el.hidden = true; }

  function setLoading(on) {
    loadingDepth += on ? 1 : -1;
    if (loadingDepth < 0) loadingDepth = 0;
    const bar = $("#loading-bar");
    if (loadingDepth > 0) {
      show(bar);
      bar.setAttribute("aria-hidden", "false");
    } else {
      hide(bar);
      bar.setAttribute("aria-hidden", "true");
    }
  }

  async function withLoading(fn) {
    setLoading(true);
    try {
      return await fn();
    } finally {
      setLoading(false);
    }
  }

  function toast(msg, ms = 3200) {
    const el = $("#toast");
    el.textContent = msg;
    show(el);
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => hide(el), ms);
  }

  function setBanner(msg) {
    const el = $("#banner-error");
    if (!msg) { hide(el); el.textContent = ""; return; }
    el.textContent = msg;
    show(el);
  }

  function formatError(err) {
    if (!err) return "Error desconocido";
    if (err.name === "AbortError") {
      return "Tiempo de espera agotado. ¿Estás conectado a la WiFi Nilocardmed-Config-xxxx?";
    }
    const msg = String(err.message || err);
    if (msg.includes("Failed to fetch") || msg.includes("NetworkError")) {
      return "No se pudo contactar con el Pi. Conecta la tablet al AP Nilocardmed-Config-xxxx.";
    }
    return msg;
  }

  async function fetchWithTimeout(url, options = {}, timeoutMs = TIMEOUT.command) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const res = await fetch(url, {
        ...options,
        signal: controller.signal,
        cache: "no-store",
      });
      const text = await res.text();
      let data;
      try { data = JSON.parse(text); } catch { data = text; }
      return { ok: res.ok, status: res.status, data };
    } finally {
      clearTimeout(timer);
    }
  }

  async function apiGet(path, timeoutMs = TIMEOUT.command) {
    return fetchWithTimeout(`${API_BASE}${path}`, {}, timeoutMs);
  }

  async function apiCommand(cmd, payload = {}, timeoutMs = TIMEOUT.command) {
    if (!token && cmd !== "auth") {
      throw new Error("Sin sesión. Autentica primero con la contraseña del dispositivo.");
    }
    const headers = { "Content-Type": "application/json" };
    if (token) headers.Authorization = `Bearer ${token}`;
    const body = JSON.stringify({
      cmd,
      id: String(Date.now()),
      payload,
    });
    const res = await fetchWithTimeout(
      `${API_BASE}/api/command`,
      { method: "POST", headers, body },
      timeoutMs,
    );
    if (!res.ok && typeof res.data === "object" && res.data.error === "dashboard_failed") {
      throw new Error("Error interno del Pi al generar el panel (dashboard_failed). Ejecuta update.sh en la Pi.");
    }
    if (typeof res.data === "object" && res.data.ok === false) {
      const err = res.data.error || "comando fallido";
      throw new Error(typeof err === "string" ? err : JSON.stringify(err));
    }
    return res.data;
  }

  let commandBusy = false;

  async function runQueued(fn) {
    if (commandBusy) {
      toast("Espera a que termine la operación anterior…");
      return null;
    }
    commandBusy = true;
    return withLoading(async () => {
      try {
        return await fn();
      } finally {
        commandBusy = false;
      }
    });
  }

  function formatIsoDate(iso) {
    if (!iso) return NA;
    try {
      return new Date(iso).toLocaleString("es-ES");
    } catch {
      return iso;
    }
  }

  function formatPower(power) {
    if (!power) return { source: NA, level: NA };
    const src = power.power_source || "";
    let source = power.source_label || NA;
    let level = NA;
    if (src === "mains") { source = "Corriente"; level = "100 %"; }
    else if (src === "usb") { source = "USB"; level = "100 %"; }
    else if (src === "powerbank") {
      source = "Powerbank";
      level = power.display_percent != null ? `${power.display_percent} %` : NA;
    } else {
      level = power.display_percent != null ? `${power.display_percent} %` : NA;
    }
    return { source, level };
  }

  function formatTemp(system) {
    if (!system || !system.available || system.celsius == null) return NA;
    return `${system.celsius} °C`;
  }

  function statusVal(value, positive) {
    const cls = positive === true ? " status-row__val--ok" : positive === false ? " status-row__val--warn" : "";
    return `<span class="status-row__val${cls}">${value ?? NA}</span>`;
  }

  function buildStatusCards(data) {
    const wifi = data?.wifi || {};
    const power = formatPower(data?.power);
    const sampling = data?.sampling || {};
    const camera = data?.camera || {};
    const captures = data?.captures || {};
    const system = data?.system || {};

    const wifiConnected = wifi.connected === true;
    const cards = [
      {
        title: "WiFi",
        rows: [
          ["Activo", wifiConnected ? "Sí" : "No", wifiConnected],
          ["SSID", wifiConnected ? (wifi.ssid || NA) : NA],
          ["IP", wifiConnected ? (wifi.ip_address || NA) : NA],
          ["Señal", wifiConnected && wifi.signal != null ? `${wifi.signal} dBm` : NA],
          ["Internet", wifiConnected ? (wifi.connectivity_ok ? "Sí" : "No") : null],
        ],
      },
      {
        title: "Alimentación",
        rows: [["Fuente", power.source], ["Nivel", power.level]],
      },
      {
        title: "Sistema",
        rows: [["Temperatura", formatTemp(system)]],
      },
      {
        title: "Muestreo",
        rows: [
          ["Intervalo", sampling.interval_seconds != null ? `${sampling.interval_seconds} s` : NA],
          ["Estado", sampling.window_active ? "Activo" : "Inactivo", sampling.window_active],
        ],
      },
      {
        title: "Cámara",
        rows: [
          ["Conectada", camera.connected ? "Sí" : "No", camera.connected],
          ["Detectadas", camera.cameras_count != null ? String(camera.cameras_count) : NA],
          ["Guardada", camera.saved_device_present ? "Sí" : "No", camera.saved_device_present],
          ["Dispositivo", camera.saved_device || NA],
        ],
      },
      {
        title: "Config",
        rows: [["Guardada", formatIsoDate(data?.config_last_saved_at)]],
      },
      {
        title: "Capturas",
        rows: [
          ["Ciclos OK", captures.cycles_successful != null ? String(captures.cycles_successful) : NA],
          ["En disco", captures.images_on_disk != null ? String(captures.images_on_disk) : NA],
        ],
      },
    ];

    const grid = $("#status-cards");
    grid.innerHTML = cards.map((c) => `
      <article class="status-card">
        <h3 class="status-card__title">${c.title}</h3>
        <div class="status-card__rows">
          ${c.rows.map(([k, v, positive]) => `
            <div class="status-row">
              <span class="status-row__key">${k}</span>
              ${statusVal(v, positive)}
            </div>`).join("")}
        </div>
      </article>
    `).join("");
  }

  function syncCaptureIntervalInput() {
    const input = $("#capture-interval");
    if (!input || !dashboard?.sampling) return;
    const val = dashboard.sampling.interval_seconds;
    if (val != null) input.value = String(val);
  }

  async function fetchDashboard() {
    let res = await apiGet("/api/dashboard", TIMEOUT.dashboard);
    if (res.ok && res.data && !res.data.error) {
      return res.data;
    }
    if (token) {
      const cmd = await apiCommand("device_status", {}, TIMEOUT.dashboard);
      return cmd.data || cmd;
    }
    throw new Error(res.data?.detail || res.data?.error || "No se pudo cargar el panel");
  }

  async function refreshDashboard() {
    setBanner("");
    try {
      dashboard = await fetchDashboard();
      buildStatusCards(dashboard);
      syncCaptureIntervalInput();
      $("#dashboard-meta").textContent = dashboard.refreshed_at
        ? `Actualizado: ${formatIsoDate(dashboard.refreshed_at)}`
        : "Actualizado";
    } catch (e) {
      setBanner(formatError(e));
      if (!dashboard) buildStatusCards(null);
    }
  }

  async function loadDeviceInfo() {
    try {
      const res = await apiGet("/api/status", TIMEOUT.status);
      if (res.ok && res.data) {
        const name = res.data.device_name || res.data.device || "Nilocardmed";
        const ver = res.data.version || "";
        const label = ver ? `${name} · v${ver}` : name;
        $("#header-subtitle").textContent = label;
        $("#login-subtitle").textContent = label;
      }
    } catch { /* ignore */ }
  }

  function showView(name) {
    hide($("#view-login"));
    hide($("#view-main"));
    if (name === "login") show($("#view-login"));
    if (name === "main") show($("#view-main"));
  }

  function setAuthenticated(on) {
    if (on) {
      showView("main");
      refreshDashboard();
    } else {
      token = "";
      sessionStorage.removeItem(TOKEN_KEY);
      showView("login");
    }
  }

  function switchTab(tabId) {
    $$(".tab").forEach((t) => {
      t.classList.toggle("tab--active", t.dataset.tab === tabId);
    });
    $$(".tab-panel").forEach((p) => hide(p));
    const panel = $(`#panel-${tabId}`);
    if (panel) show(panel);
    if (tabId === "wifi" && $("#wifi-ssid").options.length <= 1) {
      $("#btn-wifi-scan").click();
    }
  }

  function bindPasswordToggle(inputId, btnId) {
    const input = $(inputId);
    const btn = $(btnId);
    btn.addEventListener("click", () => {
      const visible = input.type === "text";
      input.type = visible ? "password" : "text";
      btn.classList.toggle("is-visible", !visible);
    });
  }

  function formatScanMode(mode) {
    if (!mode) return "";
    if (String(mode).includes("iw")) return "Escaneo ampliado (iw)";
    return String(mode);
  }

  async function withScanMinWait(promise, ms = TIMEOUT.scanMinWait) {
    const [result] = await Promise.all([
      promise,
      new Promise((r) => setTimeout(r, ms)),
    ]);
    return result;
  }

  function fillWifiSelect(networks) {
    const select = $("#wifi-ssid");
    const current = select.value;
    select.innerHTML = '<option value="">— Selecciona red —</option>';
    networks.forEach((n) => {
      const opt = document.createElement("option");
      opt.value = n.ssid;
      const sig = n.signal != null ? ` (${n.signal}${n.signal > 0 && n.signal <= 100 ? "%" : " dBm"})` : "";
      const active = n.in_use ? " ★" : "";
      opt.textContent = `${n.ssid}${sig}${active}`;
      select.appendChild(opt);
    });
    if (current && networks.some((n) => n.ssid === current)) {
      select.value = current;
    }
  }

  async function performWifiScan(retry = false) {
    const res = await apiCommand(
      "wifi_scan",
      { rescan: true },
      TIMEOUT.wifiScan,
    );
    let networks = res.data?.networks || [];

    if (!retry && networks.length <= 1) {
      await new Promise((r) => setTimeout(r, TIMEOUT.scanRetryWait));
      const retryRes = await apiCommand(
        "wifi_scan",
        { rescan: true },
        TIMEOUT.wifiScan,
      );
      const retryNetworks = retryRes.data?.networks || [];
      if (retryNetworks.length > networks.length) {
        networks = retryNetworks;
        res.data = retryRes.data;
      }
    }

    return { networks, scanMode: res.data?.scan_mode };
  }

  function resetCameraPreview() {
    const img = $("#camera-image");
    img.removeAttribute("src");
    img.alt = "";
    hide(img);
    show($("#camera-placeholder"));
  }

  function showCameraImage(src) {
    const img = $("#camera-image");
    const ph = $("#camera-placeholder");
    return new Promise((resolve, reject) => {
      img.onload = () => {
        hide(ph);
        show(img);
        resolve();
      };
      img.onerror = () => {
        resetCameraPreview();
        reject(new Error("No se pudo mostrar la imagen capturada"));
      };
      img.alt = "Captura de prueba";
      img.src = src;
    });
  }

  async function loadChunkedCapture(meta) {
    const parts = [];
    const total = meta.total_chunks || 0;
    for (let i = 0; i < total; i += 1) {
      const chunk = await apiCommand(
        "camera_capture_chunk",
        { capture_id: meta.capture_id, index: i },
        TIMEOUT.command,
      );
      const b64 = chunk.data?.chunk_base64;
      if (!b64) throw new Error(`Chunk ${i} vacío`);
      const binary = atob(b64);
      const bytes = new Uint8Array(binary.length);
      for (let j = 0; j < binary.length; j += 1) bytes[j] = binary.charCodeAt(j);
      parts.push(bytes);
    }
    const totalLen = parts.reduce((sum, p) => sum + p.length, 0);
    const merged = new Uint8Array(totalLen);
    let offset = 0;
    for (const part of parts) {
      merged.set(part, offset);
      offset += part.length;
    }
    const blob = new Blob([merged], { type: "image/jpeg" });
    return URL.createObjectURL(blob);
  }

  async function displayCaptureResult(data) {
    resetCameraPreview();
    if (data.mode === "base64" && data.image_base64) {
      await showCameraImage(`data:image/jpeg;base64,${data.image_base64}`);
    } else if (data.mode === "chunked" && data.total_chunks) {
      const url = await loadChunkedCapture(data);
      try {
        await showCameraImage(url);
      } finally {
        URL.revokeObjectURL(url);
      }
    } else {
      throw new Error(data.hint || "Respuesta de captura sin imagen");
    }
  }

  // --- Login ---
  $("#form-login").addEventListener("submit", async (e) => {
    e.preventDefault();
    hide($("#login-error"));
    const password = $("#login-password").value;
    await withLoading(async () => {
      try {
        const res = await apiCommand("auth", { password }, TIMEOUT.auth);
        token = res.data?.token || "";
        if (!token) throw new Error("Contraseña incorrecta");
        sessionStorage.setItem(TOKEN_KEY, token);
        toast("Sesión iniciada.");
        setAuthenticated(true);
      } catch (err) {
        const msg = String(err.message || "").includes("invalid_password")
          ? "Contraseña incorrecta"
          : formatError(err);
        $("#login-error").textContent = msg;
        show($("#login-error"));
      }
    });
  });

  // --- Tabs ---
  $$(".tab").forEach((tab) => {
    tab.addEventListener("click", () => switchTab(tab.dataset.tab));
  });

  // --- Estado ---
  $("#btn-refresh-dashboard").addEventListener("click", () => runQueued(refreshDashboard));

  // --- WiFi ---
  $("#btn-wifi-scan").addEventListener("click", () => runQueued(async () => {
    const hint = $("#wifi-scan-hint");
    const meta = $("#wifi-scan-meta");
    show(hint);
    meta.textContent = "Escaneando…";
    hide($("#wifi-result"));
    try {
      toast("Escaneando WiFi en el Pi…");
      const { networks, scanMode } = await withScanMinWait(performWifiScan());
      fillWifiSelect(networks);
      meta.textContent = networks.length
        ? `${networks.length} red(es). ${formatScanMode(scanMode)}`
        : "No se encontraron redes.";
      if (networks.length <= 1) {
        toast("Pocas redes detectadas. Acerca la tablet al router o reintenta.");
      } else {
        toast(`${networks.length} redes encontradas.`);
      }
    } catch (e) {
      meta.textContent = "";
      setBanner(formatError(e));
    } finally {
      hide(hint);
    }
  }));

  $("#btn-wifi-connect").addEventListener("click", () => runQueued(async () => {
    const ssid = $("#wifi-ssid").value;
    const password = $("#wifi-password").value;
    if (!ssid) { toast("Selecciona una red SSID."); return; }
    try {
      toast("Conectando WiFi…");
      const res = await apiCommand(
        "wifi_connect",
        { ssid, password, persist: true },
        TIMEOUT.wifiConnect,
      );
      toast("WiFi configurado.");
      const pre = $("#wifi-result");
      pre.textContent = JSON.stringify(res, null, 2);
      show(pre);
      await refreshDashboard();
    } catch (e) {
      setBanner(formatError(e));
    }
  }));

  // --- Cámara ---
  function fillCameraSelect(cameras, savedDevice) {
    const select = $("#camera-select");
    select.innerHTML = '<option value="">— Cámara —</option>';
    cameras.forEach((c) => {
      const opt = document.createElement("option");
      opt.value = c.path;
      opt.textContent = c.name || c.path;
      if (savedDevice && c.path === savedDevice) opt.selected = true;
      select.appendChild(opt);
    });
  }

  function renderCameraMeta(entries) {
    const dl = $("#camera-meta-list");
    dl.innerHTML = entries.map(([k, v]) => `<dt>${k}</dt><dd>${v ?? NA}</dd>`).join("");
  }

  $("#btn-camera-list").addEventListener("click", () => runQueued(async () => {
    try {
      const [listRes, devRes] = await Promise.all([
        apiCommand("camera_list", {}),
        apiCommand("camera_get_device", {}),
      ]);
      const cameras = listRes.data?.cameras || [];
      const saved = devRes.data?.device_path || devRes.data?.active_device || "";
      fillCameraSelect(cameras, saved);
      renderCameraMeta([
        ["Cámaras", String(cameras.length)],
        ["Guardada", saved || NA],
      ]);
      toast(`${cameras.length} cámara(s) listada(s).`);
    } catch (e) {
      setBanner(formatError(e));
    }
  }));

  $("#btn-camera-save").addEventListener("click", () => runQueued(async () => {
    const device = $("#camera-select").value;
    if (!device) { toast("Selecciona una cámara."); return; }
    try {
      await apiCommand("camera_set_device", { device });
      toast("Cámara guardada.");
      await refreshDashboard();
    } catch (e) {
      setBanner(formatError(e));
    }
  }));

  $("#btn-camera-capture").addEventListener("click", () => runQueued(async () => {
    const device = $("#camera-select").value;
    if (!device) { toast("Selecciona una cámara."); return; }
    resetCameraPreview();
    try {
      toast("Capturando foto…");
      const res = await apiCommand(
        "camera_capture_test",
        { device, mode: "base64" },
        TIMEOUT.wifiConnect,
      );
      const data = res.data || {};
      await displayCaptureResult(data);

      const cam = (dashboard?.camera?.cameras || []).find((c) => c.path === device) || {};
      renderCameraMeta([
        ["Cámara", cam.name || NA],
        ["Dispositivo", data.device_path || device],
        ["Driver", cam.driver || NA],
        ["Resolución", data.resolution || (data.width && data.height ? `${data.width}×${data.height}` : NA)],
        ["Tamaño", data.size_bytes != null ? `${(data.size_bytes / 1024).toFixed(1)} KB` : NA],
        ["Modo", data.mode || NA],
      ]);
      toast("Foto capturada.");
    } catch (e) {
      resetCameraPreview();
      setBanner(formatError(e));
    }
  }));

  // --- CardMed ---
  $("#btn-capture-interval-save").addEventListener("click", () => runQueued(async () => {
    const raw = $("#capture-interval").value.trim();
    const interval = parseInt(raw, 10);
    if (!interval || interval < 1) {
      toast("Introduce un intervalo válido (≥ 1 s).");
      return;
    }
    try {
      await apiCommand("sampling_set_interval", { interval_seconds: interval });
      toast(`Intervalo guardado: ${interval} s.`);
      await refreshDashboard();
    } catch (e) {
      setBanner(formatError(e));
    }
  }));

  $("#btn-cardmed-get").addEventListener("click", () => runQueued(async () => {
    try {
      const res = await apiCommand("cardmed_get", {});
      $("#cardmed-preview").textContent = JSON.stringify(res.data || res, null, 2);
      toast("Config cargada.");
    } catch (e) {
      setBanner(formatError(e));
    }
  }));

  $("#btn-cardmed-save-code").addEventListener("click", () => runQueued(async () => {
    const config_code = $("#cardmed-code").value.trim();
    if (!config_code) return;
    try {
      await apiCommand("cardmed_configure", { config_code });
      toast("Configuración guardada.");
      $("#btn-cardmed-get").click();
    } catch (e) {
      setBanner(formatError(e));
    }
  }));

  $("#btn-cardmed-save-json").addEventListener("click", () => runQueued(async () => {
    const config_json = $("#cardmed-json").value.trim();
    if (!config_json) return;
    try {
      await apiCommand("cardmed_configure", { config_json });
      toast("Configuración guardada.");
      $("#btn-cardmed-get").click();
    } catch (e) {
      setBanner(formatError(e));
    }
  }));

  $("#btn-cardmed-qr").addEventListener("click", () => runQueued(async () => {
    try {
      toast("Escaneando QR con la cámara del Pi…");
      await apiCommand("cardmed_scan_qr", { apply: true }, TIMEOUT.wifiConnect);
      toast("QR aplicado.");
      $("#btn-cardmed-get").click();
    } catch (e) {
      setBanner(formatError(e));
    }
  }));

  $("#btn-cardmed-test").addEventListener("click", () => runQueued(async () => {
    try {
      toast("Ejecutando prueba CardMed…");
      const res = await apiCommand("cardmed_test", { skip_upload: true }, TIMEOUT.cardmedTest);
      const data = res.data || {};
      const steps = data.steps || [];
      const ol = $("#cardmed-steps");
      ol.innerHTML = steps.map((s) => {
        const label = s.name || s.step || JSON.stringify(s);
        const ok = s.ok !== false;
        return `<li class="${ok ? "" : "fail"}">${ok ? "✓" : "✗"} ${label}</li>`;
      }).join("");
      const pre = $("#cardmed-test-result");
      pre.textContent = JSON.stringify(data, null, 2);
      show(pre);
      toast("Prueba completada.");
    } catch (e) {
      setBanner(formatError(e));
    }
  }));

  bindPasswordToggle("#login-password", "#login-toggle-pw");
  bindPasswordToggle("#wifi-password", "#wifi-toggle-pw");

  // Init
  loadDeviceInfo();
  buildStatusCards(null);
  resetCameraPreview();
  if (token) {
    setAuthenticated(true);
  } else {
    showView("login");
  }
})();
