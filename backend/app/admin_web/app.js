// Nexora web admin panel.
//
// Plain ES module, no framework, no build step, no third-party code.
//
// Security rules this file keeps (and that a reviewer should check for):
//
// * DOM is only ever built with createElement / textContent / setAttribute
//   through `h()`. There is no innerHTML, insertAdjacentHTML or
//   document.write anywhere, so a username like `<img onerror=...>` is shown
//   as text, never parsed. The page CSP forbids inline script as a second
//   line of defence.
// * The access token lives only in memory (lost on reload, by design). The
//   refresh token is kept in sessionStorage so a reload does not force a new
//   sign-in, but it dies with the tab and is never sent to any other origin
//   (connect-src 'self').
// * Refreshes are serialised. The backend treats a reused refresh token as
//   theft and revokes the whole token family, so two concurrent 401s must
//   share one refresh, never race two.
// * Sign-in sends no device_id: the panel is not a VPN device and must not
//   take a slot from the operator's own device allowance.
// * The role check here only hides buttons. Every action is enforced by the
//   backend, and a refusal is shown as the error it is.

const API = "/api/v1";
const RT_KEY = "nexora.admin.refresh";

const state = {
  access: null,
  me: null,
  refreshing: null,
};

// ------------------------------------------------------------- DOM helper
const SAFE_HREF = /^(#|\/)/;

/**
 * Create an element. `props` keys: `class`, `text`, `on<event>` handlers,
 * `value`/`checked` (set as properties), anything else as an attribute.
 * Children may be nodes, strings, numbers, arrays, or null/false (skipped);
 * strings always become text nodes.
 */
function h(tag, props, ...children) {
  const el = document.createElement(tag);
  if (props) {
    for (const [key, value] of Object.entries(props)) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") el.className = value;
      else if (key === "text") el.textContent = String(value);
      else if (key.startsWith("on") && typeof value === "function") {
        el.addEventListener(key.slice(2).toLowerCase(), value);
      } else if (key === "value" || key === "checked") el[key] = value;
      else if (key === "href") {
        // Links are only ever in-app routes; nothing from the server becomes
        // a URL scheme.
        if (SAFE_HREF.test(String(value))) el.setAttribute("href", String(value));
      } else el.setAttribute(key, value === true ? "" : String(value));
    }
  }
  append(el, children);
  return el;
}

function append(parent, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    parent.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return parent;
}

function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

// --------------------------------------------------------------- storage
// sessionStorage can throw (privacy modes); the panel still works, it just
// asks for a sign-in after every reload.
function readRT() {
  try { return sessionStorage.getItem(RT_KEY); } catch { return null; }
}
function writeRT(token) {
  try { sessionStorage.setItem(RT_KEY, token); } catch { /* memory only */ }
}
function clearRT() {
  try { sessionStorage.removeItem(RT_KEY); } catch { /* nothing stored */ }
}

// ------------------------------------------------------------------- API
class ApiError extends Error {
  constructor(message, code, status, details, requestId) {
    super(message);
    this.code = code;
    this.status = status;
    this.details = details || null;
    this.requestId = requestId || null;
  }
}

// Logout is allowed to refresh: with an expired access token it would
// otherwise fail and leave the refresh token alive on the server. The body
// still names the pre-rotation token, which is enough, because logout
// revokes that token's whole family.
const NO_REFRESH_PATHS = new Set(["/auth/login", "/auth/refresh"]);

async function api(method, path, body, { retry = true } = {}) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const usedToken = state.access;
  if (usedToken) headers.Authorization = `Bearer ${usedToken}`;

  let res;
  try {
    res = await fetch(API + path, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      credentials: "same-origin",
      cache: "no-store",
    });
  } catch {
    throw new ApiError("ارتباط با سرور برقرار نشد. اتصال اینترنت را بررسی کنید.", "NETWORK", 0);
  }

  if (res.status === 401 && retry && !NO_REFRESH_PATHS.has(path)) {
    // Another request may already have refreshed while this one was in
    // flight; then the new token is simply retried, without a refresh.
    const renewed = (state.access && state.access !== usedToken) || (await refreshSession());
    if (renewed) return api(method, path, body, { retry: false });
    sessionEnded("نشست شما به پایان رسید. دوباره وارد شوید.");
    throw new ApiError("نشست شما به پایان رسید.", "SESSION_ENDED", 401);
  }

  let payload = null;
  try { payload = await res.json(); } catch { /* not JSON */ }
  if (!res.ok || !payload || payload.success !== true) {
    const err = (payload && payload.error) || {};
    throw new ApiError(
      err.message || `خطای سرور (${res.status})`,
      err.code || `HTTP_${res.status}`,
      res.status,
      err.details,
      payload && payload.request_id,
    );
  }
  return payload.data;
}

/** One refresh at a time; concurrent callers await the same promise. */
function refreshSession() {
  if (!state.refreshing) {
    state.refreshing = (async () => {
      const token = readRT();
      if (!token) return false;
      try {
        const res = await fetch(`${API}/auth/refresh`, {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({ refresh_token: token }),
          credentials: "same-origin",
          cache: "no-store",
        });
        const payload = await res.json().catch(() => null);
        if (!res.ok || !payload || payload.success !== true) {
          clearRT();
          state.access = null;
          return false;
        }
        state.access = payload.data.access_token;
        writeRT(payload.data.refresh_token);
        return true;
      } catch {
        return false;
      }
    })().finally(() => {
      state.refreshing = null;
    });
  }
  return state.refreshing;
}

function can(capability) {
  return Boolean(state.me && state.me.capabilities.includes(capability));
}

// ------------------------------------------------------------ formatting
const ERROR_FA = {
  NETWORK: "ارتباط با سرور برقرار نشد.",
  AUTHENTICATION_FAILED: "نام کاربری یا رمز عبور اشتباه است.",
  ACCOUNT_LOCKED: "به دلیل تلاش‌های ناموفق زیاد، ورود موقتاً قفل شده است.",
  ACCOUNT_INACTIVE: "این حساب فعال نیست.",
  PERMISSION_DENIED: "نقش شما اجازه این کار را نمی‌دهد.",
  RATE_LIMITED: "درخواست‌ها بیش از حد مجاز است؛ کمی صبر کنید.",
  VALIDATION_ERROR: "مقادیر واردشده معتبر نیست.",
  ORDER_NOT_CANCELLABLE: "این سفارش قابل لغو نیست.",
  ORDER_NOT_PAID: "فقط سفارش پرداخت‌شده قابل ساخت مجدد است.",
  CANNOT_MODIFY_SELF: "نمی‌توانید حساب خودتان را از اینجا تغییر دهید.",
  ROLE_CHANGE_FORBIDDEN: "فقط مالک می‌تواند نقش‌ها را تغییر دهد.",
  TARGET_IS_OWNER: "فقط مالک می‌تواند حساب مالک را تغییر دهد.",
  LAST_OWNER: "این آخرین مالک فعال است؛ ابتدا مالک دیگری تعیین کنید.",
  INVALID_PANEL_GROUPS: "گروه‌های انتخاب‌شده معتبر نیستند.",
  PANEL_ERROR: "پنل درخواست را انجام نداد.",
  TOPUP_NOT_PENDING: "این درخواست قبلاً بررسی شده است.",
  TOPUP_NOT_FOUND: "درخواست شارژ پیدا نشد.",
  INSUFFICIENT_BALANCE: "موجودی کیف پول کافی نیست.",
  WALLET_TX_DUPLICATE: "این تراکنش قبلاً ثبت شده است.",
  INTERNAL_ERROR: "خطای داخلی سرور.",
};

/** A Persian summary where one is known, always followed by the server's own
 *  message so nothing the backend said is hidden. */
function describeError(err) {
  if (!(err instanceof ApiError)) return "خطای غیرمنتظره در مرورگر.";
  const fa = ERROR_FA[err.code];
  let text = fa && fa !== err.message ? `${fa} — ${err.message}` : err.message;
  if (err.status >= 500 && err.requestId) text += ` (شناسه درخواست: ${err.requestId})`;
  return text;
}

function errorBox(err) {
  const box = h("div", { class: "alert", role: "alert" }, describeError(err));
  const fields = err && err.details && Array.isArray(err.details.fields) ? err.details.fields : [];
  if (fields.length) {
    box.append(h("ul", null, fields.map((f) => h("li", null, h("code", null, f.field || "?"), ": ", f.message))));
  }
  return box;
}

const numberFmt = new Intl.NumberFormat("en-US");
function fmtNum(n) {
  return h("span", { class: "num" }, numberFmt.format(Number(n) || 0));
}

const CURRENCY_FA = { IRT: "تومان", IRR: "ریال" };
/** Amounts arrive as decimal strings; they are grouped as strings, never
 *  parsed to floats, so large rial amounts keep every digit. */
function fmtMoney(amount, currency) {
  const [intPart, frac = ""] = String(amount ?? "0").split(".");
  const neg = intPart.startsWith("-");
  const digits = neg ? intPart.slice(1) : intPart;
  const grouped = digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const trimmed = frac.replace(/0+$/, "");
  const text = `${neg ? "-" : ""}${grouped}${trimmed ? `.${trimmed}` : ""}`;
  return h("span", { class: "nowrap" }, h("span", { class: "num" }, text), " ", CURRENCY_FA[currency] || currency || "");
}

