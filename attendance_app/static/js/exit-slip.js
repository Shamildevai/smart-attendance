/* Exit slip desk: scan a student, record the exit cause, print a signed thermal receipt with a return barcode. */
(function () {
  "use strict";

  const A = window.App;
  const HID_GAP_MS = 45;
  const HID_ENTER_GAP_MS = 300;
  const HID_MAX_LENGTH = 128;
  const CAMERA_DUPLICATE_MS = 3500;
  const TEMPLATE_STORAGE_KEY = "exitSlipTemplate";
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
    reasons: [],
    lookup: null,
    template: "full",
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
  const resultEl = $("exit-result");
  const orb = $("exit-scan-orb");
  const bufferHint = $("exit-buffer-hint");
  const stateLabel = $("exit-state-label");

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
    return `/exit-slip/print/${encodeURIComponent(slipCode)}?print=1&template=${encodeURIComponent(
      state.template
    )}`;
  }

  function verifyUrlFor(slip) {
    return `/exit-slip/verify?code=${encodeURIComponent(slip.qr_payload || slip.slip_code)}`;
  }

  function syncTemplateUi() {
    document.querySelectorAll("[data-exit-template]").forEach((label) => {
      const active = label.dataset.exitTemplate === state.template;
      label.classList.toggle("is-active", active);
      const input = label.querySelector("input");
      if (input) input.checked = active;
    });
    const badge = $("exit-template-badge");
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
    return (params.get("code") || "").trim();
  }

  /* ---------------------------------------------------------------------- */
  /* Context + sidebar                                                       */
  /* ---------------------------------------------------------------------- */

  async function loadContext(options = {}) {
    const silent = Boolean(options.silent);
    try {
      const data = await A.api("/api/exit-slip/context");
      state.contextErrorShown = false;
      state.reasons = Array.isArray(data.reasons) && data.reasons.length ? data.reasons : state.reasons;
      renderStats(data.stats);
      renderRecent(data.recent || []);
      $("exit-day-meta").textContent = A.t(
        `${A.formatInt(data.stats ? data.stats.issued : 0)} ورقة خروج اليوم · ${A.formatInt(
          data.stats ? data.stats.active : 0
        )} تلميذ خارج المؤسسة`
      );
      updateReadyLabel();
      return data;
    } catch (error) {
      if (!silent || !state.contextErrorShown) {
        A.toast(error.message || A.t("تعذر تحميل بيانات ورقة الخروج."), "error");
        state.contextErrorShown = true;
      }
      $("exit-recent").innerHTML = A.emptyState(
        "warning",
        A.t("تعذر تحميل البيانات"),
        error.message || A.t("أعد المحاولة.")
      );
      return null;
    } finally {
      if (!silent) A.setBusy($("exit-refresh"), false);
    }
  }

  function renderStats(stats) {
    const data = stats || { issued: 0, returned: 0, active: 0 };
    $("exit-kpi-issued").textContent = A.formatInt(data.issued || 0);
    $("exit-kpi-returned").textContent = A.formatInt(data.returned || 0);
    $("exit-kpi-active").textContent = A.formatInt(data.active || 0);
    const rate = data.issued ? Math.round((Number(data.returned || 0) / data.issued) * 100) : 0;
    $("exit-rate-label").textContent = `${rate}%`;
    $("exit-rate-bar").style.width = `${Math.max(0, Math.min(100, rate))}%`;
  }

  function renderRecent(items) {
    const holder = $("exit-recent");
    $("exit-recent-count").textContent = String(items.length);
    if (!items.length) {
      holder.innerHTML = `<div class="tiny muted" style="padding:8px 0">${safe(
        A.t("لا توجد أوراق خروج صادرة اليوم بعد.")
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
            <span>${safe(item.reason)} — ${safe(item.exit_time_display)}${
              item.returned ? ` · ${safe(A.t("عاد"))} ${safe(item.returned_display)}` : ""
            }</span>
          </div>
          ${
            item.returned
              ? `<span class="badge badge--present">${safe(A.t("عاد"))}</span>`
              : `<span class="badge badge--absent">${safe(A.t("خارج"))}</span>`
          }
          <a class="icon-btn" href="${printUrlFor(item.slip_code)}"
             target="_blank" rel="noopener" title="${safe(A.t("إعادة الطباعة"))}" aria-label="${safe(A.t("إعادة الطباعة"))}">
            ${A.icon("printer")}
          </a>
          ${
            item.returned
              ? ""
              : `<button class="icon-btn" type="button" data-exit-return="${safe(item.slip_code)}"
                   title="${safe(A.t("تسجيل العودة"))}" aria-label="${safe(A.t("تسجيل العودة"))}">
                   ${A.icon("check")}
                 </button>`
          }
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
    stateLabel.textContent = A.t("جاهز لمسح بطاقة التلميذ");
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
      const data = await A.api("/api/exit-slip/lookup", { method: "POST", body: { code } });
      state.lookup = data;
      renderLookup(data);
      if (data.action === "ready") {
        A.flash("warning");
        A.beep("late");
      } else if (data.action === "not_found" || data.action === "inactive") {
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
    if (data.action === "ready") return { icon: "exit", label: A.t("جاهز للخروج") };
    if (data.action === "already_issued") return { icon: "printer", label: A.t("ورقة خروج سارية") };
    if (data.action === "inactive") return { icon: "user", label: A.t("غير نشط") };
    if (data.action === "not_found") return { icon: "warning", label: A.t("غير معروف") };
    return { icon: "warning", label: A.t("تنبيه") };
  }

  function headClassFor(data) {
    if (data.action === "ready") return "is-warning";
    if (["not_found", "inactive"].includes(data.action)) return "is-error";
    return "";
  }

  function todayHtml(today) {
    if (!today || !Array.isArray(today.periods) || !today.periods.length) return "";
    return `<div class="scan-today">
      <div class="scan-today-head"><strong>${safe(A.t("حضور اليوم"))}</strong><span>${safe(
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

  function parentBox(student) {
    return `<div class="exit-parent">
      <div class="exit-parent-head">${A.icon("phone")}<strong>${safe(A.t("بيانات الولي"))}</strong></div>
      <div class="entry-preview">
        <div><span>${safe(A.t("هاتف الولي"))}</span><strong class="mono">${safe(
          student.parent_phone || A.t("غير مسجل")
        )}</strong></div>
        <div><span>${safe(A.t("القسم"))}</span><strong>${safe(student.class_group || A.t("بدون قسم"))}</strong></div>
        <div><span>${safe(A.t("رقم التعريف"))}</span><strong class="mono">${safe(student.student_nin)}</strong></div>
      </div>
    </div>`;
  }

  function reasonBox(data) {
    const chips = state.reasons
      .map((reason) => {
        const label = A.t(reason);
        return `<button class="entry-chip" type="button" data-exit-chip="${safe(label)}">${safe(label)}</button>`;
      })
      .join("");
    const phone = data.student ? data.student.parent_phone || "" : "";
    return `<div class="entry-reason-box">
      <header><strong>${safe(A.t("سبب الخروج"))}</strong><span>${safe(A.t("مطلوب قبل إصدار الإيصال"))}</span></header>
      <div class="entry-presets">${chips}</div>
      <div class="entry-field">
        <label for="exit-reason">${safe(A.t("السبب"))}</label>
        <input class="input" id="exit-reason" maxlength="300" autocomplete="off"
               placeholder="${safe(A.t("اكتب أو اختر سببًا واضحًا…"))}">
      </div>
      <div class="entry-field">
        <label for="exit-guardian-name">${safe(A.t("اسم الولي أو المرافق (اختياري)"))}</label>
        <input class="input" id="exit-guardian-name" maxlength="120" autocomplete="off"
               placeholder="${safe(A.t("من يتولى مرافقة التلميذ؟"))}">
      </div>
      <div class="entry-field">
        <label for="exit-guardian-phone">${safe(A.t("هاتف الولي"))}</label>
        <input class="input" id="exit-guardian-phone" maxlength="30" dir="ltr" autocomplete="off"
               value="${safe(phone)}" placeholder="05xxxxxxxx">
      </div>
      <div class="entry-field">
        <label for="exit-note">${safe(A.t("ملاحظة إضافية (اختياري)"))}</label>
        <textarea class="textarea" id="exit-note" maxlength="300"
                  placeholder="${safe(A.t("تفاصيل إضافية تظهر على الإيصال…"))}"></textarea>
      </div>
      <div class="entry-preview">
        <div><span>${safe(A.t("وقت الخروج"))}</span><strong class="mono">${safe(
          (data.exit_time || "").slice(0, 5)
        )}</strong></div>
        <div><span>${safe(A.t("التاريخ"))}</span><strong class="mono">${safe(data.date_display)}</strong></div>
        <div><span>${safe(A.t("باركود العودة"))}</span><strong>${safe(A.t("يُطبع على الإيصال"))}</strong></div>
      </div>
      <div class="entry-issue-bar">
        <button class="btn btn-primary" type="button" id="exit-issue">
          ${A.icon("printer")}<span>${safe(A.t("إصدار وطباعة ورقة الخروج"))}</span>
        </button>
        <span class="entry-issue-note">${A.icon("shield-check")}${safe(
          A.t("يُطبع إيصال حراري موقّع مع رمز QR وباركود عودة يُستخدم عند الدخول بدل البطاقة.")
        )}</span>
      </div>
    </div>`;
  }

  function slipSummary(slip) {
    if (!slip) return "";
    const printUrl = printUrlFor(slip.slip_code);
    const verifyUrl = verifyUrlFor(slip);
    return `<div class="entry-slip-summary">
      <div class="entry-slip-summary-head">${A.icon(slip.returned ? "check" : "exit")}<span>${safe(
        slip.returned ? A.t("ورقة خروج — تمت العودة") : A.t("ورقة خروج سارية — التلميذ خارج المؤسسة")
      )}</span></div>
      <span class="entry-slip-summary-code">${safe(slip.slip_code)}</span>
      <div class="entry-preview">
        <div><span>${safe(A.t("السبب"))}</span><strong>${safe(slip.reason)}</strong></div>
        <div><span>${safe(A.t("وقت الخروج"))}</span><strong class="mono">${safe(slip.exit_time_display)}</strong></div>
        <div><span>${safe(A.t("الحالة"))}</span><strong class="${slip.returned ? "is-success" : "is-warning"}">${safe(
          slip.returned ? A.t(`عاد ${slip.returned_display}`) : A.t("خارج الآن")
        )}</strong></div>
      </div>
      <div class="entry-slip-summary-actions">
        <a class="btn btn-primary btn-sm" href="${printUrl}" target="_blank" rel="noopener">
          ${A.icon("printer")}<span>${safe(A.t("طباعة الإيصال"))}</span>
        </a>
        <a class="btn btn-secondary btn-sm" href="${verifyUrl}" target="_blank" rel="noopener">
          ${A.icon("qr")}<span>${safe(A.t("التحقق من التوقيع"))}</span>
        </a>
        ${
          slip.returned
            ? ""
            : `<button class="btn btn-secondary btn-sm" type="button" data-exit-return="${safe(slip.slip_code)}">
                 ${A.icon("check")}<span>${safe(A.t("تسجيل العودة"))}</span>
               </button>`
        }
      </div>
    </div>`;
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
      <div><span>${safe(A.t("التاريخ"))}</span><strong class="mono">${safe(data.date_display || "")}</strong></div>
      <div><span>${safe(A.t("وقت المسح"))}</span><strong class="mono">${safe((data.scan_time || "").slice(0, 5) || "—")}</strong></div>
      <div><span>${safe(A.t("مصدر المسح"))}</span><strong>${safe(
        data.scan_kind === "exit_receipt" ? A.t("ورقة خروج") : A.t("بطاقة التلميذ")
      )}</strong></div>
    </div>`;
    const receiptBanner =
      data.scan_kind === "exit_receipt" && data.exit_slip
        ? `<div class="exit-receipt-banner">${A.icon("barcode")}<div>${safe(
            A.t(`تم التعرف على التلميذ من ورقة الخروج ${data.exit_slip.slip_code}.`)
          )}</div></div>`
        : "";
    const form = data.can_issue ? reasonBox(data) : "";
    const summary = data.slip ? slipSummary(data.slip) : "";
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
        ${receiptBanner}
        ${meta}
        ${student ? parentBox(student) : ""}
        ${form}
        ${summary}
        ${student ? todayHtml(data.today) : ""}
        <div class="scan-actions">
          <button class="btn btn-secondary btn-sm" type="button" data-exit-clear>${safe(A.t("مسح بطاقة أخرى"))}</button>
          ${student ? `<a class="btn btn-ghost btn-sm" href="/students?q=${encodeURIComponent(student.student_nin)}&profile=1">${safe(A.t("ملف التلميذ"))}</a>` : ""}
          ${
            data.action === "not_found"
              ? `<a class="btn btn-primary btn-sm" href="/students?new=1&nin=${encodeURIComponent(
                  data.scanned_code || ""
                )}">${safe(A.t("إضافة التلميذ"))}</a>`
              : ""
          }
        </div>
      </div>
    </article>`;

    resultEl.querySelectorAll("[data-exit-chip]").forEach((chip) => {
      chip.addEventListener("click", () => {
        resultEl.querySelectorAll("[data-exit-chip]").forEach((item) => item.classList.remove("is-active"));
        chip.classList.add("is-active");
        const input = $("exit-reason");
        if (input) {
          input.value = chip.dataset.exitChip || "";
          input.focus();
        }
      });
    });
    const issueButton = $("exit-issue");
    if (issueButton) issueButton.addEventListener("click", issueSlip);
    const clearButton = resultEl.querySelector("[data-exit-clear]");
    if (clearButton) clearButton.addEventListener("click", clearResult);
    resultEl.querySelectorAll("[data-exit-return]").forEach((button) => {
      button.addEventListener("click", () => returnSlip(button.dataset.exitReturn, button));
    });
    resultEl.setAttribute("aria-label", `${name} — ${info.label}`);
  }

  async function issueSlip() {
    if (!state.lookup || state.busy) return;
    const reasonInput = $("exit-reason");
    const noteInput = $("exit-note");
    const guardianNameInput = $("exit-guardian-name");
    const guardianPhoneInput = $("exit-guardian-phone");
    const reason = reasonInput ? reasonInput.value.trim() : "";
    if (reason.length < 2) {
      A.toast(A.t("يرجى تحديد سبب الخروج قبل إصدار الورقة."), "warning");
      if (reasonInput) reasonInput.focus();
      return;
    }
    const button = $("exit-issue");
    state.busy = true;
    A.setBusy(button, true);
    try {
      const body = {
        code: state.lookup.scanned_code,
        reason,
        note: noteInput ? noteInput.value.trim() : "",
        guardian_name: guardianNameInput ? guardianNameInput.value.trim() : "",
        guardian_phone: guardianPhoneInput ? guardianPhoneInput.value.trim() : "",
      };
      const data = await A.api("/api/exit-slip", { method: "POST", body });
      const slip = data.slip;
      A.toast(
        A.t(`تم إصدار ورقة الخروج ${slip.slip_code}. جارٍ فتح نافذة الطباعة الحرارية…`),
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
      A.toast(error.message || A.t("تعذر إصدار ورقة الخروج."), "error");
      A.flash("error");
      A.beep("error");
    } finally {
      state.busy = false;
      A.setBusy(button, false);
      updateReadyLabel();
    }
  }

  async function returnSlip(slipCode, button) {
    if (!slipCode || state.busy) return;
    state.busy = true;
    A.setBusy(button, true);
    try {
      const data = await A.api("/api/exit-slip/return", {
        method: "POST",
        body: { code: slipCode },
      });
      A.toast(
        A.t(`تم تسجيل عودة التلميذ من ورقة الخروج ${data.slip.slip_code}.`),
        "success"
      );
      A.flash("success");
      A.beep("success");
      scheduleRefresh(400);
      if (state.lookup && state.lookup.slip && state.lookup.slip.slip_code === slipCode) {
        state.lookup.slip = data.slip;
        renderLookup({ ...state.lookup, action: "already_issued", can_issue: false });
      } else {
        clearResult();
      }
    } catch (error) {
      A.toast(error.message || A.t("تعذر تسجيل العودة."), "error");
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
          <button class="btn btn-secondary btn-sm" type="button" data-exit-clear>${safe(A.t("إخفاء الرسالة"))}</button>
          <a class="btn btn-primary btn-sm" href="/settings">${safe(A.t("الإعدادات"))}</a>
        </div>
      </div>
    </article>`;
    const clearButton = resultEl.querySelector("[data-exit-clear]");
    if (clearButton) clearButton.addEventListener("click", clearResult);
  }

  function clearResult() {
    state.lookup = null;
    resultEl.innerHTML = `<div class="scan-result-empty">
      ${A.icon("exit")}
      <strong>${safe(A.t("بانتظار مسح بطاقة تلميذ"))}</strong>
      <p>${safe(A.t("ستظهر هنا بيانات التلميذ والولي، أسباب الخروج، وزر إصدار الإيصال الحراري الموقّع."))}</p>
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
    const note = $("exit-camera-note");
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
    const toggle = $("exit-camera-toggle");
    A.setBusy(toggle, true);
    try {
      const formats = await supportedFormats(window.BarcodeDetector);
      if (!formats.length) throw new Error(A.t("لا توجد صيغ باركود مدعومة في هذا المتصفح."));
      state.detector = new window.BarcodeDetector({ formats });
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false,
      });
      const video = $("exit-camera-video");
      state.stream = stream;
      video.srcObject = stream;
      await video.play();
      state.cameraOn = true;
      $("exit-camera-dock").classList.add("is-open");
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
    const video = $("exit-camera-video");
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
    const video = $("exit-camera-video");
    if (video) video.srcObject = null;
    const dock = $("exit-camera-dock");
    if (dock) dock.classList.remove("is-open");
    const toggle = $("exit-camera-toggle");
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
    const clock = $("exit-live-time");
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
    $("exit-refresh").addEventListener("click", (event) => {
      A.setBusy(event.currentTarget, true);
      loadContext();
    });
    $("exit-clear").addEventListener("click", () => {
      resetBuffer();
      clearResult();
      A.toast(A.t("تم تفريغ ذاكرة المسح والبطاقة الحالية."), "info");
    });
    $("exit-camera-toggle").addEventListener("click", () => {
      if (state.cameraOn) stopCamera();
      else startCamera();
    });
    $("exit-camera-close").addEventListener("click", stopCamera);
    $("exit-fullscreen").addEventListener("click", toggleFullscreen);
    document.addEventListener("fullscreenchange", () => {
      const button = $("exit-fullscreen");
      if (!button) return;
      const isFull = Boolean(document.fullscreenElement);
      button.classList.toggle("is-on", isFull);
      button.querySelector("span").textContent = isFull ? A.t("إنهاء ملء الشاشة") : A.t("ملء الشاشة");
    });
    $("exit-manual-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const input = $("exit-manual-code");
      const code = input.value.trim();
      if (!code) return;
      input.value = "";
      input.blur();
      processCode(code, "manual");
    });
    $("exit-recent").addEventListener("click", (event) => {
      const button = event.target.closest("[data-exit-return]");
      if (button) returnSlip(button.dataset.exitReturn, button);
    });
    document.querySelectorAll("[data-exit-template]").forEach((label) => {
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
  state.reasons = ($("exit-layout").dataset.reasons || "").split("|").filter(Boolean);
  state.template = readStoredTemplate();
  bindEvents();
  syncTemplateUi();
  setBufferHint();
  startClock();
  loadContext().then(() => {
    if (deepLinkCode) processCode(deepLinkCode, "link");
  });
  startPolling();
})();
