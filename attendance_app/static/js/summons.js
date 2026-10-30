/* Summons desk: find a student, write a guardian summons and print a signed receipt. */
(function () {
  "use strict";

  const A = window.App;
  const TEMPLATE_KEY = "summonsTemplate";
  const TEMPLATE_VALUES = ["full", "medium", "small"];
  const SCAN_DIGITS = {
    "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4",
    "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9",
    "۰": "0", "۱": "1", "۲": "2", "۳": "3", "۴": "4",
    "۵": "5", "۶": "6", "۷": "7", "۸": "8", "۹": "9",
  };
  const AZERTY_DIGITS = {
    "&": "1", "é": "2", '"': "3", "'": "4", "(": "5",
    "-": "6", "è": "7", "_": "8", "ç": "9", "à": "0",
  };
  const HID_GAP_MS = 45;

  const state = {
    student: null,
    results: [],
    template: "full",
    busy: false,
    searchSeq: 0,
    loaded: false,
  };

  const $ = (id) => document.getElementById(id);
  const searchInput = $("summons-search");
  const classSelect = $("summons-class");
  const resultsEl = $("summons-results");
  const selectedEl = $("summons-selected");
  const formEl = $("summons-form");

  function safe(value) {
    return A.escapeHtml(value === null || value === undefined ? "" : value);
  }

  function readTemplate() {
    try {
      const stored = window.localStorage.getItem(TEMPLATE_KEY);
      return TEMPLATE_VALUES.includes(stored) ? stored : "full";
    } catch (error) {
      return "full";
    }
  }

  function saveTemplate(value) {
    state.template = TEMPLATE_VALUES.includes(value) ? value : "full";
    try {
      window.localStorage.setItem(TEMPLATE_KEY, state.template);
    } catch (error) {
      /* private windows may refuse localStorage */
    }
  }

  function printUrl(code) {
    return `/summons/print/${encodeURIComponent(code)}?print=1&template=${encodeURIComponent(
      state.template
    )}`;
  }

  function setStep(label) {
    const badge = $("summons-step-badge");
    if (badge) badge.textContent = label;
  }

  /* ---------------------------------------------------------------------- */
  /* Student search                                                          */
  /* ---------------------------------------------------------------------- */

  const runSearch = A.debounce(async (query) => {
    const seq = ++state.searchSeq;
    const trimmed = String(query || "").trim();
    if (trimmed.length < 2) {
      state.results = [];
      renderResults([], trimmed);
      return;
    }
    try {
      const params = new URLSearchParams({ q: trimmed, active: "1", per_page: "10" });
      if (classSelect.value) params.set("class_group", classSelect.value);
      const data = await A.api(`/api/students?${params.toString()}`);
      if (seq !== state.searchSeq) return;
      state.results = Array.isArray(data.items) ? data.items : [];
      renderResults(state.results, trimmed);
    } catch (error) {
      if (seq !== state.searchSeq) return;
      resultsEl.innerHTML = A.emptyState(
        "warning",
        A.t("تعذر البحث"),
        error.message || A.t("أعد المحاولة.")
      );
    }
  }, 260);

  function renderResults(items, query) {
    const holder = resultsEl;
    if (!query) {
      holder.innerHTML = `<div class="tiny muted" style="padding:10px 2px">${safe(
        A.t("اكتب حرفين على الأقل لبدء البحث، أو امسح بطاقة التلميذ بقارئ USB.")
      )}</div>`;
      return;
    }
    if (!items.length) {
      holder.innerHTML = A.emptyState(
        "users",
        A.t("لا يوجد تلميذ مطابق"),
        A.t("جرّب الاسم العائلي أو رقم التعريف أو رقم هاتف الولي.")
      );
      return;
    }
    holder.innerHTML = items
      .map(
        (item) => `<button class="summons-result" type="button" data-student="${safe(
          item.student_nin
        )}">
          ${A.avatar(item, "avatar-sm")}
          <span class="summons-result-text">
            <strong>${safe(item.full_name || A.t("بدون اسم"))}</strong>
            <span>${safe(item.class_group || A.t("بدون قسم"))} · <b class="mono">${safe(
              item.student_nin
            )}</b></span>
          </span>
          ${A.icon("chevron-left")}
        </button>`
      )
      .join("");
  }

  function findStudent(nin) {
    return state.results.find((item) => item.student_nin === nin) || null;
  }

  async function scanCode(raw) {
    const code = String(raw || "").replace(/[٠-٩۰-۹]/g, (digit) => SCAN_DIGITS[digit] || digit);
    if (!code) return;
    if (state.student) clearStudent();
    searchInput.value = code;
    try {
      const params = new URLSearchParams({ q: code, active: "1", per_page: "5" });
      const data = await A.api(`/api/students?${params.toString()}`);
      state.results = Array.isArray(data.items) ? data.items : [];
      renderResults(state.results, code);
      const exact =
        state.results.find((item) => item.student_nin === code) ||
        (state.results.length === 1 ? state.results[0] : null);
      if (exact) {
        A.beep("scan");
        selectStudent(exact);
      } else if (!state.results.length) {
        A.beep("error");
        A.toast(A.t("الباركود غير مسجل في قاعدة بيانات التلاميذ."), "warning");
      }
    } catch (error) {
      A.toast(error.message || A.t("تعذر البحث عن البطاقة."), "error");
    }
  }

  function scanDigit(event) {
    const physical = /^(?:Digit|Numpad)(\d)$/.exec(event.code || "");
    if (physical) return physical[1];
    if (SCAN_DIGITS[event.key]) return SCAN_DIGITS[event.key];
    if (Object.prototype.hasOwnProperty.call(AZERTY_DIGITS, event.key)) {
      return AZERTY_DIGITS[event.key];
    }
    return "";
  }

  function isTypingTarget(target) {
    return Boolean(
      target &&
        target.closest &&
        target.closest("input, textarea, select, [contenteditable='true'], [contenteditable='']")
    );
  }

  function bindScanner() {
    let buffer = "";
    let lastKeyAt = 0;
    document.addEventListener(
      "keydown",
      (event) => {
        if (event.defaultPrevented || event.ctrlKey || event.altKey || event.metaKey) return;
        if (event.isComposing) return;
        if (isTypingTarget(event.target)) return;
        if (document.getElementById("modal-root").childElementCount) return;
        if (event.key === "Enter") {
          if (buffer && Date.now() - lastKeyAt <= 300) {
            event.preventDefault();
            const code = buffer;
            buffer = "";
            scanCode(code);
          }
          return;
        }
        if (typeof event.key !== "string" || event.key.length !== 1) return;
        const digit = scanDigit(event);
        if (!digit && !/[A-Za-z0-9]/.test(event.key)) {
          if (Date.now() - lastKeyAt > HID_GAP_MS) buffer = "";
          return;
        }
        const now = Date.now();
        if (lastKeyAt && now - lastKeyAt > HID_GAP_MS) buffer = "";
        event.preventDefault();
        buffer += digit || event.key;
        lastKeyAt = now;
      },
      true
    );
  }

  function selectStudent(student) {
    if (!student) return;
    state.student = student;
    resultsEl.hidden = true;
    searchInput.value = "";
    state.results = [];
    formEl.hidden = false;
    setStep(A.t("الخطوة 2"));
    selectedEl.hidden = false;
    selectedEl.innerHTML = `
      ${A.avatar(student)}
      <div class="summons-selected-text">
        <strong>${safe(student.full_name || A.t("بدون اسم"))}</strong>
        <span>${safe(student.class_group || A.t("بدون قسم"))} · ${safe(
          A.t("رقم التعريف")
        )} <b class="mono">${safe(student.student_nin)}</b></span>
        <span>${safe(A.t("هاتف الولي"))}: <b class="mono">${safe(
          student.parent_phone || A.t("غير مسجل")
        )}</b></span>
      </div>
      <div class="summons-selected-actions">
        <a class="btn btn-ghost btn-sm" href="/students?q=${encodeURIComponent(
          student.student_nin
        )}&profile=1">
          ${A.icon("user")}<span>${safe(A.t("ملف التلميذ"))}</span>
        </a>
      </div>`;

    $("summons-guardian-phone").value = student.parent_phone || "";
    if (!$("summons-due-date").value) {
      const now = new Date();
      $("summons-due-date").value = now.toISOString().slice(0, 10);
    }
    if (!$("summons-due-time").value) {
      $("summons-due-time").value = "08:00";
    }
    $("summons-reason").focus();
  }

  function clearStudent() {
    state.student = null;
    formEl.hidden = true;
    selectedEl.hidden = true;
    selectedEl.innerHTML = "";
    resultsEl.hidden = false;
    setStep(A.t("الخطوة 1"));
    renderResults([], "");
    searchInput.focus();
  }

  /* ---------------------------------------------------------------------- */
  /* Summons form                                                            */
  /* ---------------------------------------------------------------------- */

  function renderPresets() {
    const raw = $("summons-layout").dataset.reasons || "";
    const reasons = raw.split("|").filter(Boolean);
    const holder = $("summons-presets");
    holder.innerHTML = reasons
      .map(
        (reason) =>
          `<button class="summons-preset" type="button" data-reason="${safe(reason)}">${safe(
            A.t(reason)
          )}</button>`
      )
      .join("");
  }

  async function issueSummons(event) {
    event.preventDefault();
    if (!state.student || state.busy) return;
    const reasonInput = $("summons-reason");
    const reason = reasonInput.value.trim();
    if (reason.length < 2) {
      A.toast(A.t("يرجى تحديد سبب الاستدعاء قبل الإصدار."), "warning");
      reasonInput.focus();
      return;
    }
    const button = $("summons-issue");
    state.busy = true;
    A.setBusy(button, true);
    try {
      const data = await A.api("/api/summons", {
        method: "POST",
        body: {
          code: state.student.student_nin,
          reason,
          due_date: $("summons-due-date").value,
          due_time: $("summons-due-time").value,
          guardian_name: $("summons-guardian-name").value.trim(),
          guardian_phone: $("summons-guardian-phone").value.trim(),
          note: $("summons-note").value.trim(),
        },
      });
      const summon = data.summon;
      A.flash("success");
      A.beep("success");
      A.toast(
        A.t(`تم إصدار الاستدعاء ${summon.summon_code}. جارٍ فتح نافذة الطباعة…`),
        "success"
      );
      window.location.assign(printUrl(summon.summon_code));
    } catch (error) {
      A.flash("error");
      A.beep("error");
      A.toast(error.message || A.t("تعذر إصدار الاستدعاء."), "error");
    } finally {
      state.busy = false;
      A.setBusy(button, false);
    }
  }

  /* ---------------------------------------------------------------------- */
  /* Context: stats, open queue, today's summonses                           */
  /* ---------------------------------------------------------------------- */

  function statusBadge(item) {
    if (item.status === "ATTENDED") {
      return `<span class="badge badge--present">${safe(A.t("حضر الولي"))}</span>`;
    }
    if (item.status === "CANCELLED") {
      return `<span class="badge badge--cancelled">${safe(A.t("ملغى"))}</span>`;
    }
    return `<span class="badge badge--late">${safe(A.t("بانتظار الولي"))}</span>`;
  }

  function dueLabel(item) {
    if (!item.due_display) return A.t("بدون موعد محدد");
    return `${A.t("الحضور المطلوب")}: ${item.due_display}`;
  }

  function rowTemplate(item) {
    return `<div class="summons-row">
      ${A.avatar(item.student, "avatar-sm")}
      <div class="summons-row-text">
        <strong>${safe(item.student.full_name || A.t("بدون اسم"))}</strong>
        <span>${safe(item.student.class_group || A.t("بدون قسم"))} · ${safe(
          dueLabel(item)
        )}</span>
      </div>
      ${statusBadge(item)}
      <div class="summons-row-actions">
        <a class="icon-btn" href="${printUrl(item.summon_code)}" target="_blank" rel="noopener"
           title="${safe(A.t("إعادة الطباعة"))}" aria-label="${safe(A.t("إعادة الطباعة"))}">
          ${A.icon("printer")}
        </a>
        ${
          item.status === "OPEN"
            ? `<button class="icon-btn" type="button" data-attend="${item.id}"
                 title="${safe(A.t("تسجيل حضور الولي"))}" aria-label="${safe(A.t("تسجيل حضور الولي"))}">
                 ${A.icon("check")}
               </button>
               <button class="icon-btn" type="button" data-cancel="${item.id}"
                 title="${safe(A.t("إلغاء الاستدعاء"))}" aria-label="${safe(A.t("إلغاء الاستدعاء"))}">
                 ${A.icon("x")}
               </button>`
            : ""
        }
      </div>
    </div>`;
  }

  function renderContext(data) {
    const stats = data.stats || {};
    $("summons-kpi-issued").textContent = A.formatInt(stats.issued || 0);
    $("summons-kpi-open").textContent = A.formatInt(stats.open || 0);
    $("summons-kpi-attended").textContent = A.formatInt(stats.attended_today || 0);
    $("summons-day-label").textContent = `${data.date_display || ""}${
      data.server_time ? ` · ${data.server_time.slice(0, 5)}` : ""
    }`;
    const overdue = Number(stats.overdue || 0);
    const overdueBox = $("summons-overdue");
    overdueBox.hidden = overdue <= 0;
    $("summons-overdue-text").textContent = A.t(
      `${A.formatInt(overdue)} استدعاء تجاوز موعد حضور الولي`
    );

    const openItems = Array.isArray(data.open_items) ? data.open_items : [];
    $("summons-open-count").textContent = String(openItems.length);
    $("summons-open-list").innerHTML = openItems.length
      ? openItems.map(rowTemplate).join("")
      : `<div class="tiny muted" style="padding:6px 0">${safe(
          A.t("لا توجد استدعاءات بانتظار الولي.")
        )}</div>`;

    const recent = Array.isArray(data.recent) ? data.recent : [];
    $("summons-recent-count").textContent = String(recent.length);
    $("summons-recent").innerHTML = recent.length
      ? recent.map(rowTemplate).join("")
      : `<div class="tiny muted" style="padding:6px 0">${safe(
          A.t("لا توجد استدعاءات صادرة اليوم بعد.")
        )}</div>`;

    const classes = Array.isArray(data.classes) ? data.classes : [];
    if (classes.length && classSelect.options.length <= 1) {
      classSelect.innerHTML =
        `<option value="">${safe(A.t("كل الأقسام"))}</option>` +
        classes
          .map(
            (item) =>
              `<option value="${safe(item.value)}">${safe(item.label)}</option>`
          )
          .join("");
    }
  }

  async function loadContext(options = {}) {
    try {
      const data = await A.api("/api/summons/context");
      renderContext(data);
      state.loaded = true;
    } catch (error) {
      if (!options.silent) {
        A.toast(error.message || A.t("تعذر تحميل بيانات الاستدعاء."), "error");
      }
      ["summons-open-list", "summons-recent"].forEach((id) => {
        $(id).innerHTML = A.emptyState(
          "warning",
          A.t("تعذر تحميل البيانات"),
          error.message || A.t("أعد المحاولة.")
        );
      });
    } finally {
      if (!options.silent) A.setBusy($("summons-refresh"), false);
    }
  }

  async function changeStatus(id, action, button) {
    if (!id || state.busy) return;
    if (action === "cancel") {
      const ok = await A.confirm({
        title: A.t("إلغاء الاستدعاء"),
        message: A.t("سيُعلَّم الاستدعاء كملغى ويبقى في السجل. هل تريد المتابعة؟"),
        confirmLabel: A.t("إلغاء الاستدعاء"),
        danger: true,
      });
      if (!ok) return;
    }
    state.busy = true;
    A.setBusy(button, true);
    try {
      await A.api(`/api/summons/${id}/${action === "cancel" ? "cancel" : "attend"}`, {
        method: "POST",
      });
      A.toast(
        action === "cancel"
          ? A.t("تم إلغاء الاستدعاء.")
          : A.t("تم تسجيل حضور ولي الأمر."),
        "success"
      );
      A.flash("success");
      A.beep("success");
      await loadContext({ silent: true });
    } catch (error) {
      A.toast(error.message || A.t("تعذر تحديث الاستدعاء."), "error");
      A.flash("error");
      A.beep("error");
    } finally {
      state.busy = false;
      A.setBusy(button, false);
    }
  }

  /* ---------------------------------------------------------------------- */
  /* Wiring                                                                  */
  /* ---------------------------------------------------------------------- */

  function bindEvents() {
    searchInput.addEventListener("input", () => {
      if (state.student) clearStudent();
      runSearch(searchInput.value);
    });

    searchInput.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      const value = searchInput.value.trim();
      if (!value) return;
      if (state.results.length === 1) {
        selectStudent(state.results[0]);
        return;
      }
      const exact = findStudent(value);
      if (exact) {
        selectStudent(exact);
        return;
      }
      runSearch(value);
    });

    classSelect.addEventListener("change", () => {
      if (searchInput.value.trim().length >= 2) runSearch(searchInput.value);
    });

    resultsEl.addEventListener("click", (event) => {
      const button = event.target.closest("[data-student]");
      if (!button) return;
      selectStudent(findStudent(button.dataset.student));
    });

    $("summons-presets").addEventListener("click", (event) => {
      const chip = event.target.closest("[data-reason]");
      if (!chip) return;
      document
        .querySelectorAll("[data-reason]")
        .forEach((item) => item.classList.remove("is-active"));
      chip.classList.add("is-active");
      $("summons-reason").value = chip.dataset.reason || "";
    });

    $("summons-reason").addEventListener("input", (event) => {
      const value = event.target.value;
      document.querySelectorAll("[data-reason]").forEach((item) => {
        item.classList.toggle("is-active", item.dataset.reason === value);
      });
    });

    formEl.addEventListener("submit", issueSummons);
    $("summons-change").addEventListener("click", clearStudent);

    $("summons-template").addEventListener("change", (event) => {
      saveTemplate(event.target.value);
    });

    $("summons-refresh").addEventListener("click", (event) => {
      A.setBusy(event.currentTarget, true);
      loadContext();
    });

    document.addEventListener("click", (event) => {
      const attend = event.target.closest("[data-attend]");
      if (attend) {
        changeStatus(Number(attend.dataset.attend), "attend", attend);
        return;
      }
      const cancel = event.target.closest("[data-cancel]");
      if (cancel) {
        changeStatus(Number(cancel.dataset.cancel), "cancel", cancel);
      }
    });
  }

  function init() {
    state.template = readTemplate();
    $("summons-template").value = state.template;
    renderPresets();
    bindEvents();
    bindScanner();
    loadContext();
    window.setInterval(() => {
      if (!document.hidden) loadContext({ silent: true });
    }, 60000);
  }

  init();
})();
