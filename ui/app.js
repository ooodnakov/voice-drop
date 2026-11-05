/* VoiceDrop UI — vanilla JS, CSP: script-src 'self'. No imports, no bundler. */

const { invoke } = window.__TAURI__.core;
const tauriEvent = window.__TAURI__.event;
const tauriWebview = window.__TAURI__.webview;
const tauriWindow = window.__TAURI__.window;

const AUDIO_EXT = new Set([
  "mp3", "m4a", "m4b", "wav", "flac", "aac", "ogg", "oga", "opus", "wma", "aiff", "aif", "amr", "caf",
]);

const VIDEO_EXT = new Set([
  "mp4", "m4v", "mov", "mkv", "webm", "avi", "flv", "wmv", "3gp", "mpeg", "mpg", "ts",
]);

const MAX_ROWS = 100;
const DEDUPE_MS = 500;

const el = (id) => document.getElementById(id);

const bannerEl = el("banner");
const accountSelect = el("account");
const addAccountBtn = el("addAccount");
const removeAccountBtn = el("removeAccount");
const chatSelect = el("chat");
const refreshBtn = el("refresh");
const zoneEl = el("zone");
const pickBtn = el("pick");
const winMinBtn = el("winMin");
const winCloseBtn = el("winClose");
const statusEl = el("status");

const loginEl = el("login");
const loginErrorEl = el("loginError");
const phoneInput = el("phone");
const codeInput = el("code");
const passwordInput = el("password");
const sendCodeBtn = el("sendCode");
const signInBtn = el("signIn");
const confirmPasswordBtn = el("confirmPassword");
const loginCancelBtn = el("loginCancel");

const settingsEl = el("settings");
const settingsToggleBtn = el("settingsToggle");
const settingsCloseBtn = el("settingsClose");
const apiIdInput = el("apiId");
const apiHashInput = el("apiHash");
const saveApiBtn = el("saveApi");
const runtimeInfoEl = el("runtimeInfo");
const checkBtn = el("check");
const checksEl = el("checks");
const themesEl = el("themes");

const THEMES = [
  { id: "system", label: "System" },
  { id: "gruvbox-dark", label: "Gruvbox dark" },
  { id: "gruvbox-light", label: "Gruvbox light" },
  { id: "paper", label: "Paper" },
  { id: "slate", label: "Slate" },
];

const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");

let themePreference = "gruvbox-dark";
let runtime = {};
let appState = { accounts: [], chat: "", account: "", api_id: 0, api_hash: "" };

let loginPhone = "";
let phoneHash = "";
let queue = [];
let pumping = false;
let lastDrop = { key: "", at: 0 };

/* ---------- small helpers ---------- */

function basename(path) {
  return String(path).split(/[\\/]/).pop() || String(path);
}

function looksVideo(path) {
  const ext = basename(path).split(".").pop().toLowerCase();
  return VIDEO_EXT.has(ext);
}

function looksAudio(path) {
  const ext = basename(path).split(".").pop().toLowerCase();
  return AUDIO_EXT.has(ext);
}

function errorText(error) {
  if (!error) return "unknown error";
  if (typeof error === "string") return error;
  return String(error.message || error);
}

function setBanner(text, isError) {
  if (!text) {
    bannerEl.textContent = "";
    bannerEl.hidden = true;
    bannerEl.classList.remove("error");
    return;
  }
  bannerEl.textContent = text;
  bannerEl.hidden = false;
  bannerEl.classList.toggle("error", Boolean(isError));
}

/* ---------- status rows ---------- */

function addRow(text, tag, cls) {
  const row = document.createElement("div");
  row.className = `row ${cls || "pending"}`;

  const name = document.createElement("div");
  name.className = "name";
  name.textContent = text;
  name.title = text;

  const label = document.createElement("div");
  label.className = "tag";
  label.textContent = tag;

  row.append(name, label);
  statusEl.append(row);

  while (statusEl.children.length > MAX_ROWS) {
    statusEl.firstElementChild.remove();
  }

  if (typeof row.scrollIntoView === "function") row.scrollIntoView({ block: "nearest" });
  return { row, tag: label };
}