let dateFmt;
try {
  dateFmt = new Intl.DateTimeFormat("fa-IR-u-ca-persian-nu-latn", {
    year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
} catch {
  dateFmt = new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short" });
}

function parseDate(iso) {
  if (!iso) return null;
  // SQLite hands back naive timestamps; the backend stores UTC.
  const withZone = /[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`;
  const d = new Date(withZone.replace(" ", "T"));
  return Number.isNaN(d.getTime()) ? null : d;
}

function fmtDate(iso) {
  const d = parseDate(iso);
  if (!d) return h("span", { class: "dim" }, "—");
  return h("span", { class: "nowrap", title: d.toISOString() }, dateFmt.format(d));
}

function fmtBytes(bytes) {
  const n = Number(bytes) || 0;
  const gb = n / 1024 ** 3;
  if (gb >= 1) return `${gb.toFixed(gb >= 100 ? 0 : 2)} GB`;
  return `${(n / 1024 ** 2).toFixed(1)} MB`;
}

function traffic(used, limit) {
  if (!limit) {
    return h("div", null, h("span", { class: "num" }, fmtBytes(used)), " / نامحدود");
  }
  const pct = Math.min(100, Math.round((used / limit) * 100));
  return h("div", null,
    h("span", { class: "num" }, `${fmtBytes(used)} / ${fmtBytes(limit)}`),
    h("div", { class: `bar${pct >= 100 ? " full" : ""}`, title: `${pct}%` },
      barFill(pct)));
}

// Widths are set through CSSOM, which a style-src 'self' policy allows,
// unlike a style="" attribute.
function barFill(pct) {
  const span = h("span");
  span.style.width = `${pct}%`;
  return span;
}

const BADGES = {
  order: {
    PENDING: ["در انتظار پرداخت", "warn"], PAID: ["پرداخت‌شده", "ok"], FAILED: ["ناموفق", "danger"],
    CANCELLED: ["لغوشده", ""], REFUNDED: ["مسترد", ""],
  },
  user: {
    PENDING: ["در انتظار", "warn"], ACTIVE: ["فعال", "ok"], SUSPENDED: ["معلق", "warn"], BANNED: ["مسدود", "danger"],
  },
  sub: {
    PENDING: ["در حال ساخت", "warn"], ACTIVE: ["فعال", "ok"], EXPIRED: ["منقضی", ""],
    SUSPENDED: ["معلق", "warn"], CANCELLED: ["لغوشده", ""],
  },
  plan: { ACTIVE: ["فعال", "ok"], HIDDEN: ["پنهان", "warn"], ARCHIVED: ["بایگانی", ""] },
  panel: { ACTIVE: ["در دسترس", "ok"], DISABLED: ["غیرفعال", ""], UNREACHABLE: ["خارج از دسترس", "danger"] },
  server: { ACTIVE: ["فعال", "ok"], MAINTENANCE: ["در حال تعمیر", "warn"], DISABLED: ["غیرفعال", ""] },
  device: { ACTIVE: ["فعال", "ok"], REVOKED: ["لغوشده", ""] },
  topup: {
    PENDING: ["در انتظار بررسی", "warn"], APPROVED: ["تأییدشده", "ok"],
    REJECTED: ["ردشده", "danger"], CANCELLED: ["لغو توسط کاربر", ""],
  },
  wallet: { TOPUP: ["شارژ", "ok"], PURCHASE: ["خرید", ""], ADJUSTMENT: ["اصلاح دستی", "warn"] },
};

function badge(kind, value) {
  const [label, tone] = (BADGES[kind] && BADGES[kind][value]) || [value || "—", ""];
  return h("span", { class: `badge ${tone}`, title: value || "" }, label);
}

const ROLE_FA = {
  OWNER: "مالک", MANAGER: "مدیر", DEVELOPER: "توسعه‌دهنده", FINANCE: "مالی", SUPPORT: "پشتیبانی",
};
function roleLabel(role) {
  return role ? ROLE_FA[role] || role : "کاربر عادی";
}

// --------------------------------------------------------------- widgets
function spinner() {
  return h("span", { class: "spinner", "aria-hidden": "true" });
}

function loading() {
  return h("div", { class: "loading", "aria-busy": "true" }, spinner());
}

/** Run `fn` with the button disabled and a spinner shown; errors become a
 *  toast unless the caller handles them. */
async function busy(button, fn) {
  if (button.disabled) return undefined;
  button.disabled = true;
  const spin = spinner();
  button.prepend(spin);
  try {
    return await fn();
  } finally {
    spin.remove();
    button.disabled = false;
  }
}

function toast(message, { error = false } = {}) {
  const host = document.getElementById("toasts");
  const el = h("div", { class: `toast${error ? " error" : ""}` }, message);
  host.append(el);
  setTimeout(() => el.remove(), error ? 7000 : 4000);
}

function toastError(err) {
  toast(describeError(err), { error: true });
}

/**
 * A table that turns into stacked cards on a phone. `columns` is a list of
 * { label, cell(row) -> node|string, class? }.
 */
function table(columns, rows, emptyText = "موردی یافت نشد.") {
  if (!rows.length) return h("div", { class: "empty" }, emptyText);
  return h("div", { class: "table-wrap" },
    h("table", { class: "responsive" },
      h("thead", null, h("tr", null, columns.map((c) => h("th", { scope: "col" }, c.label)))),
      h("tbody", null, rows.map((row) => h("tr", null, columns.map((c) =>
        h("td", { class: c.class || null, "data-label": c.label }, c.cell(row))))))));
}

function pager(total, limit, offset, onGo) {
  const from = total ? offset + 1 : 0;
  const to = Math.min(offset + limit, total);
  return h("div", { class: "pager" },
    h("span", { class: "dim small" }, fmtNum(from), "–", fmtNum(to), " از ", fmtNum(total)),
    h("div", { class: "toolbar" },
      h("button", { class: "btn sm", disabled: offset <= 0, onclick: () => onGo(Math.max(0, offset - limit)) }, "قبلی"),
      h("button", { class: "btn sm", disabled: offset + limit >= total, onclick: () => onGo(offset + limit) }, "بعدی")));
}

// ----------------------------------------------------------------- modals
function openModal(title, bodyNodes, { onClose } = {}) {
  const previous = document.activeElement;
  const modal = h("div", { class: "modal", role: "dialog", "aria-modal": "true", "aria-label": title },
    h("h2", null, title), bodyNodes);
  const overlay = h("div", { class: "overlay" }, modal);
  const close = () => {
    overlay.remove();
    document.removeEventListener("keydown", onKey);
    if (onClose) onClose();
    if (previous && previous.focus) previous.focus();
  };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
  document.addEventListener("keydown", onKey);
  document.body.append(overlay);
  const focusable = modal.querySelector("input, select, textarea, button");
  if (focusable) focusable.focus();
  return { close, modal };
}

/** Resolves true only when the operator presses the confirm button. */
function confirmDialog({ title, message, confirmText = "تأیید", danger = false }) {
  return new Promise((resolve) => {
    let decided = false;
    const done = (value) => { if (!decided) { decided = true; resolve(value); } };
    const cancel = h("button", { class: "btn ghost", type: "button" }, "انصراف");
    const ok = h("button", { class: `btn ${danger ? "solid-danger" : "primary"}`, type: "button" }, confirmText);
    const { close } = openModal(title, [
      h("div", null, message),
      h("div", { class: "actions-row" }, cancel, ok),
    ], { onClose: () => done(false) });
    cancel.addEventListener("click", () => { done(false); close(); });
    ok.addEventListener("click", () => { done(true); close(); });
    cancel.focus();
  });
}

/**
 * A form in a modal. `fields`: { name, label, type, value, options, hint,
 * required, attrs }. `onSubmit(values)` may throw an ApiError, which is shown
 * inside the modal so the operator can correct the input.
 */
function formDialog({ title, fields, submitText = "ذخیره", onSubmit }) {
  const errorHost = h("div");
  const inputs = {};
  const rows = fields.map((f) => {
    const id = `f-${f.name}-${Math.random().toString(36).slice(2, 8)}`;
    let input;
    if (f.type === "select") {
      input = h("select", { id, name: f.name },
        f.options.map(([value, label]) => h("option", { value, selected: String(f.value ?? "") === value }, label)));
    } else if (f.type === "textarea") {
      input = h("textarea", { id, name: f.name, ...(f.attrs || {}) });
      input.value = f.value ?? "";
    } else if (f.type === "checkbox") {
      input = h("input", { id, name: f.name, type: "checkbox", checked: Boolean(f.value) });
      inputs[f.name] = input;
      return h("label", { class: "check", for: id }, input, h("span", null, f.label));
    } else {
      input = h("input", { id, name: f.name, type: f.type || "text", required: f.required, ...(f.attrs || {}) });
      input.value = f.value ?? "";
    }
    inputs[f.name] = input;
    return h("div", { class: "field" },
      h("label", { for: id }, f.label),
      input,
      f.hint ? h("span", { class: "hint" }, f.hint) : null);
  });

  const submit = h("button", { class: "btn primary", type: "submit" }, submitText);
  const cancel = h("button", { class: "btn ghost", type: "button" }, "انصراف");
  const form = h("form", { novalidate: true }, errorHost, rows, h("div", { class: "actions-row" }, cancel, submit));
  const { close } = openModal(title, form);
  cancel.addEventListener("click", close);
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const values = {};
    for (const [name, input] of Object.entries(inputs)) {
      values[name] = input.type === "checkbox" ? input.checked : input.value.trim();
    }
    busy(submit, async () => {
      clear(errorHost);
      try {
        await onSubmit(values);
        close();
      } catch (err) {
        errorHost.append(errorBox(err));
      }
    });
  });
}

// ------------------------------------------------------------------ auth
function renderLogin(message, { tone = "alert" } = {}) {
  const root = clear(document.getElementById("app"));
  const errorHost = h("div");
  if (message) errorHost.append(h("div", { class: tone === "info" ? "alert info" : "alert", role: "alert" }, message));

  const identifier = h("input", { id: "login-id", name: "username", autocomplete: "username", required: true, dir: "ltr" });
  const password = h("input", { id: "login-pw", name: "password", type: "password", autocomplete: "current-password", required: true, dir: "ltr" });
  const submit = h("button", { class: "btn primary block", type: "submit" }, "ورود");

  const form = h("form", { class: "login-card" },
    h("div", { class: "brand" }, h("span", { class: "brand-mark", "aria-hidden": "true" }, "N"), "پنل مدیریت نکسورا"),
    errorHost,
    h("div", { class: "field" }, h("label", { for: "login-id" }, "نام کاربری یا ایمیل"), identifier),
    h("div", { class: "field" }, h("label", { for: "login-pw" }, "رمز عبور"), password),
    submit);

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (!identifier.value.trim() || !password.value) return;
    busy(submit, async () => {
      clear(errorHost);
      try {
        await signIn(identifier.value.trim(), password.value);
      } catch (err) {
        password.value = "";
        errorHost.append(err instanceof NotAdminError
          ? h("div", { class: "alert", role: "alert" }, "این حساب مدیر نیست. ورود به پنل مدیریت فقط برای حساب‌هایی است که نقش مدیریتی دارند.")
          : errorBox(err));
      }
    });
  });

  root.append(h("div", { class: "login-wrap" }, form));
  identifier.focus();
}

class NotAdminError extends Error {}

async function signIn(identifier, password) {
  // No device_id: signing in to the panel must not register a VPN device.
  const data = await api("POST", "/auth/login", { identifier, password }, { retry: false });
  state.access = data.tokens.access_token;
  writeRT(data.tokens.refresh_token);
  try {
    await loadMe();
  } catch (err) {
    if (err instanceof ApiError && err.status === 403) {
      // A customer account: end the session we just opened rather than
      // leaving a live refresh token behind in this tab.
      await signOut({ silent: true });
      throw new NotAdminError();
    }
    throw err;
  }
  // Setting the hash fires hashchange, which routes; routing here as well
  // would load the first page twice.
  if (!location.hash || location.hash === "#" || location.hash === "#/") location.hash = "#/dashboard";
  else route();
}

async function loadMe() {
  state.me = await api("GET", "/admin/me");
}

async function signOut({ silent = false } = {}) {
  if (state.access) {
    try { await api("POST", "/auth/logout", { refresh_token: readRT() }); } catch { /* ending anyway */ }
  }
  state.access = null;
  state.me = null;
  clearRT();
  if (!silent) renderLogin("از حساب خارج شدید.", { tone: "info" });
}

function sessionEnded(message) {
  state.access = null;
  state.me = null;
  clearRT();
  renderLogin(message);
}

// ---------------------------------------------------------------- router
const PAGES = [
  { path: "dashboard", title: "داشبورد", cap: "stats", render: pageDashboard },
  { path: "topups", title: "پرداخت‌ها", cap: "payments", render: pageTopups },
  { path: "orders", title: "سفارش‌ها", cap: "orders", render: pageOrders },
  { path: "plans", title: "پلن‌ها", cap: "plans", render: pagePlans },
  { path: "users", title: "کاربران", cap: "users.read", render: pageUsers },
  { path: "subscriptions", title: "اشتراک‌ها", cap: "subscriptions.read", render: pageSubscriptions },
  { path: "panels", title: "پنل‌ها", cap: "panels", render: pagePanels },
  { path: "servers", title: "سرورها", cap: "panels", render: pageServers },
  { path: "payment-settings", title: "تنظیمات پرداخت", cap: "payments.settings", render: pagePaymentSettings },
  { path: "audit", title: "گزارش رویدادها", cap: "audit.read", render: pageAudit },
];

function parseHash() {
  const raw = location.hash.replace(/^#\/?/, "");
  const [pathPart, queryPart = ""] = raw.split("?");
  const segments = pathPart.split("/").filter(Boolean).map((s) => {
    try { return decodeURIComponent(s); } catch { return s; }
  });
  return { segments, query: new URLSearchParams(queryPart) };
}

function go(hash) {
  if (location.hash === hash) route();
  else location.hash = hash;
}

function renderShell() {
  const root = clear(document.getElementById("app"));
  const nav = h("nav", { class: "nav", "aria-label": "بخش‌ها" },
    PAGES.filter((p) => can(p.cap)).map((p) => h("a", { href: `#/${p.path}`, "data-page": p.path }, p.title)));
  const logout = h("button", { class: "btn sm", type: "button" }, "خروج");
  logout.addEventListener("click", () => busy(logout, () => signOut()));

  const brand = () => h("div", { class: "brand" }, h("span", { class: "brand-mark", "aria-hidden": "true" }, "N"), "نکسورا");
  root.append(h("div", { class: "shell" },
    h("aside", { class: "sidebar" }, brand(), nav),
    h("div", { class: "main" },
      h("header", { class: "topbar" },
        brand(),
        h("div", { class: "who" },
          h("span", { class: "name ltr" }, state.me.username),
          h("span", { class: "badge accent" }, roleLabel(state.me.role))),
        logout),
      h("main", { class: "content", id: "content" }))));
}

function route() {
  if (!state.me) return;
  if (!document.getElementById("content")) renderShell();
  const { segments, query } = parseHash();
  const allowed = PAGES.filter((p) => can(p.cap));
  let page = allowed.find((p) => p.path === segments[0]);
  if (!page) {
    page = allowed[0];
    if (!page) {
      clear(document.getElementById("content")).append(h("div", { class: "alert" }, "نقش شما به هیچ بخشی دسترسی ندارد."));
      return;
    }
    if (segments[0] !== page.path) {
      history.replaceState(null, "", `#/${page.path}`);
    }
  }
  for (const a of document.querySelectorAll(".nav a")) {
    a.classList.toggle("active", a.dataset.page === page.path);
    if (a.dataset.page === page.path) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
  // Every render gets a fresh container. An async page checks
  // `container.isConnected` before writing, so a slow response for a page
  // the operator has already left is dropped instead of painted over the
  // current one.
  const container = h("div");
  clear(document.getElementById("content")).append(container);
  document.title = `${page.title} · پنل مدیریت نکسورا`;
  page.render(container, segments.slice(1), query).catch((err) => {
    if (container.isConnected && !(err instanceof ApiError && err.code === "SESSION_ENDED")) {
      clear(container).append(errorBox(err));
    }
  });
}

function pageHead(title, ...tools) {
  return h("div", { class: "page-head" }, h("h1", null, title), h("div", { class: "toolbar" }, tools));
}

function refreshButton(onClick) {
  const btn = h("button", { class: "btn sm", type: "button" }, "بروزرسانی");
  btn.addEventListener("click", () => busy(btn, onClick));
  return btn;
}

// -------------------------------------------------------------- dashboard
async function pageDashboard(container) {
  const body = h("div", null, loading());
  const load = async () => {
    const s = await api("GET", "/admin/stats");
    if (!container.isConnected) return;
    const byStatus = s.users.by_status || {};
    const cards = [
      statCard("کاربران", fmtNum(s.users.total),
        `فعال ${numberFmt.format(byStatus.ACTIVE || 0)} · معلق ${numberFmt.format(byStatus.SUSPENDED || 0)} · مسدود ${numberFmt.format(byStatus.BANNED || 0)}`),
      statCard("اشتراک‌های فعال", fmtNum(s.subscriptions.active),
        `${numberFmt.format(s.subscriptions.expiring_7d)} اشتراک تا 7 روز آینده منقضی می‌شود`),
      statCard("پرداخت‌های منتظر بررسی", fmtNum((s.topups && s.topups.pending) || 0),
        "رسیدهای کارت‌به‌کارت و کریپتو که هنوز تأیید یا رد نشده‌اند",
        { warn: Boolean(s.topups && s.topups.pending), href: can("payments") ? "#/topups" : null }),
      statCard("سفارش‌های در انتظار پرداخت", fmtNum(s.orders.pending), "هنوز از کیف پول پرداخت نشده‌اند",
        { warn: s.orders.pending > 0, href: can("orders") ? "#/orders" : null }),
      statCard("پرداخت‌شده بدون سرویس", fmtNum(s.orders.paid_unprovisioned),
        "در صف ساخت، یا ساخت سرویس ناموفق بوده", { warn: s.orders.paid_unprovisioned > 0 }),
      statCard("پنل‌های در دسترس",
        h("span", null, fmtNum(s.panels.reachable), h("span", { class: "dim small" }, " از "), fmtNum(s.panels.total)),
        s.panels.untested
          ? `بر اساس آخرین تست اتصال · ${numberFmt.format(s.panels.untested)} پنل هنوز تست نشده`
          : "بر اساس آخرین تست اتصال",
        { warn: s.panels.total > 0 && s.panels.reachable < s.panels.total }),
    ];
    if (Array.isArray(s.revenue_30d)) {
      if (s.revenue_30d.length === 0) {
        cards.push(statCard("درآمد 30 روز اخیر", fmtNum(0), "پرداختی در این بازه ثبت نشده"));
      }
      for (const r of s.revenue_30d) {
        cards.push(statCard("درآمد 30 روز اخیر", fmtMoney(r.amount, r.currency),
          `${numberFmt.format(r.orders)} سفارش پرداخت‌شده`));
      }
    }
    clear(body).append(
      h("div", { class: "grid" }, cards),
      h("p", { class: "dim small" }, "همه اعداد مستقیماً از پایگاه داده محاسبه شده‌اند · ", fmtDate(s.generated_at)));
  };
  container.append(pageHead("داشبورد", refreshButton(() => load().catch(toastError))), body);
  await load();
}

function statCard(label, value, sub, { warn = false, href = null } = {}) {
  return h("div", { class: `card stat${warn ? " warn" : ""}` },
    h("div", { class: "label" }, label),
    href ? h("a", { class: "value", href }, value) : h("div", { class: "value" }, value),
    sub ? h("div", { class: "sub" }, sub) : null);
}

// ----------------------------------------------------------------- orders
let ordersFilter = "PENDING";
const ORDER_FILTERS = [
  ["PENDING", "در انتظار پرداخت"], ["", "همه"], ["PAID", "پرداخت‌شده"],
  ["CANCELLED", "لغوشده"], ["FAILED", "ناموفق"], ["REFUNDED", "مسترد"],
];

async function pageOrders(container) {
  const body = h("div", { class: "card" }, loading());
  const select = h("select", { "aria-label": "وضعیت" },
    ORDER_FILTERS.map(([v, l]) => h("option", { value: v, selected: v === ordersFilter }, l)));

  const load = async () => {
    const qs = new URLSearchParams({ limit: "200" });
    if (ordersFilter) qs.set("order_status", ordersFilter);
    const rows = await api("GET", `/admin/orders?${qs}`);
    if (!container.isConnected) return;
    // Pending first: that is the queue someone is working through.
    rows.sort((a, b) => (a.status === "PENDING" ? 0 : 1) - (b.status === "PENDING" ? 0 : 1));
    clear(body).append(table([
      { label: "کاربر", cell: (o) => userLink(o.username) },
      { label: "پلن", cell: (o) => o.plan_name },
      { label: "مبلغ", cell: (o) => fmtMoney(o.amount, o.currency) },
      { label: "وضعیت", cell: (o) => h("span", null, badge("order", o.status),
        o.failure_reason ? h("div", { class: "dim small" }, o.failure_reason) : null) },
      { label: "زمان ثبت", cell: (o) => fmtDate(o.created_at) },
      { label: "عملیات", class: "actions", cell: (o) => orderActions(o, load) },
    ], rows, ordersFilter === "PENDING" ? "سفارشی در انتظار پرداخت نیست." : "سفارشی یافت نشد."));
  };

  select.addEventListener("change", () => {
    ordersFilter = select.value;
    clear(body).append(loading());
    load().catch((err) => { if (container.isConnected) clear(body).append(errorBox(err)); });
  });
  container.append(pageHead("سفارش‌ها", select, refreshButton(() => load().catch(toastError))), body);
  await load();
}

function userLink(username) {
  const name = h("span", { class: "ltr" }, username);
  if (!can("users.read")) return name;
  return h("a", { href: `#/users?q=${encodeURIComponent(username)}` }, name);
}

function orderActions(order, reload) {
  const buttons = [];
  if (order.status === "PENDING") {
    const confirm = h("button", { class: "btn sm primary", type: "button" }, "تأیید پرداخت");
    confirm.addEventListener("click", async () => {
      const ok = await confirmDialog({
        title: "تأیید پرداخت",
        message: [
          h("p", null, "آیا پرداخت این سفارش را دریافت کرده‌اید؟"),
          h("dl", { class: "kv" },
            h("dt", null, "کاربر"), h("dd", null, h("span", { class: "ltr" }, order.username)),
            h("dt", null, "پلن"), h("dd", null, order.plan_name),
            h("dt", null, "مبلغ"), h("dd", null, fmtMoney(order.amount, order.currency)),
            h("dt", null, "شناسه"), h("dd", null, h("code", null, order.id))),
          h("p", { class: "alert warn" }, "پس از تأیید، سرویس کاربر ساخته می‌شود. این کار قابل بازگشت نیست."),
        ],
        confirmText: "بله، پرداخت دریافت شده",
      });
      if (!ok) return;
      await busy(confirm, async () => {
        try {
          const r = await api("POST", `/admin/orders/${encodeURIComponent(order.id)}/confirm-payment`);
          toast(r.newly_paid ? "پرداخت تأیید شد و ساخت سرویس در صف قرار گرفت." : "این سفارش قبلاً پرداخت شده بود؛ کاری انجام نشد.");
          await reload();
        } catch (err) { toastError(err); }
      });
    });
    const cancel = h("button", { class: "btn sm danger", type: "button" }, "لغو");
    cancel.addEventListener("click", async () => {
      const ok = await confirmDialog({
        title: "لغو سفارش",
        message: h("p", null, "سفارش ", h("span", { class: "ltr" }, order.username), " (", order.plan_name, ") لغو شود؟"),
        confirmText: "لغو سفارش",
        danger: true,
      });
      if (!ok) return;
      await busy(cancel, async () => {
        try {
          await api("POST", `/admin/orders/${encodeURIComponent(order.id)}/cancel`);
          toast("سفارش لغو شد.");
          await reload();
        } catch (err) { toastError(err); }
      });
    });
    buttons.push(confirm, cancel);
  }
  if (order.status === "PAID" && !order.subscription_id && can("panels")) {
    const retry = h("button", { class: "btn sm", type: "button" }, "ساخت مجدد سرویس");
    retry.addEventListener("click", async () => {
      const ok = await confirmDialog({
        title: "ساخت مجدد سرویس",
        message: h("p", null, "این سفارش پرداخت شده ولی هنوز سرویسی به آن متصل نیست. ساخت سرویس دوباره در صف قرار بگیرد؟ اگر سرویس قبلاً ساخته شده باشد، حساب دوم ساخته نمی‌شود."),
        confirmText: "در صف قرار بده",
      });
      if (!ok) return;
      await busy(retry, async () => {
        try {
          await api("POST", `/admin/orders/${encodeURIComponent(order.id)}/reprovision`);
          toast("ساخت سرویس در صف قرار گرفت.");
        } catch (err) { toastError(err); }
      });
    });
    buttons.push(retry);
  }
  return buttons.length ? buttons : h("span", { class: "dim" }, "—");
}

// ------------------------------------------------------------------ plans
const PLAN_STATUS_OPTIONS = [["ACTIVE", "فعال (در فروشگاه)"], ["HIDDEN", "پنهان"], ["ARCHIVED", "بایگانی"]];

async function pagePlans(container) {
  const body = h("div", { class: "card" }, loading());
  const load = async () => {
    const plans = await api("GET", "/admin/plans");
    if (!container.isConnected) return;
    clear(body).append(table([
      { label: "نام", cell: (p) => h("div", null, h("strong", null, p.name),
        p.description ? h("div", { class: "dim small" }, p.description) : null) },
      { label: "مدت", cell: (p) => h("span", null, fmtNum(p.duration_days), " روز") },
      { label: "ترافیک", cell: (p) => (p.traffic_limit_bytes ? h("span", { class: "num" }, fmtBytes(p.traffic_limit_bytes)) : "نامحدود") },
      { label: "دستگاه", cell: (p) => fmtNum(p.device_limit) },
      { label: "قیمت", cell: (p) => fmtMoney(p.price, p.currency) },
      { label: "وضعیت", cell: (p) => badge("plan", p.status) },
      { label: "ترتیب", cell: (p) => fmtNum(p.sort_order) },
      { label: "عملیات", class: "actions", cell: (p) => {
        const edit = h("button", { class: "btn sm", type: "button" }, "ویرایش");
        edit.addEventListener("click", () => editPlan(p, load));
        return edit;
      } },
    ], plans, "هنوز پلنی ساخته نشده است."));
  };
  const create = h("button", { class: "btn primary sm", type: "button" }, "پلن جدید");
  create.addEventListener("click", () => createPlan(load));
  container.append(pageHead("پلن‌ها", create, refreshButton(() => load().catch(toastError))),
    h("p", { class: "dim small" }, "ویرایش پلن روی خریدهای قبلی اثری ندارد؛ هر سفارش نسخه‌ای از پلن در لحظه خرید را نگه می‌دارد. برای خارج کردن پلن از فروش، آن را پنهان یا بایگانی کنید."),
    body);
  await load();
}

function createPlan(reload) {
  formDialog({
    title: "پلن جدید",
    submitText: "ساخت پلن",
    fields: [
      { name: "name", label: "نام", required: true, attrs: { maxlength: 128 } },
      { name: "description", label: "توضیحات", type: "textarea", attrs: { maxlength: 2000 } },
      { name: "duration_days", label: "مدت (روز)", type: "number", value: "30", attrs: { min: 1, max: 3650, inputmode: "numeric", dir: "ltr" } },
      { name: "traffic_limit_gb", label: "ترافیک (گیگابایت)", type: "number", value: "0", hint: "0 یعنی نامحدود", attrs: { min: 0, step: "any", dir: "ltr" } },
      { name: "device_limit", label: "تعداد دستگاه", type: "number", value: "1", attrs: { min: 1, max: 100, inputmode: "numeric", dir: "ltr" } },
      { name: "price", label: "قیمت", required: true, hint: "فقط عدد، بدون جداکننده", attrs: { inputmode: "decimal", dir: "ltr" } },
      { name: "currency", label: "واحد پول", type: "select", value: "IRT", options: [["IRT", "تومان (IRT)"], ["IRR", "ریال (IRR)"], ["USD", "دلار (USD)"]] },
      { name: "status", label: "وضعیت", type: "select", value: "ACTIVE", options: PLAN_STATUS_OPTIONS },
      { name: "sort_order", label: "ترتیب نمایش", type: "number", value: "0", attrs: { min: 0, max: 9999, inputmode: "numeric", dir: "ltr" } },
    ],
    onSubmit: async (v) => {
      await api("POST", "/admin/plans", {
        name: v.name,
        description: v.description || null,
        duration_days: toInt(v.duration_days),
        traffic_limit_gb: Number(v.traffic_limit_gb || 0),
        device_limit: toInt(v.device_limit),
        price: normaliseDigits(v.price),
        currency: v.currency,
        status: v.status,
        sort_order: toInt(v.sort_order || "0"),
      });
      toast("پلن ساخته شد.");
      await reload();
    },
  });
}

function editPlan(plan, reload) {
  formDialog({
    title: `ویرایش پلن «${plan.name}»`,
    fields: [
      { name: "name", label: "نام", value: plan.name, required: true, attrs: { maxlength: 128 } },
      { name: "description", label: "توضیحات", type: "textarea", value: plan.description || "", attrs: { maxlength: 2000 } },
      { name: "price", label: `قیمت (${CURRENCY_FA[plan.currency] || plan.currency})`, value: plan.price, attrs: { inputmode: "decimal", dir: "ltr" } },
      { name: "sort_order", label: "ترتیب نمایش", type: "number", value: String(plan.sort_order), attrs: { min: 0, max: 9999, dir: "ltr" } },
      { name: "status", label: "وضعیت", type: "select", value: plan.status, options: PLAN_STATUS_OPTIONS },
    ],
    onSubmit: async (v) => {
      // Only what changed is sent, so the audit entry records the real edit.
      const patch = {};
      if (v.name !== plan.name) patch.name = v.name;
      if (v.description !== (plan.description || "")) patch.description = v.description || null;
      if (normaliseDigits(v.price) !== String(plan.price)) patch.price = normaliseDigits(v.price);
      if (toInt(v.sort_order) !== plan.sort_order) patch.sort_order = toInt(v.sort_order);
      if (v.status !== plan.status) patch.status = v.status;
      if (!Object.keys(patch).length) return;
      await api("PATCH", `/admin/plans/${encodeURIComponent(plan.id)}`, patch);
      toast("پلن ذخیره شد.");
      await reload();
    },
  });
}

// Operators type on a Persian keyboard; accept Persian/Arabic digits.
function normaliseDigits(value) {
  return String(value ?? "")
    .replace(/[۰-۹]/g, (d) => String("۰۱۲۳۴۵۶۷۸۹".indexOf(d)))
    .replace(/[٠-٩]/g, (d) => String("٠١٢٣٤٥٦٧٨٩".indexOf(d)))
    .replace(/[,،\s]/g, "");
}

function toInt(value) {
  const n = Number.parseInt(normaliseDigits(value), 10);
  return Number.isNaN(n) ? null : n;
}

// ------------------------------------------------------------------ users
async function pageUsers(container, segments, query) {
  if (segments[0]) return pageUserDetail(container, segments[0]);

  const q = query.get("q") || "";
  const offset = Math.max(0, Number.parseInt(query.get("offset") || "0", 10) || 0);
  const limit = 25;

  const input = h("input", { type: "search", placeholder: "نام کاربری، ایمیل یا تلفن", value: q, "aria-label": "جستجو", dir: "auto" });
  const searchBtn = h("button", { class: "btn primary sm", type: "submit" }, "جستجو");
  const form = h("form", { class: "toolbar", role: "search" }, input, searchBtn);
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const qs = new URLSearchParams();
    if (input.value.trim()) qs.set("q", input.value.trim());
    go(`#/users${qs.toString() ? `?${qs}` : ""}`);
  });

  const body = h("div", { class: "card" }, loading());
  container.append(pageHead("کاربران", form), body);

  const qs = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (q) qs.set("q", q);
  const data = await api("GET", `/admin/users?${qs}`);
  if (!container.isConnected) return;
  clear(body).append(
    table([
      { label: "نام کاربری", cell: (u) => h("a", { href: `#/users/${encodeURIComponent(u.id)}` }, h("span", { class: "ltr" }, u.username)) },
      { label: "ایمیل", cell: (u) => (u.email ? h("span", { class: "ltr break" }, u.email) : h("span", { class: "dim" }, "—")) },
      { label: "وضعیت", cell: (u) => badge("user", u.status) },
      { label: "نقش", cell: (u) => (u.admin_role ? h("span", { class: "badge accent" }, roleLabel(u.admin_role)) : h("span", { class: "dim" }, "—")) },
      { label: "اشتراک فعال", cell: (u) => (u.active_subscriptions
        ? h("span", null, fmtNum(u.active_subscriptions), h("span", { class: "dim small" }, " · تا "), fmtDate(u.latest_expire_at))
        : h("span", { class: "dim" }, "ندارد")) },
      { label: "ثبت‌نام", cell: (u) => fmtDate(u.created_at) },
      { label: "آخرین ورود", cell: (u) => fmtDate(u.last_login_at) },
    ], data.items, q ? "کاربری با این مشخصات یافت نشد." : "هنوز کاربری ثبت‌نام نکرده است."),
    pager(data.total, limit, offset, (next) => {
      const p = new URLSearchParams();
      if (q) p.set("q", q);
      if (next) p.set("offset", String(next));
      go(`#/users${p.toString() ? `?${p}` : ""}`);
    }));
  return undefined;
}

async function pageUserDetail(container, userId) {
  container.append(h("p", null, h("a", { href: "#/users" }, "→ بازگشت به کاربران")), loading());
  const u = await api("GET", `/admin/users/${encodeURIComponent(userId)}`);
  if (!container.isConnected) return;
  const reload = () => { if (container.isConnected) route(); };

  clear(container).append(
    h("p", null, h("a", { href: "#/users" }, "→ بازگشت به کاربران")),
    pageHead(h("span", { class: "ltr" }, u.username)),
    h("div", { class: "card" },
      h("dl", { class: "kv" },
        h("dt", null, "وضعیت"), h("dd", null, badge("user", u.status)),
        h("dt", null, "نقش"), h("dd", null, roleLabel(u.admin_role)),
        h("dt", null, "ایمیل"), h("dd", null, u.email ? h("span", { class: "ltr" }, u.email) : "—",
          u.email ? h("span", { class: "dim small" }, u.is_email_verified ? " (تأییدشده)" : " (تأییدنشده)") : null),
        h("dt", null, "تلفن"), h("dd", null, u.phone ? h("span", { class: "ltr" }, u.phone) : "—"),
        h("dt", null, "ثبت‌نام"), h("dd", null, fmtDate(u.created_at)),
        h("dt", null, "آخرین ورود"), h("dd", null, fmtDate(u.last_login_at)),
        h("dt", null, "شناسه"), h("dd", null, h("code", null, u.id))),
      userAdminActions(u, reload)),
    can("payments") ? walletCard(u) : null,
    h("div", { class: "card" }, h("h2", null, "اشتراک‌ها"), subscriptionsTable(u.subscriptions, { showUser: false })),
    h("div", { class: "card" }, h("h2", null, "دستگاه‌ها"), devicesTable(u, reload)),
    h("div", { class: "card" }, h("h2", null, "آخرین سفارش‌ها"), table([
      { label: "پلن", cell: (o) => o.plan_name },
      { label: "مبلغ", cell: (o) => fmtMoney(o.amount, o.currency) },
      { label: "وضعیت", cell: (o) => badge("order", o.status) },
      { label: "ثبت", cell: (o) => fmtDate(o.created_at) },
      { label: "تکمیل", cell: (o) => fmtDate(o.completed_at) },
    ], u.orders, "سفارشی ثبت نشده است.")));
}

function userAdminActions(u, reload) {
  const self = state.me && state.me.id === u.id;
  const ownerOnly = u.admin_role === "OWNER" && state.me.role !== "OWNER";
  const wrap = h("div", { class: "toolbar mt" });

  if (self) {
    wrap.append(h("span", { class: "dim small" }, "این حساب خودتان است و از اینجا قابل تغییر نیست."));
    return wrap;
  }
  if (ownerOnly) return null;

  const patch = async (btn, body, done) => {
    await busy(btn, async () => {
      try {
        const r = await api("PATCH", `/admin/users/${encodeURIComponent(u.id)}`, body);
        toast(r.sessions_revoked ? `${done} ${numberFmt.format(r.sessions_revoked)} نشست فعال بسته شد.` : done);
        reload();
      } catch (err) { toastError(err); }
    });
  };

  if (can("users.status")) {
    const statusButton = (target, label, cls, question) => {
      const btn = h("button", { class: `btn sm ${cls}`, type: "button" }, label);
      btn.addEventListener("click", async () => {
        const ok = await confirmDialog({
          title: label,
          message: h("p", null, question),
          confirmText: label,
          danger: target !== "ACTIVE",
        });
        if (ok) await patch(btn, { status: target }, `وضعیت حساب به «${BADGES.user[target][0]}» تغییر کرد.`);
      });
      return btn;
    };
    if (u.status === "ACTIVE") {
      wrap.append(
        statusButton("SUSPENDED", "تعلیق حساب", "danger", "حساب تعلیق شود؟ همه نشست‌های کاربر بسته می‌شود و تا فعال‌سازی دوباره نمی‌تواند وارد شود."),
        statusButton("BANNED", "مسدودسازی", "danger", "حساب مسدود شود؟ همه نشست‌های کاربر بسته می‌شود."));
    } else {
      wrap.append(statusButton("ACTIVE", "فعال‌سازی حساب", "primary", "حساب دوباره فعال شود؟"));
    }
  }

  if (can("roles.write")) {
    const select = h("select", { "aria-label": "نقش مدیریتی" },
      [["", "بدون نقش (کاربر عادی)"], ...Object.entries(ROLE_FA)].map(([v, l]) =>
        h("option", { value: v, selected: (u.admin_role || "") === v }, l)));
    const save = h("button", { class: "btn sm", type: "button" }, "ذخیره نقش");
    save.addEventListener("click", async () => {
      const next = select.value || null;
      if (next === (u.admin_role || null)) return;
      const ok = await confirmDialog({
        title: "تغییر نقش",
        message: h("p", null, "نقش ", h("span", { class: "ltr" }, u.username), ` از «${roleLabel(u.admin_role)}» به «${roleLabel(next)}» تغییر کند؟`),
        confirmText: "تغییر نقش",
        danger: next === "OWNER" || !next,
      });
      if (ok) await patch(save, { admin_role: next }, "نقش تغییر کرد.");
    });
    wrap.append(select, save);
  }
  return wrap.childNodes.length ? wrap : null;
}

function devicesTable(u, reload) {
  return table([
    { label: "نام دستگاه", cell: (d) => d.device_name },
    { label: "سیستم", cell: (d) => h("span", null, d.platform, d.app_version ? h("span", { class: "dim small ltr" }, ` v${d.app_version}`) : null) },
    { label: "وضعیت", cell: (d) => badge("device", d.status) },
    { label: "آخرین فعالیت", cell: (d) => fmtDate(d.last_seen_at) },
    { label: "افزوده‌شده", cell: (d) => fmtDate(d.created_at) },
    { label: "عملیات", class: "actions", cell: (d) => {
      if (d.status !== "ACTIVE" || !can("devices.revoke")) return h("span", { class: "dim" }, "—");
      const btn = h("button", { class: "btn sm danger", type: "button" }, "حذف دستگاه");
      btn.addEventListener("click", async () => {
        const ok = await confirmDialog({
          title: "حذف دستگاه",
          message: h("p", null, `دستگاه «${d.device_name}» حذف شود؟ نشست‌های این دستگاه بسته می‌شود و یک جای خالی در سقف دستگاه‌ها آزاد می‌شود.`),
          confirmText: "حذف دستگاه",
          danger: true,
        });
        if (!ok) return;
        await busy(btn, async () => {
          try {
            await api("DELETE", `/admin/users/${encodeURIComponent(u.id)}/devices/${encodeURIComponent(d.id)}`);
            toast("دستگاه حذف شد.");
            reload();
          } catch (err) { toastError(err); }
        });
      });
      return btn;
    } },
  ], u.devices, "دستگاهی ثبت نشده است.");
}

// ---------------------------------------------------------- subscriptions
function subscriptionsTable(rows, { showUser = true } = {}) {
  const columns = [];
  if (showUser) {
    columns.push({ label: "کاربر", cell: (s) => (can("users.read")
      ? h("a", { href: `#/users/${encodeURIComponent(s.user_id)}` }, h("span", { class: "ltr" }, s.username))
      : h("span", { class: "ltr" }, s.username)) });
  }
  columns.push(
    { label: "پلن", cell: (s) => s.plan_name },
    { label: "وضعیت", cell: (s) => h("span", null, badge("sub", s.status),
      s.provisioning_error ? h("div", { class: "dim small break" }, s.provisioning_error) : null) },
    { label: "ترافیک", cell: (s) => traffic(s.traffic_used_bytes, s.traffic_limit_bytes) },
    { label: "انقضا", cell: (s) => fmtDate(s.expire_at) },
    { label: "نام در پنل", cell: (s) => (s.panel_username ? h("code", null, s.panel_username) : h("span", { class: "dim" }, "—")) },
  );
  if (can("panels")) {
    columns.push({ label: "عملیات", class: "actions", cell: (s) => {
      if (!s.panel_username) return h("span", { class: "dim" }, "—");
      const btn = h("button", { class: "btn sm", type: "button" }, "دریافت مجدد کانفیگ");
      btn.addEventListener("click", () => busy(btn, async () => {
        try {
          await api("POST", `/admin/subscriptions/${encodeURIComponent(s.id)}/reprovision`);
          toast("دریافت مجدد کانفیگ‌ها در صف قرار گرفت.");
        } catch (err) { toastError(err); }
      }));
      return btn;
    } });
  }
  return table(columns, rows, "اشتراکی یافت نشد.");
}

let subsFilter = "";
async function pageSubscriptions(container, _segments, query) {
  const offset = Math.max(0, Number.parseInt(query.get("offset") || "0", 10) || 0);
  const limit = 50;
  const select = h("select", { "aria-label": "وضعیت" },
    [["", "همه"], ...Object.entries(BADGES.sub).map(([k, [l]]) => [k, l])].map(([v, l]) =>
      h("option", { value: v, selected: v === subsFilter }, l)));
  select.addEventListener("change", () => { subsFilter = select.value; go("#/subscriptions"); });

  const body = h("div", { class: "card" }, loading());
  container.append(pageHead("اشتراک‌ها", select), body);

  const qs = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (subsFilter) qs.set("status", subsFilter);
  const data = await api("GET", `/admin/subscriptions?${qs}`);
  if (!container.isConnected) return;
  clear(body).append(subscriptionsTable(data.items),
    pager(data.total, limit, offset, (next) => go(`#/subscriptions${next ? `?offset=${next}` : ""}`)));
}

// ----------------------------------------------------------------- panels
const PANEL_TYPES = [["PASARGUARD", "PasarGuard"], ["MARZBAN", "Marzban"], ["XUI", "3x-ui"], ["CUSTOM", "Custom"]];

async function pagePanels(container) {
  const body = h("div", null, loading());
  const load = async () => {
    const panels = await api("GET", "/admin/panels");
    if (!container.isConnected) return;
    clear(body);
    if (!panels.length) {
      body.append(h("div", { class: "card empty" }, "هنوز پنلی ثبت نشده است."));
      return;
    }
    for (const p of panels) body.append(panelCard(p, load));
  };
  const add = h("button", { class: "btn primary sm", type: "button" }, "ثبت پنل");
  add.addEventListener("click", () => registerPanel(load));
  container.append(pageHead("پنل‌ها", add, refreshButton(() => load().catch(toastError))), body);
  await load();
}

function panelCard(p, reload) {
  const test = h("button", { class: "btn sm", type: "button" }, "تست اتصال");
  test.addEventListener("click", () => busy(test, async () => {
    try {
      const r = await api("POST", `/admin/panels/${encodeURIComponent(p.id)}/test`);
      toast(r.reachable ? `اتصال به «${p.name}» برقرار است.` : `اتصال به «${p.name}» ناموفق بود: ${r.error || "نامشخص"}`, { error: !r.reachable });
      await reload();
    } catch (err) { toastError(err); }
  }));
  const groups = h("button", { class: "btn sm", type: "button" }, "گروه‌های پیش‌فرض");
  groups.addEventListener("click", () => busy(groups, () => chooseGroups(p, reload)));
  const creds = h("button", { class: "btn sm ghost", type: "button" }, "تغییر اطلاعات ورود");
  creds.addEventListener("click", () => replaceCredentials(p, reload));

  const defaults = p.default_group_ids || [];
  return h("div", { class: "card" },
    h("div", { class: "page-head" },
      h("h2", null, p.name, " ", h("span", { class: "badge" }, (PANEL_TYPES.find(([v]) => v === p.panel_type) || [null, p.panel_type])[1])),
      // A panel never tested has status ACTIVE by default; showing that as
      // "reachable" would claim something nobody has checked.
      p.last_checked_at ? badge("panel", p.status) : h("span", { class: "badge warn" }, "تست نشده")),
    h("dl", { class: "kv" },
      h("dt", null, "آدرس"), h("dd", null, h("code", { class: "break" }, p.base_url)),
      h("dt", null, "اطلاعات ورود"), h("dd", null, p.has_credentials ? "ثبت شده (رمزنگاری‌شده، قابل نمایش نیست)" : h("span", { class: "badge danger" }, "ثبت نشده")),
      h("dt", null, "بررسی TLS"), h("dd", null, p.verify_tls ? "فعال" : h("span", { class: "badge warn" }, "غیرفعال")),
      h("dt", null, "آخرین تست"), h("dd", null, fmtDate(p.last_checked_at)),
      h("dt", null, "گروه‌های پیش‌فرض"), h("dd", null, defaults.length
        ? h("span", { class: "num" }, defaults.join("، "))
        : h("span", { class: "badge warn" }, "انتخاب نشده — کاربران جدید کانفیگ نمی‌گیرند"))),
    p.last_error ? h("div", { class: "alert small" }, "آخرین خطا: ", p.last_error) : null,
    h("div", { class: "toolbar" }, test, groups, creds));
}

async function chooseGroups(panel, reload) {
  let groups;
  try {
    groups = await api("GET", `/admin/panels/${encodeURIComponent(panel.id)}/groups`);
  } catch (err) {
    toastError(err);
    return;
  }
  const checks = [];
  const list = groups.map((g) => {
    const unusable = g.is_disabled || g.inbound_count === 0;
    const box = h("input", { type: "checkbox", value: String(g.id), checked: g.is_default && !unusable, disabled: unusable });
    checks.push(box);
    const why = g.is_disabled ? "غیرفعال" : g.inbound_count === 0 ? "بدون اینباند" : `${numberFmt.format(g.inbound_count)} اینباند`;
    return h("label", { class: `check${unusable ? " disabled" : ""}` }, box,
      h("span", null, g.name, " ", h("span", { class: "dim small" }, "#", h("span", { class: "num" }, g.id), " · ", why)));
  });
  const errorHost = h("div");
  const save = h("button", { class: "btn primary", type: "button" }, "ذخیره");
  const cancel = h("button", { class: "btn ghost", type: "button" }, "انصراف");
  const { close } = openModal(`گروه‌های پیش‌فرض «${panel.name}»`, [
    h("p", { class: "dim small" }, "کاربران جدیدی که روی این پنل ساخته می‌شوند در این گروه‌ها قرار می‌گیرند. فهرست همین حالا از خود پنل خوانده شده است."),
    errorHost,
    groups.length ? list : h("div", { class: "empty" }, "این پنل گروهی ندارد."),
    h("div", { class: "actions-row" }, cancel, save),
  ]);
  cancel.addEventListener("click", close);
  save.addEventListener("click", () => busy(save, async () => {
    clear(errorHost);
    const ids = checks.filter((c) => c.checked).map((c) => Number(c.value));
    if (!ids.length) {
      errorHost.append(h("div", { class: "alert" }, "دست‌کم یک گروه انتخاب کنید."));
      return;
    }
    try {
      await api("PUT", `/admin/panels/${encodeURIComponent(panel.id)}/groups`, { group_ids: ids });
      toast("گروه‌های پیش‌فرض ذخیره شد.");
      close();
      await reload();
    } catch (err) { errorHost.append(errorBox(err)); }
  }));
}

const CREDENTIAL_FIELDS = [
  { name: "username", label: "نام کاربری پنل", attrs: { autocomplete: "off", dir: "ltr", maxlength: 128 } },
  { name: "password", label: "رمز عبور پنل", type: "password", attrs: { autocomplete: "new-password", dir: "ltr", maxlength: 256 } },
  { name: "api_key", label: "کلید API (در صورت نیاز پنل)", type: "password", attrs: { autocomplete: "new-password", dir: "ltr", maxlength: 512 } },
];

function registerPanel(reload) {
  formDialog({
    title: "ثبت پنل جدید",
    submitText: "ثبت و تست اتصال",
    fields: [
      { name: "name", label: "نام", required: true, attrs: { maxlength: 128 } },
      { name: "panel_type", label: "نوع پنل", type: "select", value: "PASARGUARD", options: PANEL_TYPES },
      { name: "base_url", label: "آدرس پنل", hint: "مثلاً https://panel.example.com:8000", attrs: { dir: "ltr", inputmode: "url", maxlength: 512 } },
      ...CREDENTIAL_FIELDS,
      { name: "verify_tls", label: "بررسی گواهی TLS (فقط برای گواهی خودامضا خاموش کنید)", type: "checkbox", value: true },
    ],
    onSubmit: async (v) => {
      const panel = await api("POST", "/admin/panels", {
        name: v.name,
        panel_type: v.panel_type,
        base_url: v.base_url,
        username: v.username || null,
        password: v.password || null,
        api_key: v.api_key || null,
        verify_tls: v.verify_tls,
      });
      toast("پنل ثبت شد؛ در حال تست اتصال…");
      try {
        const r = await api("POST", `/admin/panels/${encodeURIComponent(panel.id)}/test`);
        toast(r.reachable ? "اتصال برقرار است. اکنون گروه‌های پیش‌فرض را انتخاب کنید." : `اتصال ناموفق بود: ${r.error || "نامشخص"}`, { error: !r.reachable });
      } catch (err) { toastError(err); }
      await reload();
    },
  });
}

function replaceCredentials(panel, reload) {
  formDialog({
    title: `اطلاعات ورود «${panel.name}»`,
    submitText: "جایگزینی",
    fields: [
      ...CREDENTIAL_FIELDS,
    ],
    onSubmit: async (v) => {
      if (!v.password && !v.api_key) {
        throw new ApiError("رمز عبور یا کلید API را وارد کنید.", "VALIDATION_ERROR", 422);
      }
      // The endpoint takes the full panel shape; only the credentials are
      // applied, the rest is echoed back unchanged.
      await api("PATCH", `/admin/panels/${encodeURIComponent(panel.id)}/credentials`, {
        name: panel.name,
        panel_type: panel.panel_type,
        base_url: panel.base_url,
        username: v.username || null,
        password: v.password || null,
        api_key: v.api_key || null,
        verify_tls: panel.verify_tls,
      });
      toast("اطلاعات ورود جایگزین شد. اتصال را تست کنید.");
      await reload();
    },
  });
}

// ---------------------------------------------------------------- servers
async function pageServers(container) {
  const body = h("div", { class: "card" }, loading());
  const load = async () => {
    const [servers, panels] = await Promise.all([api("GET", "/admin/servers"), api("GET", "/admin/panels")]);
    if (!container.isConnected) return;
    const panelName = Object.fromEntries(panels.map((p) => [p.id, p.name]));
    // Health and load are not shown: nothing in the backend measures them
    // yet, and a column of default values would look like real monitoring.
    clear(body).append(table([
      { label: "نام", cell: (s) => s.name },
      { label: "آدرس", cell: (s) => h("code", null, `${s.host}:${s.port}`) },
      { label: "منطقه", cell: (s) => s.region || h("span", { class: "dim" }, "—") },
      { label: "پنل", cell: (s) => panelName[s.panel_id] || h("code", null, s.panel_id) },
      { label: "وضعیت", cell: (s) => badge("server", s.status) },
    ], servers, "هنوز سروری ثبت نشده است."));
  };
  container.append(pageHead("سرورها", refreshButton(() => load().catch(toastError))), body);
  await load();
}

// ------------------------------------------------------------------ audit
const ACTION_FA = {
  "order.confirm_manual_payment": "تأیید پرداخت",
  "order.cancel": "لغو سفارش",
  "order.reprovision": "ساخت مجدد سرویس",
  "subscription.refresh_configs": "دریافت مجدد کانفیگ",
  "plan.create": "ساخت پلن",
  "plan.update": "ویرایش پلن",
  "panel.set_groups": "تغییر گروه‌های پنل",
  "server.create": "ثبت سرور",
  "user.update": "تغییر حساب کاربر",
  "device.revoke": "حذف دستگاه",
  "admin.bootstrap_owner": "تعیین مالک اولیه",
  "topup.approve": "تأیید شارژ کیف پول",
  "topup.reject": "رد شارژ کیف پول",
  "wallet.adjust": "اصلاح دستی موجودی",
  "payments.settings_update": "تغییر تنظیمات پرداخت",
};

async function pageAudit(container, _segments, query) {
  const offset = Math.max(0, Number.parseInt(query.get("offset") || "0", 10) || 0);
  const limit = 50;
  const body = h("div", { class: "card" }, loading());
  container.append(pageHead("گزارش رویدادها"), body);
  const data = await api("GET", `/admin/audit?limit=${limit}&offset=${offset}`);
  if (!container.isConnected) return;
  clear(body).append(table([
    { label: "زمان", cell: (e) => fmtDate(e.created_at) },
    { label: "انجام‌دهنده", cell: (e) => (e.actor_username ? h("span", { class: "ltr" }, e.actor_username) : h("span", { class: "dim" }, "سیستم")) },
    { label: "عمل", cell: (e) => h("span", null, ACTION_FA[e.action] || "", ACTION_FA[e.action] ? " " : "", h("code", { class: "dim" }, e.action)) },
    { label: "موجودیت", cell: (e) => h("span", null, e.entity, e.entity_id ? h("code", { class: "dim small" }, ` ${e.entity_id.slice(0, 8)}…`) : null) },
    { label: "IP", cell: (e) => (e.ip_address ? h("code", null, e.ip_address) : h("span", { class: "dim" }, "—")) },
    { label: "جزئیات", cell: (e) => (e.metadata
      ? h("details", null, h("summary", null, "نمایش"), h("pre", { class: "json" }, JSON.stringify(e.metadata, null, 2)))
      : h("span", { class: "dim" }, "—")) },
  ], data.items, "رویدادی ثبت نشده است."),
  pager(data.total, limit, offset, (next) => go(`#/audit${next ? `?offset=${next}` : ""}`)));
}

// --------------------------------------------------------------- payments
const METHOD_FA = { CARD: "کارت‌به‌کارت", CRYPTO: "ارز دیجیتال" };
let topupsFilter = "PENDING";
const TOPUP_FILTERS = [
  ["PENDING", "در انتظار بررسی"], ["", "همه"], ["APPROVED", "تأییدشده"],
  ["REJECTED", "ردشده"], ["CANCELLED", "لغو توسط کاربر"],
];

async function pageTopups(container) {
  const body = h("div", { class: "card" }, loading());
  const select = h("select", { "aria-label": "وضعیت" },
    TOPUP_FILTERS.map(([v, l]) => h("option", { value: v, selected: v === topupsFilter }, l)));

  const load = async () => {
    const qs = new URLSearchParams({ limit: "100" });
    if (topupsFilter) qs.set("status", topupsFilter);
    const data = await api("GET", `/admin/topups?${qs}`);
    if (!container.isConnected) return;
    clear(body).append(table([
      { label: "کاربر", cell: (t) => h("span", null, userLink(t.username),
        h("div", { class: "dim small" }, "موجودی: ", fmtMoney(t.wallet_balance, t.currency))) },
      { label: "روش", cell: (t) => h("span", null, METHOD_FA[t.method] || t.method,
        t.network ? h("div", { class: "dim small ltr" }, `${t.asset} · ${t.network}`) : null) },
      { label: "مبلغ", cell: (t) => h("span", null, fmtMoney(t.amount, t.currency),
        t.crypto_amount ? h("div", { class: "dim small ltr" }, `${t.crypto_amount} ${t.asset}`) : null,
        t.credited_amount && t.credited_amount !== t.amount
          ? h("div", { class: "small" }, "واریزشده: ", fmtMoney(t.credited_amount, t.currency)) : null) },
      { label: "رسید", cell: (t) => h("span", null, shortRef(t.reference),
        t.reference_seen > 1 ? h("div", { class: "badge danger" }, `${t.reference_seen} بار ثبت شده`) : null,
        t.payer_note ? h("div", { class: "dim small" }, t.payer_note) : null) },
      { label: "وضعیت", cell: (t) => h("span", null, badge("topup", t.status),
        t.order_id ? h("div", { class: "dim small" }, "برای یک سفارش") : null,
        t.reject_reason ? h("div", { class: "dim small" }, t.reject_reason) : null) },
      { label: "زمان", cell: (t) => fmtDate(t.created_at) },
      { label: "عملیات", class: "actions", cell: (t) => topupActions(t, load) },
    ], data.items, topupsFilter === "PENDING" ? "پرداختی در انتظار بررسی نیست." : "موردی یافت نشد."));
  };

  select.addEventListener("change", () => {
    topupsFilter = select.value;
    clear(body).append(loading());
    load().catch((err) => { if (container.isConnected) clear(body).append(errorBox(err)); });
  });
  container.append(pageHead("پرداخت‌ها", select, refreshButton(() => load().catch(toastError))), body);
  await load();
}

/** A long transaction hash, shortened for the table; the full value is in
 *  the tooltip and in the approve dialog, and a click copies it. */
function shortRef(ref) {
  const text = ref.length > 22 ? `${ref.slice(0, 10)}…${ref.slice(-8)}` : ref;
  const el = h("code", { title: ref, class: "ltr" }, text);
  el.addEventListener("click", () => {
    if (navigator.clipboard) navigator.clipboard.writeText(ref).then(() => toast("کپی شد."), () => {});
  });
  return el;
}

function topupDetails(t) {
  return h("dl", { class: "kv" },
    h("dt", null, "کاربر"), h("dd", null, h("span", { class: "ltr" }, t.username)),
    h("dt", null, "روش"), h("dd", null, METHOD_FA[t.method] || t.method),
    h("dt", null, "مبلغ اعلام‌شده"), h("dd", null, fmtMoney(t.amount, t.currency)),
    t.crypto_amount ? h("dt", null, "مقدار ارز") : null,
    t.crypto_amount ? h("dd", null, h("span", { class: "ltr" }, `${t.crypto_amount} ${t.asset} (${t.network}) · نرخ ${t.rate}`)) : null,
    h("dt", null, "مقصد"), h("dd", null, h("code", null, t.destination)),
    h("dt", null, t.method === "CRYPTO" ? "هش تراکنش" : "شماره پیگیری"), h("dd", null, h("code", null, t.reference)),
    t.payer_note ? h("dt", null, "توضیح کاربر") : null,
    t.payer_note ? h("dd", null, t.payer_note) : null);
}

function topupActions(t, reload) {
  if (t.status !== "PENDING") return h("span", { class: "dim" }, "—");
  const approve = h("button", { class: "btn sm primary", type: "button" }, "تأیید");
  approve.addEventListener("click", () => {
    formDialog({
      title: "تأیید پرداخت و شارژ کیف پول",
      submitText: "تأیید و شارژ",
      fields: [
        { name: "amount", label: "مبلغی که واقعاً دریافت شد (تومان)", value: String(t.amount).split(".")[0],
          attrs: { inputmode: "numeric", dir: "ltr" },
          hint: t.method === "CRYPTO"
            ? "تراکنش را روی شبکه بررسی کنید: مقصد، مقدار و تعداد تأییدها."
            : "واریز را با شماره پیگیری در صورت‌حساب بانک بررسی کنید." },
      ],
      onSubmit: async (v) => {
        const amount = toInt(v.amount);
        if (!amount || amount <= 0) throw new ApiError("مبلغ معتبر نیست.", "VALIDATION_ERROR", 422);
        const r = await api("POST", `/admin/topups/${encodeURIComponent(t.id)}/approve`, { amount: String(amount) });
        toast(r.paid_order_id
          ? "کیف پول شارژ شد، سفارش کاربر پرداخت شد و ساخت سرویس در صف قرار گرفت."
          : "کیف پول کاربر شارژ شد.");
        await reload();
      },
    });
    // Show what is being approved above the amount field.
    const modal = document.querySelector(".modal form");
    if (modal) modal.prepend(topupDetails(t));
  });
  const reject = h("button", { class: "btn sm danger", type: "button" }, "رد");
  reject.addEventListener("click", () => {
    formDialog({
      title: "رد پرداخت",
      submitText: "رد کن",
      fields: [{ name: "reason", label: "دلیل (به کاربر نمایش داده می‌شود)", value: "واریزی با این مشخصات پیدا نشد.", required: true }],
      onSubmit: async (v) => {
        await api("POST", `/admin/topups/${encodeURIComponent(t.id)}/reject`, { reason: v.reason });
        toast("پرداخت رد شد.");
        await reload();
      },
    });
  });
  return [approve, reject];
}

function walletCard(u) {
  const body = h("div", null, loading());
  const card = h("div", { class: "card" }, h("h2", null, "کیف پول"), body);
  const load = async () => {
    const w = await api("GET", `/admin/users/${encodeURIComponent(u.id)}/wallet`);
    if (!card.isConnected) return;
    const adjust = h("button", { class: "btn sm", type: "button" }, "اصلاح دستی موجودی");
    adjust.addEventListener("click", () => formDialog({
      title: "اصلاح دستی موجودی",
      submitText: "ثبت",
      fields: [
        { name: "amount", label: "مبلغ (تومان) — مثبت برای افزایش، منفی برای کاهش", attrs: { dir: "ltr", inputmode: "numeric" }, required: true },
        { name: "note", label: "دلیل (در گزارش رویدادها ثبت می‌شود)", required: true },
      ],
      onSubmit: async (v) => {
        const raw = normaliseDigits(v.amount).replace(/[,\s]/g, "");
        if (!/^-?\d+$/.test(raw) || Number(raw) === 0) throw new ApiError("مبلغ معتبر نیست.", "VALIDATION_ERROR", 422);
        await api("POST", `/admin/users/${encodeURIComponent(u.id)}/wallet/adjust`, { amount: raw, note: v.note });
        toast("موجودی اصلاح شد.");
        clear(body).append(loading());
        await load();
      },
    }));
    clear(body).append(
      h("div", { class: "toolbar" }, h("strong", null, "موجودی: ", fmtMoney(w.balance, "IRT")), adjust),
      table([
        { label: "نوع", cell: (t) => badge("wallet", t.kind) },
        { label: "مبلغ", cell: (t) => fmtMoney(t.amount, "IRT") },
        { label: "موجودی بعد", cell: (t) => fmtMoney(t.balance_after, "IRT") },
        { label: "توضیح", cell: (t) => t.note || h("span", { class: "dim" }, "—") },
        { label: "زمان", cell: (t) => fmtDate(t.created_at) },
      ], w.transactions, "تراکنشی ثبت نشده است."));
  };
  load().catch((err) => { if (card.isConnected) clear(body).append(errorBox(err)); });
  return card;
}

const NETWORKS = [["TRC20", "TRC20 (Tron)"], ["BEP20", "BEP20 (BSC)"], ["ERC20", "ERC20 (Ethereum)"], ["TON", "TON"]];
const ASSETS = [["USDT", "USDT"], ["TRX", "TRX"], ["TON", "TON"], ["USDC", "USDC"]];

async function pagePaymentSettings(container) {
  container.append(pageHead("تنظیمات پرداخت"), loading());
  const s = await api("GET", "/admin/payment-settings");
  if (!container.isConnected) return;

  const errorHost = h("div");
  const field = (label, input, hint) => h("div", { class: "field" }, h("label", null, label), input, hint ? h("span", { class: "hint" }, hint) : null);
  const text = (value, attrs = {}) => { const i = h("input", { type: "text", ...attrs }); i.value = value ?? ""; return i; };
  const check = (value) => h("input", { type: "checkbox", checked: Boolean(value) });
  const area = (value) => { const t = h("textarea", { rows: 2 }); t.value = value ?? ""; return t; };

  const minTopup = text(String(s.min_topup).split(".")[0], { dir: "ltr", inputmode: "numeric" });
  const maxTopup = text(String(s.max_topup).split(".")[0], { dir: "ltr", inputmode: "numeric" });

  const cardEnabled = check(s.card.enabled);
  const cardNumber = text(s.card.number, { dir: "ltr", inputmode: "numeric", placeholder: "16 رقم" });
  const cardHolder = text(s.card.holder);
  const cardBank = text(s.card.bank);
  const cardInstr = area(s.card.instructions);

  const cryptoEnabled = check(s.crypto.enabled);
  const cryptoInstr = area(s.crypto.instructions);
  const walletsHost = h("div");
  const walletRows = [];
  const addWallet = (w = { network: "TRC20", asset: "USDT", address: "", rate: "" }) => {
    const network = h("select", null, NETWORKS.map(([v, l]) => h("option", { value: v, selected: v === w.network }, l)));
    const asset = h("select", null, ASSETS.map(([v, l]) => h("option", { value: v, selected: v === w.asset }, l)));
    const address = text(w.address, { dir: "ltr", placeholder: "آدرس کیف پول" });
    const rate = text(w.rate ? String(w.rate).split(".")[0] : "", { dir: "ltr", inputmode: "numeric", placeholder: "تومان به ازای هر واحد" });
    const remove = h("button", { class: "btn sm danger", type: "button" }, "حذف");
    const row = h("div", { class: "card" },
      h("div", { class: "toolbar" }, network, asset, remove),
      field("آدرس", address, "آدرس را دوباره با کیف پول خود مقایسه کنید؛ پرداخت به آدرس اشتباه برگشت‌پذیر نیست."),
      field("نرخ (تومان برای هر 1 واحد)", rate, "نرخ را خودتان به‌روز نگه دارید؛ برنامه نرخ لحظه‌ای را حدس نمی‌زند."));
    const entry = { network, asset, address, rate, row };
    remove.addEventListener("click", () => { row.remove(); walletRows.splice(walletRows.indexOf(entry), 1); });
    walletRows.push(entry);
    walletsHost.append(row);
  };
  for (const w of s.crypto.wallets) addWallet(w);
  const addBtn = h("button", { class: "btn sm", type: "button" }, "افزودن کیف پول");
  addBtn.addEventListener("click", () => addWallet());

  const save = h("button", { class: "btn primary", type: "button" }, "ذخیره تنظیمات");
  save.addEventListener("click", async () => {
    const body = {
      currency: "IRT",
      min_topup: String(toInt(minTopup.value) || 0),
      max_topup: String(toInt(maxTopup.value) || 0),
      card: {
        enabled: cardEnabled.checked,
        number: normaliseDigits(cardNumber.value).replace(/[\s-]/g, ""),
        holder: cardHolder.value.trim(),
        bank: cardBank.value.trim(),
        instructions: cardInstr.value.trim(),
      },
      crypto: {
        enabled: cryptoEnabled.checked,
        instructions: cryptoInstr.value.trim(),
        wallets: walletRows.map((w) => ({
          network: w.network.value, asset: w.asset.value,
          address: w.address.value.trim(), rate: String(toInt(w.rate.value) || 0),
        })),
      },
    };
    const ok = await confirmDialog({
      title: "ذخیره تنظیمات پرداخت",
      message: [
        h("p", null, "از این پس مشتری‌ها پول را به این مقصدها واریز می‌کنند:"),
        h("dl", { class: "kv" },
          h("dt", null, "کارت"), h("dd", null, body.card.enabled ? h("code", null, body.card.number) : "غیرفعال"),
          ...body.crypto.wallets.flatMap((w) => [h("dt", null, `${w.asset} ${w.network}`), h("dd", null, h("code", null, w.address))])),
        h("p", { class: "alert warn" }, "شماره کارت و آدرس‌ها را یک بار دیگر بررسی کنید."),
      ],
      confirmText: "ذخیره",
    });
    if (!ok) return;
    await busy(save, async () => {
      clear(errorHost);
      try {
        await api("PUT", "/admin/payment-settings", body);
        toast("تنظیمات پرداخت ذخیره شد.");
      } catch (err) { errorHost.append(errorBox(err)); }
    });
  });

  clear(container).append(
    pageHead("تنظیمات پرداخت"),
    h("p", { class: "dim small" }, "این اطلاعات در اپ به مشتری نمایش داده می‌شود. فقط مالک می‌تواند آن را تغییر دهد و هر تغییر در گزارش رویدادها ثبت می‌شود."),
    errorHost,
    h("div", { class: "card" }, h("h2", null, "محدوده مبلغ شارژ (تومان)"),
      field("حداقل", minTopup), field("حداکثر", maxTopup)),
    h("div", { class: "card" }, h("h2", null, "کارت‌به‌کارت"),
      h("label", { class: "check" }, cardEnabled, h("span", null, "فعال")),
      field("شماره کارت", cardNumber), field("نام صاحب کارت", cardHolder), field("نام بانک", cardBank),
      field("توضیحات برای مشتری", cardInstr)),
    h("div", { class: "card" }, h("h2", null, "ارز دیجیتال"),
      h("label", { class: "check" }, cryptoEnabled, h("span", null, "فعال")),
      walletsHost, addBtn, field("توضیحات برای مشتری", cryptoInstr)),
    h("div", { class: "actions-row" }, save));
}

// ------------------------------------------------------------------- boot
async function boot() {
  window.addEventListener("hashchange", route);
  if (readRT()) {
    clear(document.getElementById("app")).append(loading());
    if (await refreshSession()) {
      try {
        await loadMe();
        route();
        return;
      } catch (err) {
        if (err instanceof ApiError && err.status === 403) {
          await signOut({ silent: true });
          renderLogin("این حساب مدیر نیست.");
          return;
        }
        if (!(err instanceof ApiError && err.code === "SESSION_ENDED")) renderLogin(describeError(err));
        return;
      }
    }
  }
  renderLogin();
}

boot();
