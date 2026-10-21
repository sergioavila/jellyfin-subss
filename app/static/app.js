const $ = (sel) => document.querySelector(sel);
const state = {
  data: null,
  reviewIndex: 0,
  hls: null,
  settingsDirty: false,
  toastTimer: null,
  library: null,
  wasRunning: false,
  cat: "",
  picked: new Set(),
};

const ACTION = {
  downloaded: ["descargado", "gold"],
  synced: ["sincronizado", "ok"],
  translated: ["traducido", "ok"],
  empty: ["sin resultados", "bad"],
  error: ["error", "bad"],
  dropped: ["tirado", "warn"],
  pending: ["en cola", ""],
  running: ["ahora", "gold"],
  ok: ["completo", "ok"],
};

const LANG_OPTIONS = [
  ["es-419", "Latino"],
  ["es-ES", "España"],
  ["en", "Inglés"],
  ["en-GB", "Reino Unido"],
  ["pt-BR", "Brasil"],
  ["pt-PT", "Portugal"],
  ["fr", "Francia"],
  ["fr-CA", "Canadá"],
  ["de", "Alemán"],
  ["it", "Italiano"],
  ["ja", "Japonés"],
  ["ko", "Coreano"],
  ["zh-Hans", "Chino simpl."],
  ["zh-Hant", "Chino trad."],
];

document.querySelectorAll("header nav button").forEach((btn) => {
  btn.addEventListener("click", () => show(btn.dataset.view));
});
document.querySelectorAll("[data-settings-tab]").forEach((btn) => {
  btn.addEventListener("click", () => showSettingsTab(btn.dataset.settingsTab));
});

$("#btn-scan").addEventListener("click", async () => {
  const ids = selectedIds();
  if (!ids.length) {
    toast("Marca una serie, temporada o título.", false);
    return;
  }
  $("#btn-scan").disabled = true;
  const res = await fetch("/api/scan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids }),
  });
  const body = await res.json().catch(() => ({}));
  toast(res.ok ? `Cola de ${ids.length}.` : body.detail || "No se pudo escanear.", res.ok);
  if (res.ok) show("queue");
  await refresh();
});
$("#btn-translate").addEventListener("click", async () => {
  const ids = selectedIds();
  if (!ids.length) {
    toast("Marca una serie, temporada o título.", false);
    return;
  }
  const source = $("#translate-from").value;
  const target = $("#translate-to").value;
  if (!source || !target) {
    toast("Elige idioma de origen y destino.", false);
    return;
  }
  if (source === target) {
    toast("Origen y destino no pueden ser el mismo.", false);
    return;
  }
  $("#btn-translate").disabled = true;
  const res = await fetch("/api/translate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids, source, target }),
  });
  const body = await res.json().catch(() => ({}));
  toast(res.ok ? `Traduciendo ${ids.length}…` : body.detail || "No se pudo traducir.", res.ok);
  if (res.ok) show("queue");
  await refresh();
});
$("#only-missing").addEventListener("change", () => {
  if (state.library) renderLibrary();
});
$("#library").addEventListener("change", (ev) => {
  const box = ev.target.closest("input[data-pick]");
  if (!box) return;
  const wrap = box.dataset.pick === "series" ? box.closest(".lib-block") : box.dataset.pick === "season" ? box.closest(".lib-line") : null;
  if (wrap) {
    wrap.querySelectorAll("input[data-pick]").forEach((el) => {
      if (el === box) return;
      el.checked = box.checked;
      el.indeterminate = false;
    });
  }
  const series = box.closest("[data-series]");
  if (series) syncGroupChecks(series);
  syncPicked();
  updateScanCount();
});
$("#lib-tabs").addEventListener("click", (ev) => {
  const btn = ev.target.closest("[data-lib-tab]");
  if (!btn) return;
  state.cat = btn.getAttribute("data-lib-tab");
  renderLibrary();
});
$("#events-table").addEventListener("click", async (ev) => {
  const btn = ev.target.closest("[data-retry]");
  if (!btn || btn.disabled) return;
  btn.disabled = true;
  const res = await fetch("/api/retry", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: btn.getAttribute("data-id"), language: btn.getAttribute("data-lang") || "" }),
  });
  const body = await res.json().catch(() => ({}));
  toast(res.ok ? "Reintento en cola." : body.detail || "No se pudo reintentar.", res.ok);
  await refresh();
});
$("#toast-dismiss").addEventListener("click", hideToast);