function setRow(item, cls, text, title) {
  item.row.className = `row ${cls}`;
  item.tag.textContent = text;
  item.tag.title = title || text;
}

/* ---------- accounts ---------- */

function accounts() {
  return Array.isArray(appState.accounts) ? appState.accounts : [];
}

function renderAccounts() {
  const list = accounts();
  accountSelect.textContent = "";

  if (!list.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "— not logged in —";
    option.disabled = true;
    option.selected = true;
    accountSelect.append(option);
  } else {
    for (const account of list) {
      const option = document.createElement("option");
      option.value = account.key;
      option.textContent = account.label || account.key;
      accountSelect.append(option);
    }
    const wanted = appState.account && list.some((a) => a.key === appState.account)
      ? appState.account
      : list[0].key;
    accountSelect.value = wanted;
  }

  removeAccountBtn.disabled = list.length === 0;
}

/* ---------- recipients ---------- */

function optionLabel(target) {
  const kind = target.kind && target.kind !== "user" ? ` [${target.kind}]` : "";
  return `${target.name || target.value} · ${target.value}${kind}`;
}

function renderPlaceholder(text) {
  chatSelect.textContent = "";
  const option = document.createElement("option");
  option.value = "";
  option.textContent = text || "— no recipients —";
  option.disabled = true;
  option.selected = true;
  chatSelect.append(option);
}

function renderTargets(targets, selected) {
  chatSelect.textContent = "";
  const list = targets.slice();

  if (selected && !list.some((target) => target.value === selected)) {
    list.unshift({ value: selected, name: selected, kind: "", saved: true });
  }

  if (!list.length) {
    renderPlaceholder();
    return;
  }

  for (const target of list) {
    const option = document.createElement("option");
    option.value = target.value;
    option.textContent = target.saved ? `${target.value} (saved)` : optionLabel(target);
    chatSelect.append(option);
  }

  if (selected) chatSelect.value = selected;
}

async function loadTargets() {
  const selected = appState.chat || "";
  refreshBtn.disabled = true;
  try {
    const result = await invoke("list_targets");
    if (!result || result.ok === false || !Array.isArray(result.targets)) {
      throw new Error((result && result.error) || "could not load recipients");
    }
    renderTargets(result.targets, selected);
    setBanner("", false);
  } catch (error) {
    renderPlaceholder();
    addRow("recipients", errorText(error), "error");
  } finally {
    refreshBtn.disabled = false;
  }
}

/* ---------- sending (serial batches) ---------- */

function applyResult(item, result) {
  if (result.error) {
    setRow(item, "error", String(result.error));
    return;
  }
  const kind = result.circle ? "circle" : result.voice ? "voice" : "document";
  const id = result.id === undefined || result.id === null ? "" : ` · #${result.id}`;
  setRow(item, "ok", `${kind}${id}`);
}

async function sendBatch(batch) {
  for (const item of batch.rows) {
    const encoding = looksAudio(item.path) || looksVideo(item.path);
    setRow(item, "pending", encoding ? "encoding…" : "uploading…");
  }

  let result;
  try {
    result = await invoke("send_files", { paths: batch.paths });
  } catch (error) {
    const message = errorText(error);
    for (const item of batch.rows) setRow(item, "error", message);
    return;
  }

  const results = result && Array.isArray(result.results) ? result.results : null;
  if (!results) {
    const message = (result && result.error) || "no results";
    for (const item of batch.rows) setRow(item, "error", message);
    return;
  }

  const pending = new Map();
  for (const entry of results) {
    const key = basename(entry && entry.file ? entry.file : "");
    if (!pending.has(key)) pending.set(key, []);
    pending.get(key).push(entry);
  }

  for (const item of batch.rows) {
    const pool = pending.get(item.base);
    const entry = pool && pool.length ? pool.shift() : null;
    if (!entry) {
      setRow(item, "error", (result && result.error) || "no result");
      continue;
    }
    applyResult(item, entry);
  }

  for (const pool of pending.values()) {
    for (const entry of pool) {
      const item = addRow(basename(entry && entry.file ? entry.file : "?"), "", "pending");
      applyResult(item, entry);
    }
  }
}

async function pump() {
  if (pumping) return;
  pumping = true;
  try {
    while (queue.length) {
      await sendBatch(queue.shift());
    }
  } finally {
    pumping = false;
  }
}

