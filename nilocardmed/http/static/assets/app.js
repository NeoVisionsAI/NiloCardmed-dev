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
    wifiScan: 60000,
    wifiConnect: 90000,
    cardmedTest: 90000,
    scanMinWait: 4000,
  };

  let token = sessionStorage.getItem(TOKEN_KEY) || "";
  let dashboard = null;
  let commandBusy = false;
  let toastTimer = null;

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => document.querySelectorAll(sel);

  function show(el) { el.hidden = false; }
  function hide(el) { el.hidden = true; }

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

  async function runQueued(fn) {
    if (commandBusy) {
      toast("Espera a que termine la operación anterior…");
      return null;
    }
    commandBusy = true;
    try {
      return await fn();
    } finally {
      commandBusy = false;
    }
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
    else if (src === "usb") { source = "USB / alimentación externa"; level = "100 %"; }
    else if (src === "powerbank") {
      source = "Powerbank / batería";
      level = power.display_percent != null ? `${power.display_percent} %` : NA;
    } else {
      level = power.display_percent != null ? `${power.display_percent} %` : NA;
    }
    return { source, level };
  }

  function buildStatusCards(data) {
    const wifi = data?.wifi || {};
    const power = formatPower(data?.power);
    const sampling = data?.sampling || {};
    const camera = data?.camera || {};
    const captures = data?.captures || {};

    const wifiConnected = wifi.connected === true;
    const cards = [
      {
        title: "WiFi",
        rows: [
          ["Activo", wifiConnected ? "Sí" : "No"],
          ["SSID", wifiConnected ? (wifi.ssid || NA) : NA],
          ["IP", wifiConnected ? (wifi.ip_address || NA) : NA],
          ["Señal", wifiConnected && wifi.signal != null ? `${wifi.signal} dBm` : NA],
          ["Internet", wifiConnected ? (wifi.connectivity_ok ? "Sí" : "No") : NA],
        ],
      },
      {
        title: "Alimentación",
        rows: [["Fuente", power.source], ["Nivel", power.level]],
      },
      {
        title: "Muestreo",
        rows: [
          ["Intervalo", sampling.interval_seconds != null ? `${sampling.interval_seconds} s` : NA],
          ["Estado", sampling.window_active ? "Activo" : "Inactivo"],
        ],
      },
      {
        title: "Cámara",
        rows: [
          ["Conectada", camera.connected ? "Sí" : "No"],
          ["Detectadas", camera.cameras_count != null ? String(camera.cameras_count) : NA],
          ["Guardada en Pi", camera.saved_device_present ? "Sí" : "No"],
          ["Dispositivo", camera.saved_device || NA],
        ],
      },
      {
        title: "Última config",
        rows: [["Guardada", formatIsoDate(data?.config_last_saved_at)]],
      },
      {
        title: "Capturas",
        rows: [
          ["Ciclos OK", captures.cycles_successful != null ? String(captures.cycles_successful) : NA],
          ["Imágenes en disco", captures.images_on_disk != null ? String(captures.images_on_disk) : NA],
        ],
      },
    ];

    const grid = $("#status-cards");
    grid.innerHTML = cards.map((c) => `
      <article class="status-card">
        <h3 class="status-card__title">${c.title}</h3>
        <dl>${c.rows.map(([k, v]) => `<dt>${k}</dt><dd>${v ?? NA}</dd>`).join("")}</dl>
      </article>
    `).join("");
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
        $("#header-subtitle").textContent = ver ? `${name} · v${ver}` : name;
        $("#login-subtitle").textContent = ver ? `${name} · v${ver}` : name;
      }
    } catch { /* ignore */ }
  }

  function showView(name) {
    hide($("#view-login"));
    hide($("#view-main"));
    hide($("#view-done"));
    if (name === "login") show($("#view-login"));
    if (name === "main") show($("#view-main"));
    if (name === "done") show($("#view-done"));
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
    if (String(mode).includes("iw")) return "Escaneo ampliado (rescan + iw)";
    return `Modo: ${mode}`;
  }

  async function withScanMinWait(promise) {
    const [result] = await Promise.all([
      promise,
      new Promise((r) => setTimeout(r, TIMEOUT.scanMinWait)),
    ]);
    return result;
  }

  // --- Login ---
  $("#form-login").addEventListener("submit", async (e) => {
    e.preventDefault();
    hide($("#login-error"));
    const password = $("#login-password").value;
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

  // --- Tabs ---
  $$(".tab").forEach((tab) => {
    tab.addEventListener("click", () => switchTab(tab.dataset.tab));
  });

  // --- Estado ---
  $("#btn-refresh-dashboard").addEventListener("click", () => refreshDashboard());

  // --- WiFi ---
  $("#btn-wifi-scan").addEventListener("click", () => runQueued(async () => {
    const hint = $("#wifi-scan-hint");
    const meta = $("#wifi-scan-meta");
    show(hint);
    meta.textContent = "Escaneando…";
    hide($("#wifi-result"));
    try {
      toast("Escaneando WiFi (rescan en el Pi, ~3–5 s mínimo)…");
      const res = await withScanMinWait(
        apiCommand("wifi_scan", { rescan: true }, TIMEOUT.wifiScan),
      );
      const networks = res.data?.networks || [];
      const select = $("#wifi-ssid");
      select.innerHTML = '<option value="">— Selecciona red —</option>';
      networks.forEach((n) => {
        const opt = document.createElement("option");
        opt.value = n.ssid;
        const sig = n.signal != null ? ` (${n.signal} dBm)` : "";
        opt.textContent = `${n.ssid}${sig}`;
        select.appendChild(opt);
      });
      meta.textContent = networks.length
        ? `${networks.length} red(es) encontrada(s). ${formatScanMode(res.data?.scan_mode)}`
        : "No se encontraron redes.";
      toast(`${networks.length} red(es) encontrada(s).`);
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
        ["Cámaras detectadas", String(cameras.length)],
        ["Guardada en Pi", saved || NA],
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
      toast("Cámara guardada en el Pi.");
      await refreshDashboard();
    } catch (e) {
      setBanner(formatError(e));
    }
  }));

  $("#btn-camera-capture").addEventListener("click", () => runQueued(async () => {
    const device = $("#camera-select").value;
    if (!device) { toast("Selecciona una cámara."); return; }
    try {
      toast("Capturando foto…");
      const res = await apiCommand(
        "camera_capture_test",
        { device, mode: "base64" },
        TIMEOUT.wifiConnect,
      );
      const data = res.data || {};
      const img = $("#camera-image");
      const ph = $("#camera-placeholder");
      if (data.mode === "base64" && data.image_base64) {
        img.src = `data:image/jpeg;base64,${data.image_base64}`;
        show(img);
        hide(ph);
      } else if (data.mode === "chunked") {
        toast("Imagen grande — modo chunked; implementa chunks si hace falta.");
      }
      const cam = (dashboard?.camera?.cameras || []).find((c) => c.path === device) || {};
      renderCameraMeta([
        ["Cámara", cam.name || NA],
        ["Dispositivo", data.device_path || device],
        ["Driver", cam.driver || NA],
        ["Bus", cam.bus_info || NA],
        ["Resolución", data.resolution || (data.width && data.height ? `${data.width}×${data.height}` : NA)],
        ["Tamaño", data.size_bytes != null ? `${data.size_bytes} B` : NA],
        ["Backend", data.backend || NA],
        ["Modo", data.mode || NA],
      ]);
      toast("Foto de prueba capturada.");
    } catch (e) {
      setBanner(formatError(e));
    }
  }));

  // --- CardMed ---
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
      toast("Prueba CardMed completada.");
    } catch (e) {
      setBanner(formatError(e));
    }
  }));

  // --- Header / done ---
  $("#btn-finish").addEventListener("click", () => showView("done"));
  $("#btn-continue").addEventListener("click", () => showView("main"));
  $("#btn-close").addEventListener("click", () => window.close());
  $("#btn-close-done").addEventListener("click", () => window.close());

  bindPasswordToggle("#login-password", "#login-toggle-pw");
  bindPasswordToggle("#wifi-password", "#wifi-toggle-pw");

  // Init
  loadDeviceInfo();
  buildStatusCards(null);
  if (token) {
    setAuthenticated(true);
  } else {
    showView("login");
  }
})();