$("#settings-form").addEventListener("input", () => {
  state.settingsDirty = true;
});
$("#settings-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const payload = readSettings();
  if (!payload.languages.length) {
    flash("Marca al menos un idioma.", false);
    return;
  }
  const res = await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  flash(res.ok ? "Guardado." : "No se pudo guardar.", res.ok);
  if (res.ok) state.settingsDirty = false;
  await refresh();
});

$("#btn-test").addEventListener("click", async () => {
  await saveQuiet();
  const res = await fetch("/api/test/jellyfin", { method: "POST" });
  const body = await res.json();
  flashAt("jf-flash", res.ok ? `${body.ServerName} · ${body.Version}` : body.detail || "Falló", res.ok);
});
$("#provider-list").addEventListener("click", async (ev) => {
  const btn = ev.target.closest("[data-test]");
  if (!btn) return;
  await saveQuiet();
  const pid = btn.getAttribute("data-test");
  const res = await fetch(`/api/test/provider/${pid}`, { method: "POST" });
  const body = await res.json();
  const msg = res.ok ? body.user ? `${body.user} · listo` : "Conecta." : body.detail || "Falló";
  flashAt(`flash-${pid}`, msg, res.ok);
});
$("#provider-list").addEventListener("change", (ev) => {
  const box = ev.target.closest("input[data-enabled]");
  if (!box) return;
  const fields = document.getElementById(`fields-${box.getAttribute("data-enabled")}`);
  if (fields) fields.classList.toggle("hidden", !box.checked);
});

$("#btn-keep").addEventListener("click", () => decide("keep"));
$("#btn-drop").addEventListener("click", () => decide("drop"));

function readSettings() {
  const maps = $("#path_maps")
    .value.split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [from, to] = line.split("=");
      return { from: (from || "").trim(), to: (to || from || "").trim() };
    });
  const enabled = [];
  const creds = {};
  ((state.data && state.data.catalog) || []).forEach((p) => {
    const on = document.getElementById(`on-${p.id}`);
    if (on && on.checked) enabled.push(p.id);
    const row = {};
    (p.fields || []).forEach((f) => {
      const el = document.getElementById(`cred-${p.id}-${f.key}`);
      if (!el) return;
      row[f.key] = f.type === "switch" ? el.checked : el.value.trim();
    });
    if (Object.keys(row).length) creds[p.id] = row;
  });
  const langs = readLangs();
  return {
    jellyfin_url: $("#jellyfin_url").value.trim(),
    api_key: $("#api_key").value.trim(),
    languages: langs,
    language: langs[0] || "es-419",
    scan_hours: Number($("#scan_hours").value || 6),
    rate_limit_s: Number($("#rate_limit_s").value || 3),
    prefer_hash: $("#prefer_hash").checked,
    autosync: $("#autosync").checked,
    include_hi: $("#include_hi").checked,
    path_maps: maps,
    enabled_providers: enabled,
    provider_creds: creds,
    gemini_api_key: $("#gemini_api_key").value.trim(),
    tmdb_api_key: $("#tmdb_api_key").value.trim(),
  };
}

async function saveQuiet() {
  await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(readSettings()),
  });
}

function flash(text, ok) {
  flashAt("settings-flash", text, ok);
}

function flashAt(id, text, ok) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  el.className = "flash " + (ok ? "ok" : "bad");
}

function toast(text, ok) {
  const el = $("#toast");
  $("#toast-text").textContent = text;
  el.className = "toast" + (ok ? "" : " bad");
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(hideToast, 5000);
}

function hideToast() {
  $("#toast").classList.add("hidden");
  clearTimeout(state.toastTimer);
}

function show(name) {
  document.querySelectorAll("main section").forEach((s) => s.classList.add("hidden"));
  $(`#view-${name}`).classList.remove("hidden");
  document.querySelectorAll("header nav button").forEach((b) => {
    if (b.dataset.view === name) b.setAttribute("aria-current", "page");
    else b.removeAttribute("aria-current");
  });
  if (name === "home") loadLibrary();
  if (name === "review") openReview();
}