function enqueue(paths) {
  const batch = { paths: [], rows: [] };
  for (const path of paths) {
    const base = basename(path);
    const item = addRow(base, "queued", "pending");
    batch.paths.push(path);
    batch.rows.push({ ...item, path, base });
  }
  if (!batch.paths.length) return;
  queue.push(batch);
  pump();
}

function handleDrop(paths) {
  const clean = (paths || []).filter((path) => typeof path === "string" && path.length > 0);
  if (!clean.length) return;

  const key = clean.join("\n");
  const now = Date.now();
  if (key === lastDrop.key && now - lastDrop.at < DEDUPE_MS) return;
  lastDrop = { key, at: now };

  enqueue(clean);
}

/* ---------- login overlay ---------- */

function showStep(step) {
  for (const section of loginEl.querySelectorAll(".step")) {
    section.hidden = Number(section.dataset.step) !== step;
  }
}

function setLoginError(text) {
  loginErrorEl.textContent = text || "";
}

function openLogin() {
  setLoginError("");
  showStep(1);
  loginEl.hidden = false;
  if (phoneInput) phoneInput.focus();
}

function closeLogin() {
  loginEl.hidden = true;
  setLoginError("");
}

async function attempt(button, task) {
  setLoginError("");
  button.disabled = true;
  try {
    await task();
  } catch (error) {
    setLoginError(errorText(error));
  } finally {
    button.disabled = false;
  }
}

async function sendCode() {
  await attempt(sendCodeBtn, async () => {
    const phone = phoneInput.value.trim();
    if (!phone) {
      setLoginError("enter a phone number");
      return;
    }
    const result = await invoke("login_start", { phone });
    if (result && result.ok) {
      loginPhone = phone;
      phoneHash = result.phone_hash || "";
      showStep(2);
      codeInput.focus();
      return;
    }
    setLoginError((result && result.error) || "could not send code");
  });
}

async function applyLoginResult(result) {
  appState = result.state || appState;
  closeLogin();
  renderAccounts();
  renderSettingsFields();
  if (accounts().length) await loadTargets();
}

async function signIn() {
  await attempt(signInBtn, async () => {
    const code = codeInput.value.trim();
    if (!code) {
      setLoginError("enter the code");
      return;
    }
    const result = await invoke("login_code", { phone: loginPhone, code, phoneHash });
    if (result && result.ok) {
      await applyLoginResult(result);
      return;
    }
    if (result && result.needs_password) {
      showStep(3);
      passwordInput.focus();
      return;
    }
    setLoginError((result && result.error) || "sign in failed");
  });
}

async function confirmPassword() {
  await attempt(confirmPasswordBtn, async () => {
    const password = passwordInput.value;
    if (!password) {
      setLoginError("enter the password");
      return;
    }
    const result = await invoke("login_password", { phone: loginPhone, password });
    if (result && result.ok) {
      await applyLoginResult(result);
      return;
    }
    setLoginError((result && result.error) || "password rejected");
  });
}

/* ---------- settings overlay ---------- */

function renderRuntimeInfo() {
  const lines = [];
  if (runtime && runtime.python) lines.push(`python: ${runtime.python}`);
  if (runtime && runtime.ffmpeg) lines.push(`ffmpeg: ${runtime.ffmpeg}`);
  if (runtime && runtime.error) lines.push(`error: ${runtime.error}`);
  if (!lines.length) lines.push("runtime not initialized");
  runtimeInfoEl.textContent = lines.join("\n");
}

function resolvedTheme(preference) {
  if (preference === "system") {
    return darkQuery.matches ? "gruvbox-dark" : "gruvbox-light";
  }
  return THEMES.some((theme) => theme.id === preference) ? preference : "gruvbox-dark";
}

function applyTheme(preference) {
  themePreference = preference;
  document.documentElement.dataset.theme = resolvedTheme(preference);
  if (!themesEl) return;
  for (const button of themesEl.children) {
    if (button.tagName === "BUTTON") {
      button.setAttribute("aria-pressed", String(button.dataset.theme === preference));
    }
  }
}

