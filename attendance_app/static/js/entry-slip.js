/* Entry slip desk: scan an absent student, record the cause, print a signed thermal receipt. */
(function () {
  "use strict";

  const A = window.App;
  const HID_GAP_MS = 45;
  const HID_ENTER_GAP_MS = 300;
  const HID_MAX_LENGTH = 128;
  const CAMERA_DUPLICATE_MS = 3500;
  const TEMPLATE_STORAGE_KEY = "entrySlipTemplate";
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

  const state = {
    periodId: null,
    periods: [],
    reasons: [],
    lookup: null,
    template: "full",
    targetDate: null,
    busy: false,
    buffer: "",
    lastKeyAt: 0,
    refreshTimer: null,
    pollTimer: null,
    contextErrorShown: false,
    cameraOn: false,
    cameraBusy: false,
    stream: null,
    detector: null,
    cameraTimer: null,
    lastCameraCode: "",
    lastCameraAt: 0,
  };

  const $ = (id) => document.getElementById(id);
  const resultEl = $("entry-result");
  const periodSelect = $("entry-period");
  const orb = $("entry-scan-orb");
  const bufferHint = $("entry-buffer-hint");
  const stateLabel = $("entry-state-label");

  function safe(value) {
    return A.escapeHtml(value === null || value === undefined ? "" : value);
  }

  function readStoredTemplate() {
    try {
      const value = window.localStorage.getItem(TEMPLATE_STORAGE_KEY);
      return TEMPLATE_VALUES.includes(value) ? value : "full";
    } catch (error) {
      return "full";
    }
  }

  function templateLabel(value) {
    if (value === "medium") return A.t("متوسط");
    if (value === "small") return A.t("صغير");
    return A.t("كامل");
  }

  function printUrlFor(slipCode) {
    return `/entry-slip/print/${encodeURIComponent(slipCode)}?print=1&template=${encodeURIComponent(
      state.template
    )}`;
  }

  function syncTemplateUi() {
    document.querySelectorAll("[data-entry-template]").forEach((label) => {
      const active = label.dataset.entryTemplate === state.template;
      label.classList.toggle("is-active", active);
      const input = label.querySelector("input");
      if (input) input.checked = active;
    });
    const badge = $("entry-template-badge");
    if (badge) badge.textContent = templateLabel(state.template);
  }

  function selectTemplate(value) {
    state.template = TEMPLATE_VALUES.includes(value) ? value : "full";
    try {
      window.localStorage.setItem(TEMPLATE_STORAGE_KEY, state.template);
    } catch (error) {
      /* localStorage can be unavailable in private windows */
    }
    syncTemplateUi();
  }

  function readingDeepLink() {
    const params = new URLSearchParams(window.location.search);
    const date = (params.get("date") || "").trim();
    if (/^\d{4}-\d{2}-\d{2}$/.test(date)) state.targetDate = date;
    return (params.get("code") || "").trim();
  }

  function renderBackdateBanner() {
    const holder = $("entry-backdate");
    if (!holder) return;
    if (!state.targetDate) {
      holder.innerHTML = "";
      return;
    }
    holder.innerHTML = `<div class="entry-backdate">
      ${A.icon("calendar")}
      <div>
        <strong>${safe(A.t("وضع التسوية — تاريخ سابق"))}: <span class="mono">${safe(
          A.formatDate(state.targetDate)
        )}</span></strong>
        <p>${safe(
          A.t("ستُسجَّل الورقة حضوره في هذا اليوم بدل الغياب، مع إلغاء رسائل الغياب المعلّقة.")
        )}</p>
      </div>
      <button class="btn btn-ghost btn-sm" type="button" data-backdate-cancel>${safe(
        A.t("العودة إلى اليوم")
      )}</button>
    </div>`;
    const cancel = holder.querySelector("[data-backdate-cancel]");
    if (cancel) {
      cancel.addEventListener("click", () => {
        state.targetDate = null;
        const url = new URL(window.location.href);
        url.searchParams.delete("code");
        url.searchParams.delete("date");
        window.history.replaceState({}, "", url.pathname + url.search);
        renderBackdateBanner();
        clearResult();
        A.toast(A.t("تمت العودة إلى تاريخ اليوم."), "info");
      });
    }
  }

  /* ---------------------------------------------------------------------- */
  /* Context + sidebar                                                       */
  /* ---------------------------------------------------------------------- */

  async function loadContext(options = {}) {
    const silent = Boolean(options.silent);
    try {
      const params = state.periodId ? { period_id: state.periodId } : null;
      const data = await A.api(A.apiUrl("/api/entry-slip/context", params));
      state.contextErrorShown = false;
      state.reasons = Array.isArray(data.reasons) && data.reasons.length ? data.reasons : state.reasons;
      if (!Array.isArray(data.periods) || data.periods.length === 0) {
        periodSelect.innerHTML = `<option value="">${safe(A.t("لا توجد فترات نشطة"))}</option>`;
        stateLabel.textContent = A.t("أضف فترة من الإعدادات");
        $("entry-period-note").classList.add("is-manual");
        $("entry-period-note").lastElementChild.textContent = A.t("لا توجد فترة نشطة. أضف فترة من صفحة الإعدادات.");
        renderClosure(null);
        renderStats(null);
        renderRecent([]);
        if (!silent) A.toast(A.t("لا توجد فترة نشطة. أضف فترة من الإعدادات."), "warning");
        return data;
      }
      state.periods = data.periods;
      renderPeriodOptions(data.periods);
      if (data.selected_is_auto && state.periodId) {
        state.periodId = null;
        periodSelect.value = "";
      }
      periodSelect.value = state.periodId ? String(state.periodId) : "";
      renderPeriodNote(data);
      renderClosure(data.closure);
      renderStats(data.stats);
      renderRecent(data.recent || []);
      updateMeta(data);
      updateReadyLabel();
      return data;
    } catch (error) {
      if (!silent || !state.contextErrorShown) {
        A.toast(error.message || A.t("تعذر تحميل بيانات ورقة الدخول."), "error");
        state.contextErrorShown = true;
      }
      $("entry-recent").innerHTML = A.emptyState(
        "warning",
        A.t("تعذر تحميل البيانات"),
        error.message || A.t("أعد المحاولة.")
      );
      return null;
    } finally {
      if (!silent) A.setBusy($("entry-refresh"), false);
    }
  }

  function renderPeriodOptions(periods) {
    const current = periodSelect.value;
    const options = [`<option value="">${safe(A.t("تلقائي حسب الوقت الحالي"))}</option>`];
    periods.forEach((period) => {
      options.push(
        `<option value="${period.id}">${safe(period.period_name)} — ${safe(period.start_time)}</option>`
      );
    });
    periodSelect.innerHTML = options.join("");
    periodSelect.value = state.periodId ? String(state.periodId) : current || "";
  }

  function renderPeriodNote(data) {
    const note = $("entry-period-note");
    const text = note.querySelector("span");
    const period = data.selected_period;
    if (!period) {
      note.classList.add("is-manual");
      text.textContent = A.t("لا توجد فترة نشطة.");
      return;
    }
    if (data.selected_is_auto) {
      note.classList.remove("is-manual");
      text.textContent = A.t(
        `تلقائي: ${period.period_name} (${period.start_time}) — نافذة الغياب حتى ${period.absent_cutoff_time}`
      );
    } else {
      note.classList.add("is-manual");
      text.textContent = A.t(
        `اختيار يدوي: ${period.period_name} — تُسجَّل الأوراق في هذه الفترة فقط.`
      );
    }
  }

  function renderStats(stats) {
    const empty = { absent: 0, late: 0, issued: 0, total: 0, logged: 0 };
    const data = stats || empty;
    $("entry-kpi-absent").textContent = A.formatInt(data.absent || 0);
    $("entry-kpi-late").textContent = A.formatInt(data.late || 0);
    $("entry-kpi-issued").textContent = A.formatInt(data.issued || 0);
    $("entry-kpi-total").textContent = A.formatInt(data.total || 0);
    const denominator = Number(data.absent || 0) + Number(data.issued || 0);
    const rate = denominator ? Math.round((Number(data.issued || 0) / denominator) * 100) : 0;
    $("entry-rate-label").textContent = `${rate}%`;
    $("entry-rate-bar").style.width = `${Math.max(0, Math.min(100, rate))}%`;
  }

  function updateMeta(data) {
    const bits = [
      A.t(`${A.formatInt(data.stats ? data.stats.total : 0)} تلميذ نشط`),
      A.t(`المجموع المسجل: ${A.formatInt(data.stats ? data.stats.logged : 0)}`),
    ];
    $("entry-period-meta").textContent = bits.join(" · ");
  }

  function renderClosure(closure) {
    const holder = $("entry-closure");
    if (!closure) {
      holder.innerHTML = "";
      return;
    }
    const time = (closure.closed_at || "").slice(11, 16);
    holder.innerHTML = `
      <div class="closure-banner" style="margin-top:10px">
        ${A.icon("warning")}
        <div>${safe(A.t("أُغلقت هذه الفترة اليوم"))}${time ? ` ${safe(A.t("على الساعة"))} <strong class="mono">${safe(time)}</strong>` : ""}. ${safe(
          A.t("يمكن إصدار ورقة دخول لتلميذ غائب، وستُحدَّث حالته تلقائيًا.")
        )}</div>
      </div>`;
  }

  function renderRecent(items) {
    const holder = $("entry-recent");
    $("entry-recent-count").textContent = String(items.length);
    if (!items.length) {
      holder.innerHTML = `<div class="tiny muted" style="padding:8px 0">${safe(
        A.t("لا توجد أوراق دخول صادرة في هذه الفترة بعد.")
      )}</div>`;
      return;
    }
    holder.innerHTML = items
      .map(
        (item) => `
        <div class="entry-recent-row">
          ${A.avatar(item.student)}
          <div>
            <strong>${safe(item.student.full_name || A.t("بدون اسم"))}</strong>
            <span>${safe(item.reason)} — ${safe(item.period.period_name)}</span>
          </div>
          ${A.statusBadge(item.new_status, item.new_status_label)}
          <time>${safe(item.check_in_display)}</time>
          <a class="icon-btn" href="${printUrlFor(item.slip_code)}"
             target="_blank" rel="noopener" title="${safe(A.t("إعادة الطباعة"))}" aria-label="${safe(A.t("إعادة الطباعة"))}">
            ${A.icon("printer")}
          </a>
        </div>`
      )
      .join("");
  }

  function scheduleRefresh(delay = 500) {
    window.clearTimeout(state.refreshTimer);
    state.refreshTimer = window.setTimeout(() => loadContext({ silent: true }), delay);
  }

  function updateReadyLabel() {
    if (state.busy) {
      stateLabel.textContent = A.t("جارٍ التحقق من البطاقة…");
      return;
    }
    if (state.cameraOn) {
      stateLabel.textContent = A.t("الكاميرا مفعّلة — وجّه الباركود");
      return;
    }
    stateLabel.textContent = $("entry-closure").textContent.trim()
      ? A.t("الفترة مغلقة — يمكن إصدار أوراق الدخول")
      : A.t("جاهز لمسح بطاقة الغائب");
  }

  /* ---------------------------------------------------------------------- */
  /* Lookup + result card                                                    */
  /* ---------------------------------------------------------------------- */

  function normalizeScanCode(value) {
    return String(value || "")
      .replace(/[٠-٩۰-۹]/g, (digit) => SCAN_DIGITS[digit] || digit)
      .trim();
  }

  async function processCode(rawCode, source) {
    const code = normalizeScanCode(rawCode);
    if (!code || state.busy) return;
    state.busy = true;
    state.lookup = null;
    orb.classList.add("is-scanning");
    stateLabel.textContent =
      source === "camera" ? A.t("جارٍ قراءة الباركود…") : A.t("جارٍ التحقق من البطاقة…");
    try {
      const payload = { code };
      if (state.periodId) payload.period_id = state.periodId;
      if (state.targetDate) payload.date = state.targetDate;
      const data = await A.api("/api/entry-slip/lookup", { method: "POST", body: payload });
      state.lookup = data;
      renderLookup(data);
      if (data.action === "absent" || data.action === "not_recorded") {
        A.flash("warning");
        A.beep("late");
      } else if (data.action === "not_found") {
        A.flash("error");
        A.beep("error");
      } else {
        A.flash("success");
        A.beep("success");
      }
    } catch (error) {
      renderError(error.message || A.t("تعذر الاتصال بالخادم المحلي."));
      A.flash("error");
      A.beep("error");
      A.toast(error.message || A.t("تعذر قراءة البطاقة."), "error");
    } finally {
      state.busy = false;
      orb.classList.remove("is-scanning");
      updateReadyLabel();
    }
  }

  function stateInfo(data) {
    if (data.action === "absent") return { icon: "warning", label: A.t("غائب") };
    if (data.action === "not_recorded") return { icon: "clock", label: A.t("بدون تسجيل") };
    if (data.action === "already_issued") return { icon: "printer", label: A.t("ورقة صادرة") };
    if (data.action === "already_recorded") {
      return {
        icon: "check",
        label: (data.attendance && data.attendance.status_label) || A.t("مسجل"),
      };
    }
    if (data.action === "excused") return { icon: "shield", label: A.t("غياب مبرر") };
    if (data.action === "inactive") return { icon: "user", label: A.t("غير نشط") };
    if (data.action === "not_found") return { icon: "warning", label: A.t("غير معروف") };
    return { icon: "warning", label: A.t("تنبيه") };
  }

  function headClassFor(data) {
    if (["absent", "not_recorded"].includes(data.action)) return "is-late";
    if (["not_found", "inactive"].includes(data.action)) return "is-error";
    if (["excused"].includes(data.action)) return "is-warning";
    return "";
  }

  function todayHtml(today, backdated) {
    if (!today || !Array.isArray(today.periods) || !today.periods.length) return "";
    return `<div class="scan-today">
      <div class="scan-today-head"><strong>${safe(
        A.t(backdated ? "سجل يوم التسوية" : "حضور اليوم")
      )}</strong><span>${safe(
        A.t(`${A.formatInt(today.attended_periods)} من ${A.formatInt(today.total_periods)} فترات`)
      )}</span></div>
      <div class="scan-today-track">
        ${today.periods
          .map((period) => {
            const statusClass = period.status ? `is-${String(period.status).toLowerCase()}` : "";
            return `<span class="${statusClass}" title="${safe(period.period_name)} — ${safe(period.status_label)}${period.scan_time ? ` (${safe(period.scan_time)})` : ""}"></span>`;
          })
          .join("")}
      </div>
    </div>`;
  }

  function reasonBox(data) {
    const chips = state.reasons
      .map((reason) => {
        const label = A.t(reason);
        return `<button class="entry-chip" type="button" data-entry-chip="${safe(label)}">${safe(label)}</button>`;
      })
      .join("");
    const previous = data.attendance ? data.attendance.status_label : A.t("بدون تسجيل");
    return `<div class="entry-reason-box">
      <header><strong>${safe(A.t("سبب الدخول"))}</strong><span>${safe(A.t("مطلوب قبل إصدار الورقة"))}</span></header>
      <div class="entry-presets">${chips}</div>
      <div class="entry-field">
        <label for="entry-reason">${safe(A.t("السبب"))}</label>
        <input class="input" id="entry-reason" maxlength="300" autocomplete="off"
               placeholder="${safe(A.t("اكتب أو اختر سببًا واضحًا…"))}">
      </div>
      <div class="entry-field">
        <label for="entry-note">${safe(A.t("ملاحظة إضافية (اختياري)"))}</label>
        <textarea class="textarea" id="entry-note" maxlength="300"
                  placeholder="${safe(A.t("تفاصيل إضافية تظهر على الإيصال…"))}"></textarea>
      </div>
      <div class="entry-preview">
        <div><span>${safe(A.t("الحالة السابقة"))}</span><strong class="is-warning">${safe(previous)}</strong></div>
        <div><span>${safe(A.t("بعد الدخول"))}</span><strong class="is-success">${safe(data.new_status_label)}</strong></div>
        <div><span>${safe(A.t("الفترة"))}</span><strong>${safe(data.period.period_name)}</strong></div>
      </div>
      <div class="entry-issue-bar">
        <button class="btn btn-primary" type="button" id="entry-issue">
          ${A.icon("printer")}<span>${safe(A.t("إصدار وطباعة الورقة"))}</span>
        </button>
        <span class="entry-issue-note">${A.icon("shield-check")}${safe(
          A.t("ستُحدَّث حالة التلميذ وتُلغى رسائل الغياب المعلّقة، ثم تُطبع ورقة حرارية موقّعة.")
        )}</span>
      </div>
    </div>`;
  }

  function slipSummary(slip) {
    if (!slip) return "";
    const printUrl = printUrlFor(slip.slip_code);
    const verifyUrl = `/entry-slip/verify?code=${encodeURIComponent(slip.qr_payload)}`;
    return `<div class="entry-slip-summary">
      <div class="entry-slip-summary-head">${A.icon("check")}<span>${safe(
        A.t("ورقة دخول صادرة لهذه الفترة")
      )}</span></div>
      <span class="entry-slip-summary-code">${safe(slip.slip_code)}</span>
      <div class="entry-preview">
        <div><span>${safe(A.t("السبب"))}</span><strong>${safe(slip.reason)}</strong></div>
        <div><span>${safe(A.t("وقت الدخول"))}</span><strong class="mono">${safe(slip.check_in_display)}</strong></div>
        <div><span>${safe(A.t("الحالة"))}</span><strong class="is-success">${safe(slip.new_status_label)}</strong></div>
      </div>
      <div class="entry-slip-summary-actions">
        <a class="btn btn-primary btn-sm" href="${printUrl}" target="_blank" rel="noopener">
          ${A.icon("printer")}<span>${safe(A.t("طباعة الإيصال"))}</span>
        </a>
        <a class="btn btn-secondary btn-sm" href="${verifyUrl}" target="_blank" rel="noopener">
          ${A.icon("qr")}<span>${safe(A.t("التحقق من التوقيع"))}</span>
        </a>
      </div>
    </div>`;
  }

  function actionLinks(data) {
    const student = data.student;
    const periodId = data.period ? data.period.id : "";
    if (student && data.action === "already_recorded") {
      return `<a class="btn btn-secondary btn-sm" href="/attendance?date=${encodeURIComponent(
        data.date
      )}&period_id=${encodeURIComponent(periodId)}">${safe(A.t("سجل الفترة"))}</a>
      <a class="btn btn-ghost btn-sm" href="/students?q=${encodeURIComponent(student.student_nin)}&profile=1">${safe(
        A.t("ملف التلميذ")
      )}</a>`;
    }
    if (student && data.action === "excused") {
      return `<a class="btn btn-secondary btn-sm" href="/attendance?date=${encodeURIComponent(
        data.date
      )}&period_id=${encodeURIComponent(periodId)}&status=EXCUSED">${safe(A.t("مراجعة التبرير"))}</a>`;
    }
    if (data.action === "not_found") {
      const code = data.scanned_code || "";
      return `<a class="btn btn-secondary btn-sm" href="/students?q=${encodeURIComponent(code)}">${safe(
        A.t("بحث في التلاميذ")
      )}</a>
      <a class="btn btn-primary btn-sm" href="/students?new=1&nin=${encodeURIComponent(code)}">${safe(
        A.t("إضافة التلميذ")
      )}</a>`;
    }
    return "";
  }

  function renderLookup(data) {
    const student = data.student;
    const info = stateInfo(data);
    const headClass = headClassFor(data);
    const name = student
      ? student.full_name
      : data.action === "not_found"
        ? A.t("باركود غير مسجل")
        : A.t("بطاقة غير معروفة");
    const subBits = student
      ? [student.class_group || A.t("بدون قسم"), student.student_nin]
      : [A.t(`الرمز المقروء: ${data.scanned_code || "—"}`)];
    const meta = `<div class="scan-meta">
      <div><span>${safe(A.t("الفترة"))}</span><strong>${safe(data.period ? data.period.period_name : "—")}</strong></div>
      <div><span>${safe(A.t("وقت التحقق"))}</span><strong class="mono">${safe((data.scan_time || "").slice(0, 5) || "—")}</strong></div>
      <div><span>${safe(A.t("التاريخ"))}</span><strong class="mono">${safe(data.date_display || "")}</strong></div>
    </div>`;
    const form = data.can_issue ? reasonBox(data) : "";
    const summary = data.slip ? slipSummary(data.slip) : "";
    const links = actionLinks(data);
    resultEl.innerHTML = `<article class="scan-result" data-action="${safe(data.action)}">
      <header class="scan-card-head ${headClass}">
        ${
          student
            ? A.avatar(student)
            : `<div class="avatar" aria-hidden="true">${A.icon(data.action === "not_found" ? "warning" : "user")}</div>`
        }
        <div class="scan-person">
          <h3>${safe(name)}</h3>
          <p>${subBits.map((bit) => safe(bit)).join(" · ")}</p>
        </div>
        <div class="scan-state">${A.icon(info.icon)}<strong>${safe(info.label)}</strong></div>
      </header>
      <div class="scan-card-body">
        <div class="scan-message is-${data.action === "not_found" || data.action === "inactive" ? "error" : "warning"}">${safe(
          data.message || ""
        )}</div>
        ${meta}
        ${form}
        ${summary}
        ${todayHtml(data.today, data.is_backdated)}
        <div class="scan-actions">
          <button class="btn btn-secondary btn-sm" type="button" data-entry-clear>${safe(A.t("مسح بطاقة أخرى"))}</button>
          ${links}
        </div>
      </div>
    </article>`;

    resultEl.querySelectorAll("[data-entry-chip]").forEach((chip) => {
      chip.addEventListener("click", () => {
        resultEl.querySelectorAll("[data-entry-chip]").forEach((item) => item.classList.remove("is-active"));
        chip.classList.add("is-active");
        const input = $("entry-reason");
        if (input) {
          input.value = chip.dataset.entryChip || "";
          input.focus();
        }
      });
    });
    const issueButton = $("entry-issue");
    if (issueButton) issueButton.addEventListener("click", issueSlip);
    const clearButton = resultEl.querySelector("[data-entry-clear]");
    if (clearButton) clearButton.addEventListener("click", clearResult);
    resultEl.setAttribute("aria-label", `${name} — ${info.label}`);
  }

  async function issueSlip() {
    if (!state.lookup || state.busy) return;
    const reasonInput = $("entry-reason");
    const noteInput = $("entry-note");
    const reason = reasonInput ? reasonInput.value.trim() : "";
    if (reason.length < 2) {
      A.toast(A.t("يرجى تحديد سبب الدخول قبل إصدار الورقة."), "warning");
      if (reasonInput) reasonInput.focus();
      return;
    }
    const button = $("entry-issue");
    state.busy = true;
    A.setBusy(button, true);
    try {
      const body = {
        code: state.lookup.scanned_code,
        period_id: state.lookup.period ? state.lookup.period.id : state.periodId,
        reason,
        note: noteInput ? noteInput.value.trim() : "",
      };
      if (state.targetDate) body.date = state.targetDate;
      const data = await A.api("/api/entry-slip", { method: "POST", body });
      const slip = data.slip;
      A.toast(
        A.t(`تم إصدار ورقة الدخول ${slip.slip_code}. جارٍ فتح نافذة الطباعة الحرارية…`),
        "success"
      );
      A.flash("success");
      A.beep("success");
      scheduleRefresh(800);
      if (slip.print_url) {
        window.location.assign(`${slip.print_url}?print=1&template=${encodeURIComponent(state.template)}`);
      } else {
        renderLookup({ ...state.lookup, action: "already_issued", slip, can_issue: false });
      }
    } catch (error) {
      A.toast(error.message || A.t("تعذر إصدار ورقة الدخول."), "error");
      A.flash("error");
      A.beep("error");
    } finally {
      state.busy = false;
      A.setBusy(button, false);
      updateReadyLabel();
    }
  }

  function renderError(message) {
    resultEl.innerHTML = `<article class="scan-result">
      <header class="scan-card-head is-error">
        <div class="avatar" aria-hidden="true">${A.icon("warning")}</div>
        <div class="scan-person"><h3>${safe(A.t("تعذر التحقق من البطاقة"))}</h3><p>${safe(
          A.t("خطأ في الاتصال أو في البيانات المرسلة")
        )}</p></div>
        <div class="scan-state">${A.icon("warning")}<strong>${safe(A.t("خطأ"))}</strong></div>
      </header>
      <div class="scan-card-body">
        <div class="scan-message is-error">${safe(message)}</div>
        <div class="scan-actions">
          <button class="btn btn-secondary btn-sm" type="button" data-entry-clear>${safe(A.t("إخفاء الرسالة"))}</button>
          <a class="btn btn-primary btn-sm" href="/settings">${safe(A.t("الإعدادات"))}</a>
        </div>
      </div>
    </article>`;
    const clearButton = resultEl.querySelector("[data-entry-clear]");
    if (clearButton) clearButton.addEventListener("click", clearResult);
  }

  function clearResult() {
    state.lookup = null;
    resultEl.innerHTML = `<div class="scan-result-empty">
      ${A.icon("entry")}
      <strong>${safe(A.t("بانتظار مسح بطاقة غائب"))}</strong>
      <p>${safe(A.t("ستظهر هنا حالة التلميذ في الفترة، أسباب الدخول، وزر إصدار الورقة الحرارية الموقّعة."))}</p>
    </div>`;
    updateReadyLabel();
    if (document.activeElement && typeof document.activeElement.blur === "function") {
      document.activeElement.blur();
    }
  }

  /* ---------------------------------------------------------------------- */
  /* HID keyboard-wedge capture                                              */
  /* ---------------------------------------------------------------------- */

  function isTypingTarget(target) {
    if (!target || !(target instanceof Element)) return false;
    return Boolean(target.closest("input, textarea, [contenteditable='true'], [contenteditable='']"));
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

  function modalOpen() {
    const root = $("modal-root");
    return Boolean(root && root.childElementCount) || document.body.style.overflow === "hidden";
  }

  function setBufferHint() {
    if (!bufferHint) return;
    bufferHint.innerHTML = `<kbd>${state.buffer.length}</kbd> ${safe(A.t("حرف مستلم"))}`;
  }

  function resetBuffer() {
    state.buffer = "";
    state.lastKeyAt = 0;
    setBufferHint();
  }

  function acceptCharacter(event) {
    if (event.ctrlKey || event.altKey || event.metaKey) return false;
    if (event.isComposing) return false;
    if (modalOpen()) return false;
    if (isTypingTarget(event.target)) return false;
    if (event.key === "Enter") {
      const fresh = state.buffer && Date.now() - state.lastKeyAt <= HID_ENTER_GAP_MS;
      if (!fresh) {
        resetBuffer();
        return false;
      }
      event.preventDefault();
      const code = state.buffer;
      resetBuffer();
      processCode(code, "hid");
      return true;
    }
    if (typeof event.key !== "string" || event.key.length !== 1) return false;
    const digit = scanDigit(event);
    if (!digit && !/[A-Za-z0-9]/.test(event.key)) {
      if (Date.now() - state.lastKeyAt > HID_GAP_MS) resetBuffer();
      return false;
    }
    const now = Date.now();
    const gap = state.lastKeyAt ? now - state.lastKeyAt : 0;
    if (gap > HID_GAP_MS && state.lastKeyAt) {
      state.buffer = "";
    }
    event.preventDefault();
    if (state.buffer.length >= HID_MAX_LENGTH) state.buffer = "";
    state.buffer += digit || event.key;
    state.lastKeyAt = now;
    setBufferHint();
    return true;
  }

  document.addEventListener(
    "keydown",
    (event) => {
      if (event.defaultPrevented) return;
      if (event.key === "Escape" && state.cameraOn) {
        stopCamera();
        return;
      }
      acceptCharacter(event);
    },
    true
  );

  window.addEventListener("blur", resetBuffer);
  document.addEventListener("visibilitychange", () => {
    resetBuffer();
    if (document.hidden) {
      stopCamera();
    } else {
      loadContext({ silent: true });
    }
  });

  /* ---------------------------------------------------------------------- */
  /* Camera fallback                                                         */
  /* ---------------------------------------------------------------------- */

  function setCameraNote(message, isError) {
    const note = $("entry-camera-note");
    if (!note) return;
    note.textContent = message;
    note.style.color = isError ? "#f0b9b9" : "";
  }

  function supportedFormats(detectorClass) {
    if (typeof detectorClass.getSupportedFormats !== "function") {
      return Promise.resolve(["code_128", "qr_code"]);
    }
    return detectorClass.getSupportedFormats().then((formats) => {
      const wanted = ["code_128", "code_39", "code_93", "qr_code", "ean_13"].filter((f) =>
        formats.includes(f)
      );
      return wanted;
    });
  }

  async function startCamera() {
    if (state.cameraOn || state.cameraBusy) return;
    if (!("BarcodeDetector" in window)) {
      setCameraNote(A.t("هذا المتصفح لا يدعم قراءة الباركود من الكاميرا. استعمل قارئ USB أو متصفح Edge/Chrome حديث."), true);
      A.toast(A.t("قراءة الباركود بالكاميرا غير مدعومة في هذا المتصفح. استعمل قارئ USB."), "warning");
      return;
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setCameraNote(A.t("لا يمكن الوصول إلى الكاميرا. تأكد من تشغيل النظام على localhost ومنح الإذن."), true);
      return;
    }
    state.cameraBusy = true;
    const toggle = $("entry-camera-toggle");
    A.setBusy(toggle, true);
    try {
      const formats = await supportedFormats(window.BarcodeDetector);
      if (!formats.length) throw new Error(A.t("لا توجد صيغ باركود مدعومة في هذا المتصفح."));
      state.detector = new window.BarcodeDetector({ formats });
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false,
      });
      const video = $("entry-camera-video");
      state.stream = stream;
      video.srcObject = stream;
      await video.play();
      state.cameraOn = true;
      $("entry-camera-dock").classList.add("is-open");
      toggle.classList.add("is-on");
      toggle.querySelector("span").textContent = A.t("إيقاف الكاميرا");
      setCameraNote(A.t("وجّه الباركود داخل الإطار. تبقى الكاميرا تعمل حتى توقفها أو تصغّر النافذة."), false);
      updateReadyLabel();
      detectFrame();
    } catch (error) {
      const name = error && error.name;
      let message = A.t("تعذر تشغيل الكاميرا.");
      if (name === "NotAllowedError" || name === "SecurityError") {
        message = A.t("تم رفض إذن الكاميرا. اسمح بالوصول من إعدادات المتصفح ثم أعد المحاولة.");
      } else if (name === "NotFoundError" || name === "OverconstrainedError") {
        message = A.t("لا توجد كاميرا متصلة بهذا الجهاز.");
      } else if (name === "NotReadableError") {
        message = A.t("الكاميرا مستعملة من طرف تطبيق آخر. أغلق التطبيق الآخر ثم أعد المحاولة.");
      } else if (error && error.message) {
        message = error.message;
      }
      setCameraNote(message, true);
      A.toast(message, "warning");
      stopCamera();
    } finally {
      state.cameraBusy = false;
      A.setBusy(toggle, false);
      if (state.cameraOn) {
        toggle.disabled = false;
      }
    }
  }

  async function detectFrame() {
    if (!state.cameraOn || !state.detector) return;
    const video = $("entry-camera-video");
    try {
      if (video.readyState >= 2 && video.videoWidth) {
        const codes = await state.detector.detect(video);
        if (codes && codes.length) {
          const value = String(codes[0].rawValue || "").trim();
          const now = Date.now();
          if (
            value &&
            (value !== state.lastCameraCode || now - state.lastCameraAt > CAMERA_DUPLICATE_MS) &&
            !state.busy
          ) {
            state.lastCameraCode = value;
            state.lastCameraAt = now;
            A.beep("scan");
            processCode(value, "camera");
          }
        }
      }
    } catch (error) {
      state.lastCameraError = error;
    }
    if (state.cameraOn) {
      state.cameraTimer = window.setTimeout(detectFrame, 280);
    }
  }

  function stopCamera() {
    state.cameraOn = false;
    window.clearTimeout(state.cameraTimer);
    state.cameraTimer = null;
    if (state.stream) {
      state.stream.getTracks().forEach((track) => track.stop());
      state.stream = null;
    }
    const video = $("entry-camera-video");
    if (video) video.srcObject = null;
    const dock = $("entry-camera-dock");
    if (dock) dock.classList.remove("is-open");
    const toggle = $("entry-camera-toggle");
    if (toggle) {
      toggle.classList.remove("is-on");
      const span = toggle.querySelector("span");
      if (span) span.textContent = A.t("تشغيل الكاميرا");
      toggle.disabled = false;
    }
    updateReadyLabel();
  }

  /* ---------------------------------------------------------------------- */
  /* Shell events                                                            */
  /* ---------------------------------------------------------------------- */

  async function toggleFullscreen() {
    try {
      if (document.fullscreenElement) {
        await document.exitFullscreen();
      } else {
        await document.documentElement.requestFullscreen();
      }
    } catch (error) {
      A.toast(A.t("تعذر تفعيل ملء الشاشة في هذا المتصفح."), "warning");
    }
  }

  function startClock() {
    const clock = $("entry-live-time");
    if (!clock) return;
    const tick = () => {
      clock.textContent = new Date().toLocaleTimeString("en-GB", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        numberingSystem: "latn",
      });
    };
    tick();
    window.setInterval(tick, 1000);
  }

  function bindEvents() {
    periodSelect.addEventListener("change", () => {
      const value = periodSelect.value;
      state.periodId = value ? Number(value) : null;
      resetBuffer();
      clearResult();
      loadContext();
    });
    $("entry-refresh").addEventListener("click", (event) => {
      A.setBusy(event.currentTarget, true);
      loadContext();
    });
    $("entry-clear").addEventListener("click", () => {
      resetBuffer();
      clearResult();
      A.toast(A.t("تم تفريغ ذاكرة المسح والبطاقة الحالية."), "info");
    });
    $("entry-camera-toggle").addEventListener("click", () => {
      if (state.cameraOn) stopCamera();
      else startCamera();
    });
    $("entry-camera-close").addEventListener("click", stopCamera);
    $("entry-fullscreen").addEventListener("click", toggleFullscreen);
    document.addEventListener("fullscreenchange", () => {
      const button = $("entry-fullscreen");
      if (!button) return;
      const isFull = Boolean(document.fullscreenElement);
      button.classList.toggle("is-on", isFull);
      button.querySelector("span").textContent = isFull ? A.t("إنهاء ملء الشاشة") : A.t("ملء الشاشة");
    });
    $("entry-manual-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const input = $("entry-manual-code");
      const code = input.value.trim();
      if (!code) return;
      input.value = "";
      input.blur();
      processCode(code, "manual");
    });
    document.querySelectorAll("[data-entry-template]").forEach((label) => {
      const input = label.querySelector("input");
      if (input) input.addEventListener("change", () => selectTemplate(input.value));
    });
  }

  function startPolling() {
    state.pollTimer = window.setInterval(() => {
      if (!document.hidden && !state.busy) loadContext({ silent: true });
    }, 60000);
  }

  const deepLinkCode = readingDeepLink();
  state.reasons = ($("entry-layout").dataset.reasons || "").split("|").filter(Boolean);
  state.template = readStoredTemplate();
  bindEvents();
  syncTemplateUi();
  setBufferHint();
  renderBackdateBanner();
  startClock();
  loadContext().then(() => {
    if (deepLinkCode) processCode(deepLinkCode, "link");
  });
  startPolling();
})();