function showSettingsTab(name) {
  document.querySelectorAll("[data-settings-tab]").forEach((b) => {
    b.setAttribute("aria-selected", b.dataset.settingsTab === name ? "true" : "false");
  });
  ["idioma", "jellyfin", "traducir", "proveedores"].forEach((id) => {
    const panel = document.getElementById(`tab-${id}`);
    if (panel) panel.classList.toggle("hidden", id !== name);
  });
}

async function refresh() {
  const res = await fetch("/api/state");
  state.data = await res.json();
  renderHome();
  fillSettings();
}

function queueActive() {
  return !!(state.data && state.data.worker && state.data.worker.running);
}

function renderHome() {
  const d = state.data;
  const w = d.worker || {};
  const server = d.server || {};
  $("#app-status").textContent = server.error
    ? server.error
    : server.ServerName
      ? `${server.ServerName} · ${w.running ? w.phase : "en espera"}`
      : "Sin conexión";
  $("#btn-scan").disabled = !!w.running;
  if ($("#btn-translate")) $("#btn-translate").disabled = !!w.running;
  renderQueue();
  if (state.wasRunning && !w.running) loadLibrary(true);
  state.wasRunning = !!w.running;
}

function rowProvider(e) {
  if (e.provider) return e.provider;
  const detail = e.detail || "";
  const m = detail.match(/·\s*([^:]+):/);
  return m ? m[1].trim() : "";
}

function renderQueue() {
  const w = (state.data && state.data.worker) || {};
  const queue = w.queue || [];
  const events = (state.data && state.data.events) || [];
  const rows = queue.length ? queue : events;
  const done = queue.filter((q) => q.status && q.status !== "pending" && q.status !== "running").length;
  const lede = $("#queue-lede");
  lede.textContent = w.error
    ? w.error
    : w.running
      ? `Cola ${done}/${queue.length || 0}`
      : queue.length
        ? "Última cola. Reintenta los que fallaron."
        : "Los resultados aparecen acá, uno por uno.";
  if (w.error) lede.classList.add("bad");
  else lede.classList.remove("bad");
  const tb = $("#events-table tbody");
  if (!rows.length && w.error) {
    tb.innerHTML = `<tr><td colspan="5" class="empty">${esc(w.error)}</td></tr>`;
    return;
  }
  tb.innerHTML = rows.length
    ? rows
        .map((e) => {
          const status = e.status || e.action;
          const [label, cls] = ACTION[status] || [status, ""];
          const extra = e.hash_match ? ` <span class="badge gold">exacto</span>` : "";
          const title = e.language ? `${e.title} · ${langLabel(e.language)}` : e.title;
          const iid = e.id || e.item_id || "";
          const retry =
            iid && (status === "error" || status === "empty") && !w.running
              ? `<button class="btn btn-ghost" type="button" data-retry data-id="${esc(iid)}" data-lang="${esc(e.language || "")}">Reintentar</button>`
              : "";
          return `<tr><td>${esc(title)}</td><td><span class="badge ${cls}">${label}</span>${extra}</td><td>${esc(rowProvider(e) || "—")}</td><td>${esc(e.detail || "")}</td><td>${retry}</td></tr>`;
        })
        .join("")
    : `<tr><td colspan="5" class="empty">Todavía no hay actividad.</td></tr>`;
}

async function loadLibrary(force) {
  const box = $("#library");
  if (!force && state.library) return;
  box.innerHTML = `<p class="empty loading">Leyendo la biblioteca…</p>`;
  const res = await fetch(`/api/library${force ? "?fresh=1" : ""}`);
  const body = await res.json();
  if (!res.ok) {
    box.innerHTML = `<p class="empty">${esc(body.detail || "Configura Jellyfin en Ajustes.")}</p>`;
    return;
  }
  state.library = body;
  renderLibrary();
}

function filterGroup(g, only) {
  if (!only) return g;
  if (g.kind !== "series") return (g.missing || []).length ? g : null;
  const seasons = (g.seasons || [])
    .map((s) => {
      const episodes = (s.episodes || []).filter((ep) => (ep.missing || []).length);
      return episodes.length ? { ...s, episodes } : null;
    })
    .filter(Boolean);
  return seasons.length ? { ...g, seasons } : null;
}