function renderThemes() {
  if (!themesEl) return;
  if (!themesEl.children.length) {
    for (const theme of THEMES) {
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.theme = theme.id;
      button.textContent = theme.label;
      button.setAttribute("role", "radio");
      themesEl.append(button);
    }
  }
  applyTheme(themePreference);
}

function renderCheck(label, result) {
  const row = document.createElement("div");
  const ok = Boolean(result && result.ok);
  row.className = `check-row ${ok ? "check-ok" : "check-err"}`;
  const nameEl = document.createElement("span");
  nameEl.className = "check-name";
  nameEl.textContent = label;
  const stateEl = document.createElement("span");
  stateEl.className = "check-state";
  stateEl.textContent = ok ? "ok" : "fail";
  const detailEl = document.createElement("span");
  detailEl.className = "check-detail";
  detailEl.textContent = (result && result.detail) || "unknown";
  row.append(nameEl, stateEl, detailEl);
  if (checksEl) checksEl.append(row);
}

async function runChecks() {
  if (!checkBtn || !checksEl) return;
  checkBtn.disabled = true;
  checksEl.textContent = "";
  const pending = document.createElement("div");
  pending.className = "check-row";
  pending.textContent = "checking…";
  checksEl.append(pending);
  try {
    const result = await invoke("diagnostics");
    checksEl.textContent = "";
    renderCheck("python", result.python);
    renderCheck("telegram", result.telegram);
    renderCheck("ffmpeg", result.ffmpeg);
  } catch (error) {
    checksEl.textContent = "";
    renderCheck("checks", { ok: false, detail: String(error) });
  } finally {
    checkBtn.disabled = false;
  }
}

function renderSettingsFields() {
  apiIdInput.value = appState.api_id ? String(appState.api_id) : "";
  apiHashInput.value = appState.api_hash || "";
  if (appState && appState.theme) applyTheme(appState.theme);
  renderThemes();
}

function openSettings() {
  renderSettingsFields();
  renderRuntimeInfo();
  settingsEl.hidden = false;
  if (checksEl && !checksEl.children.length) runChecks();
  else apiIdInput.focus();
}

async function saveApi() {
  const apiId = Number(apiIdInput.value.trim());
  const apiHash = apiHashInput.value.trim();
  if (!Number.isFinite(apiId) || apiId <= 0 || !apiHash) {
    setBanner("enter api_id and api_hash", true);
    return;
  }
  saveApiBtn.disabled = true;
  try {
    appState = await invoke("set_api_credentials", { apiId, apiHash });
    renderSettingsFields();
    setBanner("", false);
  } catch (error) {
    setBanner(errorText(error), true);
  } finally {
    saveApiBtn.disabled = false;
  }
}

/* ---------- custom listbox (formatted dropdown) ---------- */

let selectSeq = 0;

function parseOptionText(text) {
  const trimmed = String(text || "").trim();
  const kindMatch = trimmed.match(/\s\[([a-z]+)\]$/i);
  const kind = kindMatch ? kindMatch[1].toLowerCase() : "";
  const base = kindMatch ? trimmed.slice(0, kindMatch.index) : trimmed;
  const [name, ...rest] = base.split(" \u00b7 ");
  return { name: name || base, meta: rest.join(" \u00b7 "), kind };
}

