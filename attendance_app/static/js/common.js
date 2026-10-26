/* Shared UI runtime: API client, toasts, modals, navigation, clock, helpers. */
(function () {
  "use strict";

  const API_HEADER = { "X-Requested-With": "StudentAttendance", Accept: "application/json" };
  const THEME_KEY = "attendance-theme";

  class ApiError extends Error {
    constructor(message, code, status, details) {
      super(message);
      this.name = "ApiError";
      this.code = code || "error";
      this.status = status || 0;
      this.details = details;
    }
  }

  function icon(name, cls) {
    return `<svg class="ico ${cls || ""}" aria-hidden="true"><use href="#i-${name}"></use></svg>`;
  }

  function escapeHtml(value) {
    return String(value === null || value === undefined ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  const ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹";
  const ARABIC_DIGIT_RE = /[\u0660-\u0669\u06F0-\u06F9]/;

  function latinDigits(value) {
    const text = String(value === null || value === undefined ? "" : value);
    if (!ARABIC_DIGIT_RE.test(text)) return text;
    return text.replace(/[\u0660-\u0669\u06F0-\u06F9]/g, (digit) => {
      const index = ARABIC_DIGITS.indexOf(digit);
      return index < 0 ? digit : String(index % 10);
    });
  }

  function translateUi(value) {
    const text = latinDigits(value);
    return typeof window.translateUiText === "function" ? latinDigits(window.translateUiText(text)) : text;
  }

  function initials(first, last) {
    const a = String(first || "").trim();
    const b = String(last || "").trim();
    if (a && b) return (a[0] + b[0]).toUpperCase();
    const single = (a || b || "؟").trim();
    return single.slice(0, 2).toUpperCase();
  }

  function formatInt(value) {
    const n = Number(value || 0);
    return Number.isFinite(n) ? new Intl.NumberFormat("en-US").format(n) : "0";
  }

  function formatDate(iso) {
    if (!iso) return "—";
    const value = String(iso).slice(0, 10);
    const parts = value.split("-");
    if (parts.length !== 3) return value;
    return `${parts[2]}/${parts[1]}/${parts[0]}`;
  }

  function todayISO() {
    const now = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
  }

  function apiUrl(path, params) {
    const url = new URL(path, window.location.origin);
    if (params) {
      Object.entries(params).forEach(([key, value]) => {
        if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, value);
      });
    }
    return url.pathname + url.search;
  }

  async function api(path, options = {}) {
    const init = { method: options.method || "GET", headers: { ...API_HEADER } };
    if (options.form instanceof FormData) {
      init.body = options.form;
    } else if (options.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(options.body);
    }
    let response;
    try {
      response = await fetch(path, init);
    } catch (error) {
      throw new ApiError("تعذر الاتصال بالخادم المحلي. تحقق من أن النظام ما زال يعمل.", "network", 0);
    }
    let data = null;
    try {
      data = await response.json();
    } catch (error) {
      data = null;
    }
    if (!response.ok || !data || data.ok === false) {
      if (response.status === 401) {
        const next = window.location.pathname + window.location.search;
        window.location.href = `/login?next=${encodeURIComponent(next)}`;
        throw new ApiError("انتهت الجلسة. جارٍ إعادة تسجيل الدخول…", "session", 401);
      }
      const err = (data && data.error) || {};
      let message = err.message || "حدث خطأ غير متوقع. أعد المحاولة.";
      if (response.status === 413) message = "حجم الملف كبير جدًا.";
      if (response.status === 403) message = err.message || "ليست لديك صلاحية لتنفيذ هذه العملية.";
      throw new ApiError(message, err.code, response.status, err.details);
    }
    return data;
  }

  const toastIcons = { success: "check", error: "warning", warning: "warning", info: "sparkle" };
  const toastTitles = { success: "تمت العملية", error: "تعذر إتمام العملية", warning: "تنبيه", info: "معلومة" };

  function toast(message, type = "success", title) {
    const stack = document.getElementById("toast-stack");
    if (!stack) return;
    const kind = toastIcons[type] ? type : "info";
    const node = document.createElement("div");
    node.className = `toast toast--${kind}`;
    node.setAttribute("role", "status");
    node.innerHTML = `
      <div class="toast-icon">${icon(toastIcons[kind])}</div>
      <div class="toast-body"><strong>${escapeHtml(title || toastTitles[kind])}</strong><p>${escapeHtml(message || "")}</p></div>
      <button class="toast-close" type="button" aria-label="إغلاق">${icon("close")}</button>`;
    const dismiss = () => {
      if (node.classList.contains("is-leaving")) return;
      node.classList.add("is-leaving");
      window.setTimeout(() => node.remove(), 220);
    };
    node.querySelector(".toast-close").addEventListener("click", dismiss);
    stack.appendChild(node);
    window.setTimeout(dismiss, kind === "error" ? 7000 : 4500);
  }

  function setBusy(button, busy) {
    if (!button) return;
    if (busy) {
      button.dataset.originalDisabled = button.disabled ? "1" : "";
      button.disabled = true;
      button.classList.add("is-loading");
    } else {
      button.disabled = button.dataset.originalDisabled === "1";
      button.classList.remove("is-loading");
      delete button.dataset.originalDisabled;
    }
  }

  let activeModal = null;

  function closeModal(result) {
    if (!activeModal) return;
    const { backdrop, resolve, onClose, previousFocus } = activeModal;
    activeModal = null;
    backdrop.style.opacity = "0";
    backdrop.style.transition = "opacity 150ms ease";
    window.setTimeout(() => backdrop.remove(), 150);
    document.body.style.overflow = "";
    if (previousFocus && typeof previousFocus.focus === "function") previousFocus.focus();
    if (onClose) onClose(result);
    if (resolve) resolve(result);
  }

  /**
   * openModal({ title, subtitle, body, footer, size, danger, icon, onMount, onClose })
   * Returns { element, close }.
   */
  function openModal(config = {}) {
    if (activeModal) closeModal(null);
    const previousFocus = document.activeElement;
    const backdrop = document.createElement("div");
    backdrop.className = "modal-backdrop";
    const iconName = config.icon || (config.danger ? "warning" : "sparkle");
    const footer = config.footer !== undefined ? config.footer : `
      <button class="btn btn-secondary" type="button" data-modal-close>إغلاق</button>`;
    backdrop.innerHTML = `
      <div class="modal ${config.size === "lg" ? "modal-lg" : config.size === "xl" ? "modal-xl" : ""} ${config.danger ? "modal--danger" : ""}"
           role="dialog" aria-modal="true" aria-labelledby="modal-title-${Date.now()}">
        <div class="modal-header">
          <div class="modal-title-row">
            <div class="modal-icon">${icon(iconName)}</div>
            <div><h2 id="modal-title-${Date.now()}">${escapeHtml(config.title || "نافذة")}</h2>
            ${config.subtitle ? `<p>${escapeHtml(config.subtitle)}</p>` : ""}</div>
          </div>
          <button class="icon-btn modal-close" type="button" data-modal-close aria-label="إغلاق">${icon("close")}</button>
        </div>
        <div class="modal-body">${config.body || ""}</div>
        ${footer ? `<div class="modal-footer">${footer}</div>` : ""}
      </div>`;
    document.getElementById("modal-root").appendChild(backdrop);
    document.body.style.overflow = "hidden";
    const modal = backdrop.querySelector(".modal");
    const close = (result) => closeModal(result);
    activeModal = { backdrop, resolve: config.resolve || null, onClose: config.onClose || null, previousFocus };
    backdrop.addEventListener("click", (event) => {
      if (event.target === backdrop && config.dismissible !== false) close(null);
    });
    backdrop.querySelectorAll("[data-modal-close]").forEach((btn) => {
      btn.addEventListener("click", () => close(config.closeValue !== undefined ? config.closeValue : null));
    });
    if (config.onMount) config.onMount(modal, { close });
    const focusTarget = modal.querySelector("[autofocus], .modal-body input:not([type=hidden]), .modal-body select, .modal-body textarea, .modal-footer .btn-primary");
    if (focusTarget) window.setTimeout(() => focusTarget.focus(), 60);
    return { element: modal, close };
  }

  function confirmDialog(options = {}) {
    return new Promise((resolve) => {
      openModal({
        title: options.title || "تأكيد العملية",
        subtitle: options.subtitle || "",
        body: `<div class="info-banner ${options.danger ? "is-danger" : "is-warning"}">${icon(options.danger ? "warning" : "shield-check")}<div>${escapeHtml(options.message || "هل تريد المتابعة؟")}</div></div>`,
        danger: Boolean(options.danger),
        icon: options.danger ? "warning" : "shield-check",
        footer: `
          <button class="btn btn-secondary" type="button" data-modal-close>إلغاء</button>
          <button class="btn ${options.danger ? "btn-danger" : "btn-primary"}" type="button" data-confirm>${escapeHtml(options.confirmLabel || "تأكيد")}</button>`,
        resolve,
        closeValue: false,
        onMount(modal, { close }) {
          modal.querySelector("[data-confirm]").addEventListener("click", () => {
            closeModal(true);
          });
        },
      });
    });
  }

  function pagination(container, options = {}) {
    if (!container) return;
    const page = Math.max(1, Number(options.page) || 1);
    const pages = Math.max(1, Number(options.pages) || 1);
    const total = Number(options.total) || 0;
    const onChange = options.onChange || (() => {});
    if (pages <= 1) {
      container.innerHTML = total ? `<span>${translateUi(`إجمالي ${formatInt(total)} سجل`)}</span>` : "";
      return;
    }
    let buttons = "";
    const add = (label, target, opts = {}) => {
      buttons += `<button class="page-btn ${opts.active ? "is-active" : ""}" type="button" data-page="${target}" ${opts.disabled ? "disabled" : ""}>${label}</button>`;
    };
    add(icon("chevron-right", "pager-prev"), page - 1, { disabled: page <= 1 });
    const windowStart = Math.max(1, Math.min(page - 1, pages - 2));
    const windowEnd = Math.min(pages, Math.max(page + 1, 3));
    if (windowStart > 1) add("1", 1, { active: page === 1 });
    if (windowStart > 2) buttons += `<span class="muted tiny">…</span>`;
    for (let i = windowStart; i <= windowEnd; i += 1) add(String(i), i, { active: i === page });
    if (windowEnd < pages - 1) buttons += `<span class="muted tiny">…</span>`;
    if (windowEnd < pages) add(String(pages), pages, { active: page === pages });
    add(icon("chevron-left", "pager-next"), page + 1, { disabled: page >= pages });
    container.innerHTML = `<span>${translateUi(`إجمالي ${formatInt(total)} سجل`)} — ${translateUi(`صفحة ${page} من ${pages}`)}</span><div class="pagination-controls">${buttons}</div>`;
    container.querySelectorAll("[data-page]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const target = Number(btn.dataset.page);
        if (target && target !== page) onChange(target);
      });
    });
  }

  const badgeMap = {
    PRESENT: ["badge--present", "حاضر"],
    LATE: ["badge--late", "متأخر"],
    ABSENT: ["badge--absent", "غائب"],
    EXCUSED: ["badge--excused", "غياب مبرر"],
    PENDING: ["badge--pending", "في الانتظار"],
    SENT: ["badge--sent", "تم الإرسال"],
    FAILED: ["badge--failed", "فشل"],
    CANCELLED: ["badge--cancelled", "ملغاة"],
  };

  function statusBadge(status, label) {
    const conf = badgeMap[status] || ["", label || status || "—"];
    return `<span class="badge ${conf[0]}"><i class="badge-dot"></i>${escapeHtml(label || conf[1])}</span>`;
  }

  function avatar(student, extraClass) {
    const cls = `avatar ${extraClass || ""}`.trim();
    const name = student.full_name || `${student.first_name || ""} ${student.last_name || ""}`.trim();
    const parts = name.split(/\s+/).filter(Boolean);
    const first = student.first_name || parts[0] || "";
    const last = student.last_name || (parts.length > 1 ? parts[parts.length - 1] : "");
    const photo = student.photo_url;
    const fallback = escapeHtml(initials(first, last));
    if (photo) {
      return `<div class="${cls}">${fallback}<img data-avatar-img src="${escapeHtml(photo)}" alt="${escapeHtml(name)}" loading="lazy"></div>`;
    }
    return `<div class="${cls}" aria-hidden="true">${fallback}</div>`;
  }

  function emptyState(iconName, title, text, actionHtml) {
    return `<div class="empty-state">
      <div class="empty-icon">${icon(iconName || "database")}</div>
      <strong>${escapeHtml(title || "لا توجد بيانات")}</strong>
      ${text ? `<p>${escapeHtml(text)}</p>` : ""}
      ${actionHtml || ""}
    </div>`;
  }

  function debounce(fn, delay = 300) {
    let timer = null;
    return function debounced(...args) {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => fn.apply(this, args), delay);
    };
  }

  function formValues(form) {
    const values = {};
    new FormData(form).forEach((value, key) => {
      if (values[key] !== undefined) {
        if (!Array.isArray(values[key])) values[key] = [values[key]];
        values[key].push(value);
      } else {
        values[key] = value;
      }
    });
    form.querySelectorAll('input[type="checkbox"][name]').forEach((cb) => {
      values[cb.name] = cb.checked;
    });
    return values;
  }

  function download(url) {
    const link = document.createElement("a");
    link.href = url;
    link.rel = "noopener";
    document.body.appendChild(link);
    link.click();
    link.remove();
  }

  /* ---------- Audio feedback ---------- */

  let audioContext = null;
  function beep(kind = "success") {
    try {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (!AudioCtx) return;
      if (!audioContext) audioContext = new AudioCtx();
      if (audioContext.state === "suspended") audioContext.resume();
      const now = audioContext.currentTime;
      const sequences = {
        success: [[880, 0, 0.09, "sine"], [1320, 0.11, 0.13, "sine"]],
        late: [[660, 0, 0.12, "triangle"], [520, 0.16, 0.16, "triangle"]],
        error: [[220, 0, 0.16, "square"], [180, 0.18, 0.22, "square"]],
        scan: [[1200, 0, 0.045, "sine"]],
      };
      (sequences[kind] || sequences.success).forEach(([freq, offset, duration, type]) => {
        const osc = audioContext.createOscillator();
        const gain = audioContext.createGain();
        osc.type = type;
        osc.frequency.setValueAtTime(freq, now + offset);
        gain.gain.setValueAtTime(0.0001, now + offset);
        gain.gain.exponentialRampToValueAtTime(0.09, now + offset + 0.015);
        gain.gain.exponentialRampToValueAtTime(0.0001, now + offset + duration);
        osc.connect(gain).connect(audioContext.destination);
        osc.start(now + offset);
        osc.stop(now + offset + duration + 0.02);
      });
    } catch (error) {
      /* audio is optional */
    }
  }

  function flash(kind = "success") {
    const layer = document.getElementById("flash-layer");
    if (!layer) return;
    layer.className = `flash-layer is-active is-${kind === "late" ? "late" : kind === "error" ? "error" : "success"}`;
    window.setTimeout(() => {
      layer.className = "flash-layer";
    }, 320);
  }

  /* ---------- Theme / navigation / clock ---------- */

  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", theme === "dark" ? "#08110e" : "#0e7c66");
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch (error) {
      /* storage can be unavailable */
    }
  }

  function toggleTheme() {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(next);
    window.dispatchEvent(new CustomEvent("app:themechange", { detail: { theme: next } }));
  }

  function closeSidebar() {
    document.body.classList.remove("sidebar-open");
  }

  function shutdown() {
    confirmDialog({
      title: "إغلاق النظام",
      message: "سيتم إيقاف الخادم المحلي. تأكد من عدم وجود عمليات مسح أو إرسال رسائل جارية. هل تريد المتابعة؟",
      danger: true,
      confirmLabel: "إغلاق النظام",
    }).then(async (confirmed) => {
      if (!confirmed) return;
      try {
        await api("/api/system/shutdown", { method: "POST" });
        toast("تم إيقاف الخادم. يمكنك إغلاق النافذة الآن.", "info", "إلى اللقاء");
        const backdrop = document.createElement("div");
        backdrop.className = "modal-backdrop";
        backdrop.innerHTML = `<div class="modal" style="width:min(420px,100%)"><div class="modal-body text-center" style="display:grid;gap:10px;padding:34px"><div style="justify-self:center;display:grid;place-items:center;width:64px;height:64px;border-radius:50%;background:var(--success-soft);color:var(--success)">${icon("check")}</div><h2 style="font-size:18px">تم إيقاف النظام</h2><p class="muted">أُغلقت قاعدة البيانات بأمان. يمكنك إغلاق هذه النافذة.</p></div></div>`;
        document.getElementById("modal-root").appendChild(backdrop);
      } catch (error) {
        toast(error.message, "warning");
      }
    });
  }

  function westernizeField(input) {
    if (!input || typeof input.value !== "string") return;
    const next = latinDigits(input.value);
    if (next === input.value) return;
    const start = input.selectionStart;
    const end = input.selectionEnd;
    input.value = next;
    try {
      input.setSelectionRange(start, end);
    } catch (error) {
      /* inputs without selection support (number/date) */
    }
  }

  function bindDigitInputs() {
    const selector = "input[type='text'], input[type='tel'], input[type='search'], input[type='number'], textarea";
    const handler = (event) => {
      const target = event.target;
      if (target && target.matches && target.matches(selector)) westernizeField(target);
    };
    document.addEventListener("input", handler, true);
    document.addEventListener("change", handler, true);
  }

  function initShell() {
    bindDigitInputs();
    document.querySelectorAll("[data-theme-toggle]").forEach((btn) => btn.addEventListener("click", toggleTheme));
    document.addEventListener("error", (event) => {
      if (event.target && event.target.matches && event.target.matches("[data-avatar-img]")) {
        event.target.remove();
      }
    }, true);
    document.querySelectorAll("[data-sidebar-open]").forEach((btn) => btn.addEventListener("click", () => document.body.classList.add("sidebar-open")));
    document.querySelectorAll("[data-sidebar-close]").forEach((btn) => btn.addEventListener("click", closeSidebar));
    document.querySelectorAll("[data-action='shutdown']").forEach((btn) => btn.addEventListener("click", shutdown));
    document.addEventListener("keydown", (event) => {
      if (event.key === "F2") {
        event.preventDefault();
        if (document.body.dataset.page === "gate") {
          window.dispatchEvent(new CustomEvent("app:focus-gate"));
        } else {
          window.location.href = "/gate";
        }
      }
      if (event.key === "Escape") {
        closeSidebar();
        if (activeModal) closeModal(null);
      }
    });
    const clock = document.querySelector("[data-live-clock]");
    if (clock) {
      const update = () => {
        clock.textContent = new Date().toLocaleTimeString("en-GB", {
          hour: "2-digit", minute: "2-digit", second: "2-digit", numberingSystem: "latn",
        });
      };
      update();
      window.setInterval(update, 1000);
    }
    window.addEventListener("resize", () => {
      if (window.innerWidth > 1060) closeSidebar();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initShell);
  } else {
    initShell();
  }

  window.App = {
    ApiError,
    api,
    apiUrl,
    icon,
    escapeHtml,
    latinDigits,
    t: translateUi,
    initials,
    formatInt,
    formatDate,
    todayISO,
    toast,
    confirm: confirmDialog,
    openModal,
    closeModal: () => closeModal(null),
    pagination,
    statusBadge,
    avatar,
    emptyState,
    debounce,
    formValues,
    setBusy,
    download,
    beep,
    flash,
    applyTheme,
    toggleTheme,
    closeSidebar,
  };
})();