function visibleCats() {
  const only = $("#only-missing") && $("#only-missing").checked;
  return (state.library || [])
    .map((cat) => ({
      name: cat.name,
      groups: (cat.groups || []).map((g) => filterGroup(g, only)).filter(Boolean),
    }))
    .filter((cat) => cat.groups.length);
}

function renderLibTabs(cats) {
  const nav = $("#lib-tabs");
  if (!cats.length) {
    nav.innerHTML = "";
    return;
  }
  if (!cats.some((c) => c.name === state.cat)) state.cat = cats[0].name;
  nav.innerHTML = cats
    .map(
      (c) =>
        `<button type="button" role="tab" data-lib-tab="${esc(c.name)}" aria-selected="${c.name === state.cat ? "true" : "false"}">${esc(c.name)}</button>`
    )
    .join("");
}

function renderLibrary() {
  const box = $("#library");
  const cats = visibleCats();
  renderLibTabs(cats);
  const cat = cats.find((c) => c.name === state.cat) || cats[0];
  if (!cat) {
    box.innerHTML = `<p class="empty">${$("#only-missing") && $("#only-missing").checked ? "Nada falta en los idiomas pedidos." : "La biblioteca está vacía."}</p>`;
    updateScanCount();
    return;
  }
  box.innerHTML = cat.groups.map(renderGroup).join("");
  restorePicked();
  updateScanCount();
}

function renderGroup(g) {
  const img = g.image ? `<img class="poster" alt="" src="${esc(g.image)}" loading="lazy">` : "<span></span>";
  const title = `${esc(g.title)}${g.year ? ` <span class="lede">(${g.year})</span>` : ""}`;
  if (g.kind === "series") {
    const seasons = (g.seasons || []).map(renderSeason).join("");
    return `<div class="lib-block" data-series="${esc(g.id)}">
      <div class="lib-line">
        <span class="lib-check"><input type="checkbox" data-pick="series"></span>
        <details class="lib">
          <summary>
            ${img}
            <span class="lib-title">${title}</span>
            <span class="lib-have">${havePills(g)}</span>
            <span class="lib-miss">${missPills(g)}</span>
          </summary>
          ${seasons}
        </details>
      </div>
    </div>`;
  }
  return `<div class="lib-row">
    <span class="lib-check"><input type="checkbox" data-pick="item" data-id="${esc(g.id)}"></span>
    ${img}
    <span class="lib-title">${title}</span>
    <span class="lib-have">${havePills(g)}</span>
    <span class="lib-miss">${missPills(g)}</span>
  </div>`;
}

function renderSeason(s) {
  const eps = (s.episodes || [])
    .map((ep) => {
      const name = `${String(ep.index).padStart(2, "0")} · ${esc(ep.title)}`;
      return `<tr>
        <td><input type="checkbox" data-pick="item" data-id="${esc(ep.id)}"></td>
        <td>${name}</td>
        <td>${havePills(ep)}</td>
        <td>${missPills(ep)}</td>
      </tr>`;
    })
    .join("");
  return `<div class="lib-line" data-season>
    <span class="lib-check"><input type="checkbox" data-pick="season"></span>
    <div class="lib-season">
      <div class="lib-season-head">
        <span class="lib-title">${esc(s.title)}</span>
        <span class="lib-have">${havePills(s)}</span>
        <span class="lib-miss">${missPills(s)}</span>
      </div>
      <table class="table lib-eps"><tbody>${eps}</tbody></table>
    </div>
  </div>`;
}

function havePills(row) {
  return (row.have || [])
    .map((code) => {
      const extra = row.total && row.counts ? ` ${row.counts[code] || 0}/${row.total}` : "";
      return `<span class="badge ok">${esc(langLabel(code))}${extra}</span>`;
    })
    .join(" ") || "—";
}

function missPills(row) {
  return (row.missing || [])
    .map((code) => {
      const extra = row.total && row.counts ? ` ${row.total - (row.counts[code] || 0)}/${row.total}` : "";
      return `<span class="badge bad">${esc(langLabel(code))}${extra}</span>`;
    })
    .join(" ") || "—";
}