function enhanceSelect(select) {
  const wrap = select.parentElement;
  if (!wrap || wrap.querySelector(".select-btn")) return null;

  const button = document.createElement("button");
  button.type = "button";
  button.className = "select-btn";
  button.setAttribute("role", "combobox");
  button.setAttribute("aria-haspopup", "listbox");
  button.setAttribute("aria-expanded", "false");

  const labelEl = document.createElement("span");
  labelEl.className = "select-label";
  const caret = document.createElement("span");
  caret.className = "select-caret";
  button.append(labelEl, caret);

  const menu = document.createElement("div");
  menu.className = "select-menu";
  menu.hidden = true;
  menu.setAttribute("role", "listbox");
  menu.id = `listbox-${++selectSeq}`;
  button.setAttribute("aria-controls", menu.id);

  const search = document.createElement("input");
  search.className = "select-search";
  search.type = "text";
  search.placeholder = "search…";
  search.spellcheck = false;
  search.autocomplete = "off";
  search.setAttribute("aria-label", "Search options");
  search.hidden = true;

  const list = document.createElement("div");
  list.className = "select-list";
  menu.append(search, list);

  select.classList.add("select-native");
  select.tabIndex = -1;
  select.setAttribute("aria-hidden", "true");

  wrap.append(button);
  document.body.append(menu);

  let activeIndex = -1;

  const entries = () => [...list.querySelectorAll(".select-option")];
  const visibleEntries = () => entries().filter((item) => !item.hidden);
  const activeItem = () => visibleEntries()[activeIndex];

  function syncLabel() {
    const option = select.selectedOptions[0];
    labelEl.textContent = option ? option.textContent.trim() : "\u2014";
    const usable = [...select.options].some((item) => !item.disabled && item.value !== "");
    button.disabled = !usable;
  }

  function emptyNote(text) {
    const note = document.createElement("div");
    note.className = "select-empty";
    note.textContent = text;
    return note;
  }

  function choose(option) {
    select.value = option.value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
    syncLabel();
    closeMenu(true);
  }

  function rebuild() {
    list.textContent = "";
    search.value = "";
    const options = [...select.options].filter((item) => !item.disabled && item.value !== "");
    if (!options.length) {
      list.append(emptyNote("no options"));
      search.hidden = true;
      return;
    }
    options.forEach((option, index) => {
      const item = document.createElement("div");
      item.className = "select-option";
      item.id = `${menu.id}-opt-${index}`;
      item.dataset.value = option.value;
      item.setAttribute("role", "option");
      if (option.selected) {
        item.classList.add("is-selected");
        item.setAttribute("aria-selected", "true");
      }
      const { name, meta, kind } = parseOptionText(option.textContent);
      const nameEl = document.createElement("span");
      nameEl.className = "opt-name";
      nameEl.textContent = name;
      item.append(nameEl);
      const metaText = [meta, kind ? `[${kind}]` : ""].filter(Boolean).join(" ");
      if (metaText) {
        const metaEl = document.createElement("span");
        metaEl.className = "opt-meta";
        metaEl.textContent = metaText;
        item.append(metaEl);
      }
      item.addEventListener("mousedown", (event) => {
        event.preventDefault();
        choose(option);
      });
      list.append(item);
    });
    search.hidden = options.length < 8;
    activeIndex = -1;
  }

  function clearActive() {
    activeIndex = -1;
    entries().forEach((item) => item.classList.remove("is-active"));
    menu.removeAttribute("aria-activedescendant");
  }

  function setActive(index) {
    const items = visibleEntries();
    entries().forEach((item) => item.classList.remove("is-active"));
    if (!items.length) {
      clearActive();
      return;
    }
    activeIndex = ((index % items.length) + items.length) % items.length;
    const current = items[activeIndex];
    current.classList.add("is-active");
    menu.setAttribute("aria-activedescendant", current.id);
    current.scrollIntoView({ block: "nearest" });
  }

  function applyFilter(term) {
    const query = term.trim().toLowerCase();
    entries().forEach((item) => {
      const match = !query || item.textContent.toLowerCase().includes(query);
      item.hidden = !match;
    });
    let note = list.querySelector(".select-empty");
    if (!visibleEntries().length) {
      if (!note) list.append(emptyNote("no matches"));
      clearActive();
      return;
    }
    if (note) note.remove();
    setActive(0);
  }

  function onOutside(event) {
    if (!wrap.contains(event.target) && !menu.contains(event.target)) closeMenu(false);
  }

  function positionMenu() {
    const rect = button.getBoundingClientRect();
    const width = Math.max(rect.width, 180);
    const spaceBelow = window.innerHeight - rect.bottom - 8;
    const spaceAbove = rect.top - 8;
    const available = Math.max(spaceBelow, spaceAbove, 120);
    menu.style.width = `${width}px`;
    menu.style.maxHeight = `${Math.min(320, available)}px`;
    const height = Math.min(menu.scrollHeight, Math.min(320, available));
    let top = rect.bottom + 3;
    if (spaceBelow < height && spaceAbove > spaceBelow) top = rect.top - height - 3;
    menu.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - width - 8))}px`;
    menu.style.top = `${Math.max(8, top)}px`;
  }

  function openMenu(seed) {
    if (button.disabled) return;
    rebuild();
    menu.hidden = false;
    button.setAttribute("aria-expanded", "true");
    positionMenu();
    const selected = entries().findIndex((item) => item.classList.contains("is-selected"));
    if (selected >= 0) setActive(selected);
    if (typeof seed === "string" && seed) {
      search.value = seed;
      applyFilter(seed);
    }
    if (!search.hidden) {
      search.focus();
      if (seed) search.setSelectionRange(seed.length, seed.length);
    }
    document.addEventListener("pointerdown", onOutside, true);
    window.addEventListener("resize", positionMenu);
    window.addEventListener("scroll", positionMenu, true);
  }

  function closeMenu(focusButton) {
    menu.hidden = true;
    button.setAttribute("aria-expanded", "false");
    menu.removeAttribute("aria-activedescendant");
    document.removeEventListener("pointerdown", onOutside, true);
    window.removeEventListener("resize", positionMenu);
    window.removeEventListener("scroll", positionMenu, true);
    if (focusButton) button.focus();
  }

  button.addEventListener("click", () => (menu.hidden ? openMenu() : closeMenu(false)));
  button.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (menu.hidden) openMenu();
      else setActive(activeIndex + (event.key === "ArrowDown" ? 1 : -1));
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (menu.hidden) openMenu();
      else {
        const item = activeItem();
        if (item) item.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
      }
    } else if (event.key === "Escape" && !menu.hidden) {
      event.preventDefault();
      closeMenu(true);
    } else if (event.key === "Tab" && !menu.hidden) {
      closeMenu(false);
    } else if (event.key === "Home" && !menu.hidden) {
      event.preventDefault();
      setActive(0);
    } else if (event.key === "End" && !menu.hidden) {
      event.preventDefault();
      setActive(visibleEntries().length - 1);
    } else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault();
      openMenu(event.key);
    }
  });

  search.addEventListener("input", () => applyFilter(search.value));
  search.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive(activeIndex + 1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive(activeIndex - 1);
    } else if (event.key === "Enter") {
      event.preventDefault();
      const item = activeItem();
      if (item) item.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    } else if (event.key === "Escape") {
      event.preventDefault();
      closeMenu(true);
    } else if (event.key === "Tab") {
      closeMenu(false);
    }
  });

  select.addEventListener("change", syncLabel);
  new MutationObserver(syncLabel).observe(select, {
    childList: true,
    subtree: true,
    characterData: true,
  });
  syncLabel();
  return { syncLabel };
}

/* ---------- drag & drop ---------- */

function bindHotState() {
  const hot = (on) => zoneEl.classList.toggle("hot", on);
  try {
    if (tauriWebview && typeof tauriWebview.getCurrentWebview === "function") {
      return Promise.resolve(
        tauriWebview.getCurrentWebview().onDragDropEvent((event) => {
          const payload = (event && event.payload) || {};
          if (payload.type === "enter" || payload.type === "over") {
            hot(true);
          } else if (payload.type === "leave") {
            hot(false);
          } else if (payload.type === "drop") {
            hot(false);
            handleDrop(payload.paths || []);
          }
        }),
      ).catch(() => rawDragEvents(hot));
    }
  } catch (error) {
    return rawDragEvents(hot);
  }
  return rawDragEvents(hot);
}

function rawDragEvents(hot) {
  const target = { kind: "Webview", label: "main" };
  return Promise.all([
    tauriEvent.listen("tauri://drag-over", () => hot(true), { target }),
    tauriEvent.listen("tauri://drag-leave", () => hot(false), { target }),
    tauriEvent.listen(
      "tauri://drag-drop",
      (event) => {
        hot(false);
        handleDrop((event && event.payload && event.payload.paths) || []);
      },
      { target },
    ),
  ]).catch((error) => {
    addRow("drag & drop", `unavailable: ${errorText(error)}`, "error");
  });
}

/* ---------- wiring ---------- */

function wire() {
  accountSelect.addEventListener("change", async () => {
    const key = accountSelect.value;
    if (!key) return;
    try {
      appState = await invoke("set_account", { key });
      renderAccounts();
      await loadTargets();
    } catch (error) {
      addRow("account", errorText(error), "error");
    }
  });

  addAccountBtn.addEventListener("click", () => {
    openLogin();
  });

  removeAccountBtn.addEventListener("click", async () => {
    const key = accountSelect.value;
    if (!key) return;
    removeAccountBtn.disabled = true;
    try {
      appState = await invoke("remove_account", { key });
      renderAccounts();
      if (accounts().length) {
        await loadTargets();
      } else {
        renderPlaceholder();
        openLogin();
      }
    } catch (error) {
      addRow("account", errorText(error), "error");
      removeAccountBtn.disabled = accounts().length === 0;
    }
  });

  chatSelect.addEventListener("change", async () => {
    const chat = chatSelect.value;
    if (!chat) return;
    try {
      appState = await invoke("set_chat", { chat });
    } catch (error) {
      addRow("recipient", errorText(error), "error");
    }
  });

  refreshBtn.addEventListener("click", () => {
    loadTargets();
  });

  const currentWindow = tauriWindow && tauriWindow.getCurrentWindow
    ? tauriWindow.getCurrentWindow()
    : null;
  if (currentWindow) {
    winMinBtn.addEventListener("click", () => currentWindow.minimize());
    winCloseBtn.addEventListener("click", () => currentWindow.close());
  } else {
    winMinBtn.disabled = true;
    winCloseBtn.disabled = true;
  }

  if (themesEl) {
    themesEl.addEventListener("click", async (event) => {
      const button = event.target.closest("button");
      if (!button) return;
      try {
        appState = await invoke("set_theme", { theme: button.dataset.theme });
        applyTheme(appState.theme);
      } catch (error) {
        addRow("theme", errorText(error), "error");
      }
    });
  }

  darkQuery.addEventListener("change", () => {
    if (themePreference === "system") applyTheme("system");
  });

  pickBtn.addEventListener("click", async () => {
    try {
      const paths = await invoke("plugin:dialog|open", {
        options: { multiple: true, title: "Send to Telegram" },
      });
      if (Array.isArray(paths)) handleDrop(paths);
    } catch (error) {
      addRow("file picker", errorText(error), "error");
    }
  });

  sendCodeBtn.addEventListener("click", sendCode);
  signInBtn.addEventListener("click", signIn);
  confirmPasswordBtn.addEventListener("click", confirmPassword);
  loginCancelBtn.addEventListener("click", closeLogin);

  for (const input of [phoneInput, codeInput, passwordInput]) {
    input.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      const step = input.closest(".step");
      if (!step || step.hidden) return;
      if (step.dataset.step === "1") sendCodeBtn.click();
      else if (step.dataset.step === "2") signInBtn.click();
      else confirmPasswordBtn.click();
    });
  }

  settingsToggleBtn.addEventListener("click", openSettings);
  settingsCloseBtn.addEventListener("click", () => {
    settingsEl.hidden = true;
  });
  saveApiBtn.addEventListener("click", saveApi);
  if (checkBtn) checkBtn.addEventListener("click", runChecks);

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (settingsEl && !settingsEl.hidden) {
      settingsEl.hidden = true;
      settingsToggleBtn.focus();
    }
  });

  return bindHotState();
}

/* ---------- boot ---------- */

async function boot() {
  setBanner("preparing runtime…", false);
  renderPlaceholder();
  enhanceSelect(accountSelect);
  enhanceSelect(chatSelect);

  try {
    const prepared = await invoke("prepare");
    runtime = (prepared && prepared.runtime) || {};
    appState = (prepared && prepared.state) || appState;
  } catch (error) {
    setBanner(errorText(error), true);
    runtime = {};
    addRow("runtime", errorText(error), "error");
  }

  renderAccounts();
  renderSettingsFields();
  renderRuntimeInfo();

  if (runtime.ok === false) {
    setBanner(runtime.error || "runtime unavailable", true);
  } else if (!Number(appState.api_id) || !appState.api_hash) {
    setBanner("Add api_id and api_hash in settings to log in.", false);
  } else {
    setBanner("", false);
  }

  await wire();

  if (!accounts().length) {
    openLogin();
    return;
  }

  await loadTargets();
}

boot();
