/* High-speed gate terminal: HID keyboard capture, camera fallback, live period stats. */
(function () {
  "use strict";

  const A = window.App;
  const HID_GAP_MS = 45;
  const HID_ENTER_GAP_MS = 300;
  const HID_MAX_LENGTH = 128;
  const RESULT_LIFETIME_MS = 12000;
  const CAMERA_DUPLICATE_MS = 3500;

  const SCAN_DIGITS = {
    "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4",
    "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9",
    "۰": "0", "۱": "1", "۲": "2", "۳": "3", "۴": "4",
    "۵": "5", "۶": "6", "۷": "7", "۸": "8", "۹": "9",
  };

  // French AZERTY keyboards report these symbols for unshifted digit keys.
  const AZERTY_DIGITS = {
    "&": "1", "é": "2", '"': "3", "'": "4", "(": "5",
    "-": "6", "è": "7", "_": "8", "ç": "9", "à": "0",
  };

  const state = {
    periodId: null,
    periods: [],
    busy: false,
    buffer: "",
    lastKeyAt: 0,
    resultTimer: null,
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
  const resultEl = $("gate-result");
  const periodSelect = $("gate-period");
  const orb = $("scan-orb");
  const bufferHint = $("gate-buffer-hint");
  const stateLabel = $("gate-state-label");

  function safe(value) {
    return A.escapeHtml(value === null || value === undefined ? "" : value);
  }

  /* ---------------------------------------------------------------------- */
  /* Context + sidebar                                                       */
  /* ---------------------------------------------------------------------- */

  async function loadContext(options = {}) {
    const silent = Boolean(options.silent);
    try {
      const params = state.periodId ? { period_id: state.periodId } : null;
      const data = await A.api(A.apiUrl("/api/gate/context", params));
      state.contextErrorShown = false;
      if (!Array.isArray(data.periods) || data.periods.length === 0) {
        periodSelect.innerHTML = '<option value="">لا توجد فترات نشطة</option>';
        stateLabel.textContent = "أضف فترة من الإعدادات";
        $("gate-period-note").classList.add("is-manual");
        $("gate-period-note").lastElementChild.textContent = "لا توجد فترة نشطة. أضف فترة من صفحة الإعدادات.";
        renderClosure(null);
        renderStats(null);
        renderRecent([]);
        if (!silent) A.toast("لا توجد فترة نشطة. أضف فترة من الإعدادات لتسجيل الحضور.", "warning");
        return data;
      }
      if (Array.isArray(data.periods)) {
        state.periods = data.periods;
        renderPeriodOptions(data.periods);
      }
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
      if (!state.busy && !state.cameraOn) {
        stateLabel.textContent = data.closure ? "الفترة مغلقة — المسح يحدّث الحالات" : "جاهز للمسح";
      }
      return data;
    } catch (error) {
      if (!silent || !state.contextErrorShown) {
        A.toast(error.message || "تعذر تحميل بيانات البوابة.", "error");
        state.contextErrorShown = true;
      }
      $("gate-recent").innerHTML = A.emptyState("warning", "تعذر تحميل البيانات", error.message || "أعد المحاولة.");
      return null;
    } finally {
      if (!silent) A.setBusy($("gate-refresh"), false);
    }
  }

  function renderPeriodOptions(periods) {
    const current = periodSelect.value;
    const options = ['<option value="">تلقائي حسب الوقت الحالي</option>'];
    periods.forEach((period) => {
      options.push(
        `<option value="${period.id}">${safe(period.period_name)} — ${safe(period.start_time)}</option>`
      );
    });
    periodSelect.innerHTML = options.join("");
    periodSelect.value = state.periodId ? String(state.periodId) : current || "";
  }

  function renderPeriodNote(data) {
    const note = $("gate-period-note");
    const text = note.querySelector("span");
    const period = data.selected_period;
    if (!period) {
      note.classList.add("is-manual");
      text.textContent = "لا توجد فترة نشطة.";
      return;
    }
    if (data.selected_is_auto) {
      note.classList.remove("is-manual");
      text.textContent = `تلقائي: ${period.period_name} (${period.start_time}) — نافذة الغياب حتى ${period.absent_cutoff_time}`;
    } else {
      note.classList.add("is-manual");
      text.textContent = `اختيار يدوي: ${period.period_name} — يُسجَّل المسح في هذه الفترة فقط.`;
    }
  }

  function renderStats(stats) {
    if (!stats) {
      ["present", "late", "absent", "unscanned"].forEach((key) => {
        $(`gate-kpi-${key}`).textContent = "0";
      });
      $("gate-rate-label").textContent = "0%";
      $("gate-rate-bar").style.width = "0%";
      return;
    }
    $("gate-kpi-present").textContent = A.formatInt(stats.present);
    $("gate-kpi-late").textContent = A.formatInt(stats.late);
    $("gate-kpi-absent").textContent = A.formatInt(stats.absent);
    $("gate-kpi-unscanned").textContent = A.formatInt(stats.unscanned);
    const rate = Number(stats.attendance_rate || 0);
    $("gate-rate-label").textContent = `${rate}%`;
    $("gate-rate-bar").style.width = `${Math.max(0, Math.min(100, rate))}%`;
  }

  function updateMeta(data) {
    const period = data.selected_period;
    const total = Number(data.total_active_students || 0);
    const anti = Number(data.anti_passback_minutes || 0);
    const bits = [A.t(`${A.formatInt(total)} تلميذ نشط`)];
    if (period) bits.push(A.t(`المجموع المسجل: ${A.formatInt((data.stats && data.stats.logged) || 0)}`));
    if (anti > 0) bits.push(A.t(`حماية التكرار: ${anti} دقائق`));
    $("gate-period-meta").textContent = bits.join(" · ");
  }

  function renderClosure(closure) {
    const holder = $("gate-closure");
    if (!closure) {
      holder.innerHTML = "";
      return;
    }
    const time = (closure.closed_at || "").slice(11, 16);
    holder.innerHTML = `
      <div class="closure-banner" style="margin-top:10px">
        ${A.icon("warning")}
        <div>أُغلقت هذه الفترة اليوم${time ? ` على الساعة <strong class="mono">${safe(time)}</strong>` : ""}. أي مسح جديد سيحوّل الغياب إلى حضور أو تأخر تلقائيًا${
          closure.sms_queued ? "، ورسائل الغياب في قائمة الانتظار." : "."
        }</div>
      </div>`;
  }

  function renderRecent(items) {
    const holder = $("gate-recent");
    $("gate-recent-count").textContent = String(items.length);
    if (!items.length) {
      holder.innerHTML = '<div class="tiny muted" style="padding:8px 0">لا توجد عمليات مسح مسجلة في هذه الفترة بعد.</div>';
      return;
    }
    holder.innerHTML = `<div class="gate-recent">${items
      .map(
        (item) => `
        <div class="gate-recent-row">
          ${A.avatar(item, "")}
          <div><strong>${safe(item.full_name || "بدون اسم")}</strong><span>${safe(item.class_group || "بدون قسم")}</span></div>
          ${A.statusBadge(item.status, item.status_label)}
          <time>${safe(item.scan_time || "—")}</time>
        </div>`
      )
      .join("")}</div>`;
  }

  function scheduleRefresh(delay = 500) {
    window.clearTimeout(state.refreshTimer);
    state.refreshTimer = window.setTimeout(() => loadContext({ silent: true }), delay);
  }

  /* ---------------------------------------------------------------------- */
  /* Scan submission + result card                                           */
  /* ---------------------------------------------------------------------- */

  async function processCode(rawCode, source) {
    const code = normalizeScanCode(rawCode);
    if (!code || state.busy) return;
    state.busy = true;
    clearResultTimer();
    orb.classList.add("is-scanning");
    stateLabel.textContent = source === "camera" ? "جارٍ التحقق من الباركود…" : "جارٍ تسجيل الحضور…";
    try {
      const payload = { code };
      if (state.periodId) payload.period_id = state.periodId;
      const data = await A.api("/api/gate/scan", { method: "POST", body: payload });
      renderScanResult(data, code);
      playFeedback(data.flash);
      if (data.stats) renderStats(data.stats);
      if (data.closure !== undefined) renderClosure(data.closure);
      scheduleRefresh(450);
      armResultTimer(data.flash === "success" || data.flash === "warning" ? RESULT_LIFETIME_MS : 8000);
    } catch (error) {
      renderErrorResult(error.message || "تعذر الاتصال بالخادم المحلي.");
      playFeedback("error");
      armResultTimer(8000);
      A.toast(error.message || "تعذر تسجيل المسح.", "error");
    } finally {
      state.busy = false;
      orb.classList.remove("is-scanning");
      updateReadyLabel();
    }
  }

  function playFeedback(flash) {
    const kind = flash === "success" ? "success" : flash === "error" ? "error" : "late";
    A.flash(kind);
    A.beep(kind);
  }

  function updateReadyLabel() {
    if (state.busy) {
      stateLabel.textContent = "جارٍ المعالجة…";
      return;
    }
    if (state.cameraOn) {
      stateLabel.textContent = "الكاميرا مفعّلة — وجّه الباركود";
      return;
    }
    stateLabel.textContent = $("gate-closure").textContent.trim()
      ? "الفترة مغلقة — المسح يحدّث الحالات"
      : "جاهز للمسح";
  }

  function headClassFor(data) {
    if (data.action === "late" || (data.attendance && data.attendance.status === "LATE")) return "is-late";
    if (["not_found", "inactive"].includes(data.action)) return "is-error";
    if (["duplicate", "already_recorded", "excused"].includes(data.action)) return "is-warning";
    if (data.flash === "error") return "is-error";
    if (data.flash === "warning") return "is-warning";
    return "";
  }

  function stateInfo(data) {
    const action = data.action;
    if (action === "present") return { icon: "check", label: "حاضر" };
    if (action === "late") return { icon: "clock", label: "متأخر" };
    if (action === "updated_from_absent") {
      return { icon: "refresh", label: (data.attendance && data.attendance.status_label) || "تحديث" };
    }
    if (action === "duplicate") return { icon: "warning", label: "مسح مكرر" };
    if (action === "already_recorded") return { icon: "check", label: "مسجل مسبقًا" };
    if (action === "excused") return { icon: "shield", label: "غياب مبرر" };
    if (action === "inactive") return { icon: "user", label: "غير نشط" };
    if (action === "preview_card") return { icon: "barcode", label: "بطاقة معاينة" };
    if (action === "not_found") return { icon: "warning", label: "غير معروف" };
    return { icon: data.success ? "check" : "warning", label: data.success ? "تم" : "تنبيه" };
  }

  function clazz(value) {
    return safe(value || "بدون قسم");
  }

  function previousAbsenceHtml(data) {
    const prev = data.previous_absence || null;
    const student = data.student;
    if (!prev || !student) return "";
    const periods = Array.isArray(prev.periods) ? prev.periods : [];
    const periodsText = periods.map((item) => item.period_name).join("، ");
    return `<div class="scan-previous" data-prev-date="${safe(prev.date)}">
      ${A.icon("warning")}
      <div class="scan-previous-body">
        <strong>${A.t("غياب غير معالج في آخر يوم دراسي")} — <span class="mono">${safe(prev.date_display)}</span></strong>
        <p>${safe(A.t(`لم تُصدر له ورقة دخول في: ${periodsText}`))}</p>
        <div class="scan-previous-actions">
          <button class="btn btn-primary btn-sm" type="button" data-prev-enter>
            ${A.icon("check")}<span>${safe(A.t("تسوية الغياب فورًا"))}</span>
          </button>
          <a class="btn btn-secondary btn-sm" href="/entry-slip?code=${encodeURIComponent(student.student_nin)}&date=${encodeURIComponent(prev.date)}">
            ${A.icon("entry")}<span>${safe(A.t("فتح ورقة الدخول"))}</span>
          </a>
        </div>
      </div>
    </div>`;
  }

  async function regularizePrevious(data) {
    const prev = data.previous_absence;
    const student = data.student;
    if (!prev || !student || state.busy) return;
    const confirmed = await A.confirm({
      title: A.t("تسوية غياب يوم سابق"),
      message: A.t(
        `سيتم تحويل غياب ${student.full_name} يوم ${prev.date_display} إلى حضور، وإلغاء رسائل الغياب المعلّقة.`
      ),
      confirmLabel: A.t("تأكيد التسوية"),
    });
    if (!confirmed) return;
    const button = resultEl.querySelector("[data-prev-enter]");
    state.busy = true;
    A.setBusy(button, true);
    try {
      const result = await A.api("/api/gate/regularize-absence", {
        method: "POST",
        body: { code: student.student_nin, date: prev.date },
      });
      A.toast(A.t(result.message), "success");
      A.flash("success");
      A.beep("success");
      data.previous_absence = null;
      if (result.sms_notice) {
        data.sms_notice = data.sms_notice
          ? `${data.sms_notice} ${result.sms_notice}`
          : result.sms_notice;
      }
      renderScanResult(data, data.scanned_code);
      armResultTimer(RESULT_LIFETIME_MS);
      scheduleRefresh(400);
    } catch (error) {
      A.toast(error.message || A.t("تعذر تسوية الغياب."), "error");
      A.flash("error");
      A.beep("error");
    } finally {
      state.busy = false;
      updateReadyLabel();
    }
  }

  function renderScanResult(data, submittedCode) {
    const student = data.student;
    const period = data.period;
    const info = stateInfo(data);
    const headClass = headClassFor(data);
    const status = data.attendance && data.attendance.status;
    const messageClass = data.flash === "error" ? "is-error" : data.flash === "warning" ? "is-warning" : "";
    const scannedCode = data.scanned_code || submittedCode;
    const name = student
      ? student.full_name || `${student.first_name || ""} ${student.last_name || ""}`.trim()
      : data.action === "preview_card"
        ? "بطاقة معاينة تجريبية"
        : "باركود غير مسجل";
    const subBits = [];
    if (student) {
      subBits.push(student.class_group || "بدون قسم");
      subBits.push(student.student_nin);
    } else {
      subBits.push(`الرمز المقروء: ${scannedCode}`);
    }
    const meta = `
      <div class="scan-meta">
        <div><span>الفترة</span><strong>${safe(period ? period.period_name : "—")}</strong></div>
        <div><span>وقت التسجيل</span><strong class="mono">${safe(data.scan_time || "—")}</strong></div>
        <div><span>التاريخ</span><strong class="mono">${safe(data.date_display || "")}</strong></div>
      </div>`;
    const today = data.student_today;
    const todayHtml = today && Array.isArray(today.periods) && today.periods.length
      ? `<div class="scan-today">
           <div class="scan-today-head"><strong>حضور اليوم</strong><span>${A.formatInt(today.attended_periods)} من ${A.formatInt(today.total_periods)} فترات</span></div>
           <div class="scan-today-track">
             ${today.periods
               .map((p) => {
                 const statusClass = p.status ? `is-${String(p.status).toLowerCase()}` : "";
                 return `<span class="${statusClass}" title="${safe(p.period_name)} — ${safe(p.status_label)}${p.scan_time ? ` (${safe(p.scan_time)})` : ""}"></span>`;
               })
               .join("")}
           </div>
         </div>`
      : "";
    const smsNote = data.sms_notice
      ? `<div class="scan-sms-note">${A.icon("message")}<span>${safe(data.sms_notice)}</span></div>`
      : "";
    const lookupNote = data.lookup_note
      ? `<div class="scan-sms-note">${A.icon("barcode")}<span>${safe(data.lookup_note)}</span></div>`
      : "";
    const suggestions = Array.isArray(data.suggestions) ? data.suggestions : [];
    const suggestHtml = suggestions.length
      ? `<div class="scan-suggest">
           <strong>هل تقصد أحد هؤلاء التلاميذ؟</strong>
           <div class="scan-suggest-list">
             ${suggestions
               .map(
                 (item) => `<a class="scan-suggest-item" href="/students?q=${encodeURIComponent(item.student_nin)}&profile=1">
                   <span>${safe(item.full_name)}</span>
                   <b class="mono">${safe(item.student_nin)}</b>
                   <em>${safe(item.class_group || "بدون قسم")}</em>
                 </a>`
               )
               .join("")}
           </div>
           <small>لم يُسجَّل أي حضور. افتح ملف التلميذ الصحيح ثم أعد المسح أو سجّل الحضور يدويًا.</small>
         </div>`
      : "";
    let actions = '<button class="btn btn-secondary btn-sm" type="button" data-scan-clear>مسح تالٍ</button>';
    if (student) {
      actions += `<a class="btn btn-secondary btn-sm" href="/attendance?date=${encodeURIComponent(data.date || "")}&period_id=${encodeURIComponent(
        period ? period.id : ""
      )}">سجل الفترة</a>`;
      actions += `<a class="btn btn-ghost btn-sm" href="/students?q=${encodeURIComponent(student.student_nin)}&profile=1">ملف التلميذ</a>`;
    } else if (data.action === "preview_card") {
      actions += `<a class="btn btn-primary btn-sm" href="/print">استوديو الطباعة</a>`;
      actions += `<a class="btn btn-ghost btn-sm" href="/students">قائمة التلاميذ</a>`;
    } else if (data.action === "not_found") {
      actions += `<a class="btn btn-secondary btn-sm" href="/students?q=${encodeURIComponent(scannedCode)}">بحث في التلاميذ</a>`;
      actions += `<a class="btn btn-primary btn-sm" href="/students?new=1&nin=${encodeURIComponent(scannedCode)}">إضافة التلميذ</a>`;
    }
    resultEl.innerHTML = `
      <article class="scan-result" data-flash="${safe(data.flash || "")}">
        <header class="scan-card-head ${headClass}">
          ${
            student
              ? A.avatar(student)
              : `<div class="avatar" aria-hidden="true">${A.icon(data.action === "preview_card" ? "barcode" : "warning")}</div>`
          }
          <div class="scan-person">
            <h3>${safe(name)}</h3>
            <p>${subBits.map((bit) => safe(bit)).join(" · ")}</p>
          </div>
          <div class="scan-state">
            ${A.icon(info.icon)}
            <strong>${safe(info.label)}</strong>
          </div>
        </header>
        <div class="scan-card-body">
          <div class="scan-message ${messageClass}">${safe(data.message || "")}</div>
          ${meta}
          ${suggestHtml}
          ${todayHtml}
          ${previousAbsenceHtml(data)}
          ${lookupNote}
          ${smsNote}
          <div class="scan-actions">${actions}</div>
        </div>
      </article>`;
    const clearBtn = resultEl.querySelector("[data-scan-clear]");
    if (clearBtn) clearBtn.addEventListener("click", clearResult);
    const prevBtn = resultEl.querySelector("[data-prev-enter]");
    if (prevBtn) prevBtn.addEventListener("click", () => regularizePrevious(data));
    // Keep this scan visible for screen readers even though it auto-clears.
    resultEl.setAttribute("aria-label", `${name} — ${info.label}`);
    return status;
  }

  function renderErrorResult(message) {
    resultEl.innerHTML = `
      <article class="scan-result">
        <header class="scan-card-head is-error">
          <div class="avatar" aria-hidden="true">${A.icon("warning")}</div>
          <div class="scan-person"><h3>تعذر تسجيل المسح</h3><p>خطأ في الاتصال أو في البيانات المرسلة</p></div>
          <div class="scan-state">${A.icon("warning")}<strong>خطأ</strong></div>
        </header>
        <div class="scan-card-body">
          <div class="scan-message is-error">${safe(message)}</div>
          <div class="scan-actions">
            <button class="btn btn-secondary btn-sm" type="button" data-scan-clear>إخفاء الرسالة</button>
            <a class="btn btn-primary btn-sm" href="/settings">الإعدادات</a>
          </div>
        </div>
      </article>`;
    const clearBtn = resultEl.querySelector("[data-scan-clear]");
    if (clearBtn) clearBtn.addEventListener("click", clearResult);
  }

  function clearResult() {
    clearResultTimer();
    resultEl.innerHTML = `
      <div class="scan-result-empty">
        ${A.icon("keyboard")}
        <strong>بانتظار أول عملية مسح</strong>
        <p>ستظهر هنا بطاقة التلميذ مع الاسم، القسم، وقت التسجيل، وحصيلة اليوم مباشرةً بعد المسح.</p>
      </div>`;
    updateReadyLabel();
  }

  function clearResultTimer() {
    window.clearTimeout(state.resultTimer);
    state.resultTimer = null;
  }

  function armResultTimer(delay) {
    clearResultTimer();
    state.resultTimer = window.setTimeout(() => {
      if (state.busy) {
        armResultTimer(3000);
        return;
      }
      clearResult();
    }, delay);
  }

  /* ---------------------------------------------------------------------- */
  /* HID keyboard-wedge capture                                              */
  /* ---------------------------------------------------------------------- */

  function isTypingTarget(target) {
    if (!target || !(target instanceof Element)) return false;
    return Boolean(target.closest("input, textarea, [contenteditable='true'], [contenteditable='']"));
  }

  function normalizeScanCode(value) {
    return String(value || "")
      .replace(/[٠-٩۰-۹]/g, (digit) => SCAN_DIGITS[digit] || digit)
      .trim();
  }

  // Physical codes are layout-independent, so HID scans survive AZERTY and Arabic layouts.
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
    bufferHint.innerHTML = `<kbd>${state.buffer.length}</kbd> حرف مستلم`;
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
      // A slow human keypress breaks the burst; fast foreign symbols are ignored.
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
    const note = $("camera-note");
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
      setCameraNote("هذا المتصفح لا يدعم قراءة الباركود من الكاميرا. استعمل قارئ USB أو متصفح Edge/Chrome الحديث.", true);
      A.toast("قراءة الباركود بالكاميرا غير مدعومة في هذا المتصفح. استعمل قارئ USB.", "warning");
      return;
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setCameraNote("لا يمكن الوصول إلى الكاميرا. تأكد من تشغيل النظام على localhost ومنح الإذن.", true);
      return;
    }
    state.cameraBusy = true;
    const toggle = $("camera-toggle");
    A.setBusy(toggle, true);
    try {
      const formats = await supportedFormats(window.BarcodeDetector);
      if (!formats.length) throw new Error("لا توجد صيغ باركود مدعومة في هذا المتصفح.");
      state.detector = new window.BarcodeDetector({ formats });
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false,
      });
      const video = $("camera-video");
      state.stream = stream;
      video.srcObject = stream;
      await video.play();
      state.cameraOn = true;
      $("camera-dock").classList.add("is-open");
      toggle.classList.add("is-on");
      toggle.querySelector("span").textContent = "إيقاف الكاميرا";
      setCameraNote("وجّه الباركود داخل الإطار. تبقى الكاميرا تعمل حتى توقفها أو تصغّر النافذة.", false);
      updateReadyLabel();
      detectFrame();
    } catch (error) {
      const name = error && error.name;
      let message = "تعذر تشغيل الكاميرا.";
      if (name === "NotAllowedError" || name === "SecurityError") {
        message = "تم رفض إذن الكاميرا. اسمح بالوصول من إعدادات المتصفح ثم أعد المحاولة.";
      } else if (name === "NotFoundError" || name === "OverconstrainedError") {
        message = "لا توجد كاميرا متصلة بهذا الجهاز.";
      } else if (name === "NotReadableError") {
        message = "الكاميرا مستعملة من طرف تطبيق آخر. أغلق التطبيق الآخر ثم أعد المحاولة.";
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
    const video = $("camera-video");
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
      // A single failed frame is normal while the video warms up; keep scanning.
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
    const video = $("camera-video");
    if (video) video.srcObject = null;
    const dock = $("camera-dock");
    if (dock) dock.classList.remove("is-open");
    const toggle = $("camera-toggle");
    if (toggle) {
      toggle.classList.remove("is-on");
      const span = toggle.querySelector("span");
      if (span) span.textContent = "تشغيل الكاميرا";
      toggle.disabled = false;
    }
    updateReadyLabel();
  }

  /* ---------------------------------------------------------------------- */
  /* Shell events                                                            */
  /* ---------------------------------------------------------------------- */

  function bindEvents() {
    periodSelect.addEventListener("change", () => {
      const value = periodSelect.value;
      state.periodId = value ? Number(value) : null;
      resetBuffer();
      loadContext();
    });
    $("gate-refresh").addEventListener("click", (event) => {
      A.setBusy(event.currentTarget, true);
      loadContext();
    });
    $("gate-clear").addEventListener("click", () => {
      resetBuffer();
      clearResult();
      A.toast("تم تفريغ ذاكرة المسح والبطاقة الحالية.", "info");
    });
    $("camera-toggle").addEventListener("click", () => {
      if (state.cameraOn) stopCamera();
      else startCamera();
    });
    $("camera-close").addEventListener("click", stopCamera);
    $("gate-fullscreen").addEventListener("click", toggleFullscreen);
    document.addEventListener("fullscreenchange", () => {
      const button = $("gate-fullscreen");
      if (!button) return;
      const isFull = Boolean(document.fullscreenElement);
      button.classList.toggle("is-on", isFull);
      button.querySelector("span").textContent = isFull ? "إنهاء ملء الشاشة" : "ملء الشاشة";
    });
    window.addEventListener("app:focus-gate", () => {
      resetBuffer();
      if (document.activeElement && typeof document.activeElement.blur === "function") {
        document.activeElement.blur();
      }
      orb.classList.add("is-scanning");
      window.setTimeout(() => orb.classList.remove("is-scanning"), 700);
    });
  }

  async function toggleFullscreen() {
    try {
      if (document.fullscreenElement) {
        await document.exitFullscreen();
      } else {
        await document.documentElement.requestFullscreen();
      }
    } catch (error) {
      A.toast("تعذر تفعيل ملء الشاشة في هذا المتصفح.", "warning");
    }
  }

  function startClock() {
    const clock = $("gate-live-time");
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

  function startPolling() {
    state.pollTimer = window.setInterval(() => {
      if (!document.hidden && !state.busy) loadContext({ silent: true });
    }, 60000);
  }

  bindEvents();
  setBufferHint();
  startClock();
  loadContext();
  startPolling();
})();