function selectedIds() {
  return [...state.picked];
}

function syncPicked() {
  document.querySelectorAll('#library input[data-pick="item"]').forEach((el) => {
    const id = el.getAttribute("data-id");
    if (!id) return;
    if (el.checked) state.picked.add(id);
    else state.picked.delete(id);
  });
}

function restorePicked() {
  document.querySelectorAll('#library input[data-pick="item"]').forEach((el) => {
    el.checked = state.picked.has(el.getAttribute("data-id"));
  });
  document.querySelectorAll("[data-series]").forEach(syncGroupChecks);
}

function syncGroupChecks(root) {
  root.querySelectorAll(".lib-line").forEach((line) => {
    const box = line.querySelector('input[data-pick="season"]');
    if (!box) return;
    const items = [...line.querySelectorAll('input[data-pick="item"]')];
    const n = items.filter((el) => el.checked).length;
    box.checked = n === items.length && n > 0;
    box.indeterminate = n > 0 && n < items.length;
  });
  const seriesBox = root.querySelector('input[data-pick="series"]');
  if (!seriesBox) return;
  const items = [...root.querySelectorAll('input[data-pick="item"]')];
  const n = items.filter((el) => el.checked).length;
  seriesBox.checked = n === items.length && n > 0;
  seriesBox.indeterminate = n > 0 && n < items.length;
}

function updateScanCount() {
  const n = selectedIds().length;
  $("#scan-count").textContent = n ? `${n} marcados` : "";
}

function fillSettings() {
  if (state.settingsDirty) return;
  const s = state.data.settings || {};
  $("#jellyfin_url").value = s.jellyfin_url || "";
  if (!$("#api_key").value) $("#api_key").value = s.api_key || "";
  if (!$("#gemini_api_key").value) $("#gemini_api_key").value = s.gemini_api_key || "";
  if (!$("#tmdb_api_key").value) $("#tmdb_api_key").value = s.tmdb_api_key || "";
  renderProviders();
  fillLangs(s.languages && s.languages.length ? s.languages : [s.language || "es-419"]);
  fillTranslateSelects(s);
  $("#scan_hours").value = s.scan_hours ?? 6;
  $("#rate_limit_s").value = s.rate_limit_s ?? 3;
  $("#prefer_hash").checked = !!s.prefer_hash;
  $("#autosync").checked = !!s.autosync;
  $("#include_hi").checked = !!s.include_hi;
  $("#path_maps").value = (s.path_maps || []).map((m) => `${m.from}=${m.to}`).join("\n");
}

function fillTranslateSelects(s) {
  const from = $("#translate-from");
  const to = $("#translate-to");
  if (!from || !to) return;
  const prevFrom = from.value;
  const prevTo = to.value;
  const opts = LANG_OPTIONS.map(([code, label]) => `<option value="${code}">${esc(label)}</option>`).join("");
  from.innerHTML = opts;
  to.innerHTML = opts;
  const langs = (s && s.languages && s.languages.length ? s.languages : null) || ["es-419"];
  from.value = prevFrom || "en";
  to.value = prevTo || langs[0] || "es-419";
  if (![...from.options].some((o) => o.value === from.value)) from.value = "en";
  if (![...to.options].some((o) => o.value === to.value)) to.value = "es-419";
}

function readLangs() {
  return [...document.querySelectorAll('input[name="lang"]:checked')].map((el) => el.value);
}

function fillLangs(langs) {
  const set = new Set(langs || []);
  document.querySelectorAll('input[name="lang"]').forEach((el) => {
    el.checked = set.has(el.value);
  });
}

function langLabel(code) {
  const el = document.querySelector(`input[name="lang"][value="${code}"]`);
  return el ? el.parentNode.textContent.trim() : code;
}

function renderProviders() {
  const list = $("#provider-list");
  if (!list) return;
  const catalog = (state.data && state.data.catalog) || [];
  const s = (state.data && state.data.settings) || {};
  const enabled = new Set(s.enabled_providers || []);
  const creds = s.provider_creds || {};
  list.innerHTML = catalog
    .map((p) => {
      const on = enabled.has(p.id);
      const row = creds[p.id] || {};
      const langs = (p.langs || []).map((l) => l.toUpperCase()).join(" · ");
      const fields = (p.fields || [])
        .map((f) => {
          const id = `cred-${p.id}-${f.key}`;
          if (f.type === "switch") {
            const checked = row[f.key] === undefined ? !!f.default : !!row[f.key];
            return `<label class="check"><input type="checkbox" id="${id}" ${checked ? "checked" : ""}> ${esc(f.name)}</label>`;
          }
          const val = row[f.key] ?? "";
          return `<div><label for="${id}">${esc(f.name)}</label><input id="${id}" type="${f.type === "password" ? "password" : "text"}" value="${esc(val)}" autocomplete="off"></div>`;
        })
        .join("");
      return `<div class="prov">
        <div class="prov-head">
          <label class="check"><input type="checkbox" id="on-${p.id}" data-enabled="${p.id}" ${on ? "checked" : ""}> ${esc(p.name)}</label>
          <span class="prov-meta">${esc(langs)} · ${p.kind === "both" ? "todo" : p.kind === "movie" ? "películas" : "series"}</span>
          <button class="btn btn-ghost" type="button" data-test="${p.id}">Probar</button>
        </div>
        <p class="prov-hint">${esc(p.hint || "")}</p>
        <div class="prov-fields ${on ? "" : "hidden"}" id="fields-${p.id}">${fields}</div>
        <div class="flash" id="flash-${p.id}"></div>
      </div>`;
    })
    .join("");
}

async function openReview() {
  const queue = state.data.review || [];
  if (!queue.length) {
    $("#review-empty").classList.remove("hidden");
    $("#review-box").classList.add("hidden");
    stopPlayer();
    return;
  }
  $("#review-empty").classList.add("hidden");
  $("#review-box").classList.remove("hidden");
  state.reviewIndex = Math.min(state.reviewIndex, queue.length - 1);
  const item = queue[state.reviewIndex];
  $("#review-title").textContent = item.language ? `${item.title} · ${langLabel(item.language)}` : item.title;
  $("#review-queue-label").textContent = `${state.reviewIndex + 1} de ${queue.length}`;
  const langQ = item.language ? `?lang=${encodeURIComponent(item.language)}` : "";
  const play = await (await fetch(`/api/play/${item.item_id}${langQ}`)).json();
  $("#review-help").textContent = "Arranca un poco entrada, con el subtítulo que se acaba de bajar.";
  startPlayer(play);
}

function startPlayer(play) {
  stopPlayer();
  const video = $("#player");
  const hasHls = typeof play.hls === "string" && play.hls.startsWith("/");
  if (!hasHls) {
    video.classList.add("hidden");
    return;
  }
  video.classList.remove("hidden");
  if (window.Hls && Hls.isSupported()) {
    state.hls = new Hls();
    state.hls.loadSource(play.hls);
    state.hls.attachMedia(video);
  } else {
    video.src = play.hls;
  }
  if (play.vtt) {
    const track = document.createElement("track");
    track.kind = "subtitles";
    track.label = "Subss";
    track.srclang = play.lang || "es";
    track.src = play.vtt;
    track.default = true;
    video.appendChild(track);
  }
  video.currentTime = play.start_s || 0;
  video.play().catch(() => {});
}

function stopPlayer() {
  if (state.hls) {
    state.hls.destroy();
    state.hls = null;
  }
  const video = $("#player");
  video.pause();
  video.removeAttribute("src");
  video.querySelectorAll("track").forEach((t) => t.remove());
  video.load();
}

async function decide(kind) {
  const queue = state.data.review || [];
  const item = queue[state.reviewIndex];
  if (!item) return;
  const langQ = item.language ? `?lang=${encodeURIComponent(item.language)}` : "";
  await fetch(`/api/review/${item.item_id}/${kind}${langQ}`, { method: "POST" });
  await refresh();
  openReview();
}

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll('"', "&quot;");
}

function tick() {
  const running = state.data && state.data.worker && state.data.worker.running;
  setTimeout(() => refresh().then(tick), running ? 1500 : 8000);
}
refresh().then(() => {
  if (location.hash === "#review") show("review");
  else loadLibrary();
  tick();
});
