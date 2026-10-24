/* Settings: institution identity, branding, schedule, periods, SMS gateway, system data. */
(function () {
  "use strict";

  const A = window.App;
  const $ = (id) => document.getElementById(id);
  const state = {
    settings: null,
    periods: [],
    saving: false,
  };

  const SMS_VARIABLES = {
    "[اسم_المؤسسة]": "مؤسسة النور التعليمية",
    "[اسم_التلميذ]": "محمد أمين بن علي",
    "[الاسم]": "محمد أمين",
    "[اللقب]": "بن علي",
    "[القسم]": "1AS1",
    "[اسم_الفترة]": "الفترة الصباحية",
    "[التاريخ]": "2026/10/24",
    "[السنة_الدراسية]": "2026/2025",
    "[الرقم_التعريفي]": "1099000123",
  };

  function safe(value) {
    return A.escapeHtml(value === null || value === undefined ? "" : value);
  }

  function formatBytes(value) {
    const n = Number(value) || 0;
    if (n < 1024) return `${A.formatInt(n)} بايت`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} ك.ب`;
    if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} م.ب`;
    return `${(n / (1024 * 1024 * 1024)).toFixed(2)} غ.ب`;
  }

  let savedTimer = null;
  function flashSaved() {
    const chip = $("settings-saved-chip");
    if (!chip) return;
    chip.classList.remove("hidden");
    window.clearTimeout(savedTimer);
    savedTimer = window.setTimeout(() => chip.classList.add("hidden"), 2600);
  }

  /* ------------------------------------------------------------------ */
  /* Load + apply                                                       */
  /* ------------------------------------------------------------------ */

  async function loadAll(silent) {
    try {
      const [settingsData, periodsData] = await Promise.all([
        A.api("/api/settings"),
        A.api("/api/periods"),
      ]);
      state.settings = settingsData.settings || {};
      state.periods = Array.isArray(periodsData.periods) ? periodsData.periods : [];
      applySettings();
      renderPeriods();
      renderStats();
      if (!silent) A.toast("تم تحديث الإعدادات من قاعدة البيانات.", "info", "تحديث");
    } catch (error) {
      A.toast(error.message || "تعذر تحميل الإعدادات.", "error");
    }
  }

  async function loadPeriods() {
    try {
      const data = await A.api("/api/periods");
      state.periods = Array.isArray(data.periods) ? data.periods : [];
      renderPeriods();
      renderStats();
    } catch (error) {
      A.toast(error.message || "تعذر تحميل الفترات.", "error");
    }
  }

  function applySettings() {
    const s = state.settings || {};
    $("school-name").value = s.school_name || "";
    $("academic-year").value = s.academic_year || "";
    $("principal-name").value = s.principal_name || "";
    $("header-text").value = s.header_text || "";

    $("auto-sweep").checked = Boolean(s.auto_sweep);
    $("anti-passback").value = s.anti_passback_minutes || 5;
    const days = Array.isArray(s.school_weekdays) ? s.school_weekdays.map(String) : [];
    document.querySelectorAll('input[name="school_weekdays"]').forEach((box) => {
      box.checked = days.includes(box.value);
    });

    $("sms-template").value = s.sms_template || "";
    $("sms-gateway").value = s.sms_gateway === "cloud" ? "cloud" : "local";
    $("sms-api-url").value = s.sms_api_url || "";
    $("sms-api-key").value = s.sms_api_key || "";
    $("sms-timeout").value = s.sms_timeout || 10;
    $("sms-retry").value = s.sms_retry_max || 3;
    $("sms-headers").value = s.sms_headers || "{}";
    $("sms-payload").value = s.sms_payload_template || "";
    $("sms-test-mode").checked = Boolean(s.sms_test_mode);
    $("whatsapp-enabled").checked = s.whatsapp_enabled !== false;
    $("whatsapp-country-code").value = s.whatsapp_country_code || "213";

    renderBrand("logo");
    renderBrand("stamp");
    syncGatewayUI();
    updateTemplateMeta();
  }

  function renderStats() {
    const s = state.settings || {};
    $("settings-students").textContent = A.formatInt(s.students_count);
    $("settings-active-students").textContent = A.formatInt(s.active_students);
    $("settings-periods-count").textContent = A.formatInt(state.periods.length || s.periods_count);
    $("settings-db-size").textContent = formatBytes(s.db_size);
    $("settings-data-dir").textContent = s.data_dir || "—";
    $("settings-version").textContent = `الإصدار ${s.version || ""}`.trim();
  }

  /* ------------------------------------------------------------------ */
  /* Branding                                                           */
  /* ------------------------------------------------------------------ */

  function renderBrand(kind) {
    const s = state.settings || {};
    const url = s[`${kind}_url`] || "";
    const preview = $(`${kind}-preview`);
    const remove = $(`${kind}-remove`);
    if (!preview) return;
    if (url) {
      preview.innerHTML = `<img src="${safe(url)}" alt="${kind === "logo" ? "شعار المؤسسة" : "ختم المؤسسة"}">`;
      const img = preview.querySelector("img");
      if (img) {
        img.addEventListener("error", () => {
          preview.innerHTML = A.icon(kind === "logo" ? "school" : "stamp");
          if (remove) remove.classList.add("hidden");
        });
      }
    } else {
      preview.innerHTML = A.icon(kind === "logo" ? "school" : "stamp");
    }
    if (remove) remove.classList.toggle("hidden", !url);
  }

  async function uploadBrand(kind, file, button) {
    if (!file) return;
    if (!["image/png", "image/jpeg", "image/webp"].includes(file.type)) {
      A.toast("صيغة الصورة غير مدعومة. استعمل PNG أو JPG أو WEBP.", "warning");
      return;
    }
    if (file.size > 3 * 1024 * 1024) {
      A.toast("حجم الصورة يتجاوز 3 ميغابايت.", "warning");
      return;
    }
    const form = new FormData();
    form.append("file", file);
    A.setBusy(button, true);
    try {
      const data = await A.api(`/api/uploads/${kind}`, { method: "POST", form });
      state.settings[`${kind}_url`] = data.url;
      renderBrand(kind);
      flashSaved();
      A.toast(kind === "logo" ? "تم رفع شعار المؤسسة." : "تم رفع ختم المؤسسة.", "success");
    } catch (error) {
      A.toast(error.message || "تعذر رفع الصورة.", "error");
    } finally {
      A.setBusy(button, false);
    }
  }

  function bindBranding() {
    ["logo", "stamp"].forEach((kind) => {
      const button = document.querySelector(`[data-upload="${kind}"]`);
      const input = $(`${kind}-file`);
      const remove = $(`${kind}-remove`);
      button.addEventListener("click", () => input.click());
      input.addEventListener("change", () => {
        const file = input.files && input.files[0];
        input.value = "";
        uploadBrand(kind, file, button);
      });
      if (remove) {
        remove.addEventListener("click", async () => {
          if (!state.settings || !state.settings[`${kind}_url`]) return;
          const confirmed = await A.confirm({
            title: "إزالة الصورة",
            message: kind === "logo"
              ? "سيُحذف شعار المؤسسة من المطبوعات. هل تريد المتابعة؟"
              : "سيُحذف ختم المؤسسة من المطبوعات. هل تريد المتابعة؟",
            danger: true,
            confirmLabel: "إزالة",
          });
          if (!confirmed) return;
          try {
            await A.api(`/api/uploads/${kind}`, { method: "DELETE" });
            state.settings[`${kind}_url`] = "";
            renderBrand(kind);
            flashSaved();
            A.toast("تم حذف الصورة.", "success");
          } catch (error) {
            A.toast(error.message || "تعذر حذف الصورة.", "error");
          }
        });
      }
    });
  }

  /* ------------------------------------------------------------------ */
  /* Saving                                                             */
  /* ------------------------------------------------------------------ */

  async function saveSettings(payload, button, successMessage) {
    A.setBusy(button, true);
    try {
      const data = await A.api("/api/settings", { method: "PUT", body: payload });
      state.settings = { ...(state.settings || {}), ...(data.settings || {}) };
      renderStats();
      flashSaved();
      A.toast(data.message || successMessage || "تم حفظ الإعدادات.", "success");
      return true;
    } catch (error) {
      A.toast(error.message || "تعذر حفظ الإعدادات.", "error");
      return false;
    } finally {
      A.setBusy(button, false);
    }
  }

  function bindInstitutionForm() {
    $("institution-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const name = $("school-name").value.trim();
      if (!name) {
        A.toast("اسم المؤسسة مطلوب.", "warning");
        $("school-name").focus();
        return;
      }
      saveSettings(
        {
          school_name: name,
          academic_year: $("academic-year").value.trim(),
          principal_name: $("principal-name").value.trim(),
          header_text: $("header-text").value.trim(),
        },
        $("institution-form").querySelector('button[type="submit"]'),
        "تم حفظ بيانات المؤسسة."
      );
    });
  }

  function bindScheduleForm() {
    $("schedule-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const days = Array.from(document.querySelectorAll('input[name="school_weekdays"]:checked')).map((box) => Number(box.value));
      if (!days.length) {
        A.toast("اختر يومًا دراسيًا واحدًا على الأقل.", "warning");
        return;
      }
      const antiPassback = Number($("anti-passback").value);
      if (!Number.isFinite(antiPassback) || antiPassback < 1 || antiPassback > 60) {
        A.toast("مدة منع التكرار يجب أن تكون بين 1 و60 دقيقة.", "warning");
        $("anti-passback").focus();
        return;
      }
      saveSettings(
        {
          school_weekdays: days,
          auto_sweep: $("auto-sweep").checked,
          anti_passback_minutes: Math.floor(antiPassback),
        },
        $("schedule-form").querySelector('button[type="submit"]'),
        "تم حفظ إعدادات الدوام."
      );
    });
  }

  /* ------------------------------------------------------------------ */
  /* SMS                                                                */
  /* ------------------------------------------------------------------ */

  function updateTemplateMeta() {
    const textarea = $("sms-template");
    const value = textarea.value || "";
    $("sms-template-count").textContent = `${value.length} / 2000`;
    const preview = $("sms-template-preview");
    if (!value.trim()) {
      preview.textContent = "اكتب قالب الرسالة لعرض المعاينة.";
      return;
    }
    preview.textContent = value.replace(/\[[^\[\]]+\]/g, (match) => SMS_VARIABLES[match] || match);
  }

  function syncGatewayUI() {
    const gateway = $("sms-gateway").value;
    const badge = $("sms-gateway-badge");
    badge.textContent = gateway === "cloud" ? "واجهة سحابية" : "بوابة محلية";
    $("sms-api-url").placeholder = gateway === "cloud"
      ? "https://api.example.com/sms/send"
      : "http://127.0.0.1:8080/send";
    const cloud = $("gateway-cloud");
    if (gateway === "cloud") cloud.open = true;
    syncTestModeNote();
  }

  function syncTestModeNote() {
    const enabled = $("sms-test-mode").checked;
    const note = $("sms-mode-note");
    const text = $("sms-mode-text");
    note.classList.toggle("is-warning", !enabled);
    const use = note.querySelector("use");
    if (use) use.setAttribute("href", enabled ? "#i-shield-check" : "#i-warning");
    text.textContent = enabled
      ? "وضع الاختبار مفعّل: تُحاكى عمليات الإرسال دون استخدام الشبكة أو استهلاك رصيد."
      : "وضع الاختبار معطّل: ستُرسل رسائل حقيقية عبر البوابة المضبوطة. تأكد من صحة الرابط والمفتاح قبل الإرسال.";
  }

  function insertTemplateVariable(variable) {
    const textarea = $("sms-template");
    const start = textarea.selectionStart ?? textarea.value.length;
    const end = textarea.selectionEnd ?? start;
    textarea.setRangeText(variable, start, end, "end");
    textarea.focus();
    updateTemplateMeta();
  }

  function smsPayload() {
    const template = $("sms-template").value.trim();
    if (!template) {
      A.toast("قالب رسالة SMS لا يمكن أن يكون فارغًا.", "warning");
      $("sms-template").focus();
      return null;
    }
    const gateway = $("sms-gateway").value;
    const testMode = $("sms-test-mode").checked;
    const apiUrl = $("sms-api-url").value.trim();
    if (apiUrl && !/^https?:\/\//i.test(apiUrl)) {
      A.toast("رابط بوابة SMS يجب أن يبدأ بـ http:// أو https://.", "warning");
      $("sms-api-url").focus();
      return null;
    }
    if (!testMode && !apiUrl) {
      A.toast("أدخل رابط بوابة SMS قبل تعطيل وضع الاختبار.", "warning");
      $("sms-api-url").focus();
      return null;
    }
    const timeout = Number($("sms-timeout").value);
    if (!Number.isFinite(timeout) || timeout < 3 || timeout > 120) {
      A.toast("مهلة الاتصال يجب أن تكون بين 3 و120 ثانية.", "warning");
      $("sms-timeout").focus();
      return null;
    }
    const retry = Number($("sms-retry").value);
    if (!Number.isFinite(retry) || retry < 1 || retry > 10) {
      A.toast("عدد محاولات الإرسال يجب أن يكون بين 1 و10.", "warning");
      $("sms-retry").focus();
      return null;
    }
    const whatsappCountry = $("whatsapp-country-code").value.replace(/\D/g, "");
    if (!/^\d{1,4}$/.test(whatsappCountry)) {
      A.toast("رمز الدولة يجب أن يتكون من 1 إلى 4 أرقام.", "warning");
      $("whatsapp-country-code").focus();
      return null;
    }
    const payload = {
      sms_template: template,
      sms_gateway: gateway,
      sms_api_url: apiUrl,
      sms_api_key: $("sms-api-key").value,
      sms_test_mode: testMode,
      sms_timeout: Math.floor(timeout),
      sms_retry_max: Math.floor(retry),
      whatsapp_enabled: $("whatsapp-enabled").checked,
      whatsapp_country_code: whatsappCountry,
    };
    if (gateway === "cloud") {
      const headersRaw = $("sms-headers").value.trim() || "{}";
      const templateRaw = $("sms-payload").value.trim() || '{"phone": "{{phone}}", "message": "{{message}}"}';
      try {
        JSON.parse(headersRaw);
      } catch (error) {
        A.toast("ترويسات HTTP ليست JSON صالحًا.", "warning");
        $("sms-headers").focus();
        return null;
      }
      try {
        JSON.parse(templateRaw);
      } catch (error) {
        A.toast("قالب JSON للطلب غير صالح.", "warning");
        $("sms-payload").focus();
        return null;
      }
      payload.sms_headers = headersRaw;
      payload.sms_payload_template = templateRaw;
    }
    return payload;
  }

  function bindSmsForm() {
    $("sms-template").addEventListener("input", updateTemplateMeta);
    $("sms-gateway").addEventListener("change", syncGatewayUI);
    $("sms-test-mode").addEventListener("change", syncTestModeNote);
    document.querySelectorAll(".var-chip").forEach((button) => {
      button.addEventListener("click", () => insertTemplateVariable(button.dataset.var || ""));
    });
    $("sms-api-key-toggle").addEventListener("click", () => {
      const input = $("sms-api-key");
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      $("sms-api-key-toggle").setAttribute("aria-label", show ? "إخفاء المفتاح" : "إظهار المفتاح");
    });
    $("sms-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const payload = smsPayload();
      if (!payload) return;
      saveSettings(
        payload,
        $("sms-form").querySelector('button[type="submit"]'),
        "تم حفظ إعدادات رسائل SMS."
      );
    });
    $("sms-test-send").addEventListener("click", async () => {
      const phone = $("sms-test-phone").value.trim();
      if (!phone) {
        A.toast("أدخل رقم هاتف لإجراء الاختبار.", "warning");
        $("sms-test-phone").focus();
        return;
      }
      const payload = smsPayload();
      if (!payload) return;
      const button = $("sms-test-send");
      const saved = await saveSettings(payload, button, "تم حفظ الإعدادات قبل الاختبار.");
      if (!saved) return;
      A.setBusy(button, true);
      try {
        const data = await A.api("/api/settings/test-sms", {
          method: "POST",
          body: { phone },
        });
        const result = data.result || {};
        A.toast(result.message || "اكتمل الاختبار.", result.sent ? "success" : "info", "اختبار بوابة SMS");
      } catch (error) {
        A.toast(error.message || "فشل اختبار بوابة SMS.", "error");
      } finally {
        A.setBusy(button, false);
      }
    });
  }

  /* ------------------------------------------------------------------ */
  /* Periods                                                            */
  /* ------------------------------------------------------------------ */

  function renderPeriods() {
    const list = $("periods-list");
    if (!state.periods.length) {
      list.innerHTML = `<div class="period-row"><span class="muted">لا توجد فترات بعد. أضف فترة لبدء تسجيل الحضور.</span></div>`;
      return;
    }
    const rows = [
      `<div class="period-row is-head"><span>الفترة</span><span style="text-align:center">البداية</span><span style="text-align:center">حد الحضور</span><span style="text-align:center">حد الغياب</span><span></span></div>`,
    ];
    state.periods.forEach((period) => {
      const badge = period.is_active
        ? '<span class="badge badge--present"><i class="badge-dot"></i>نشطة</span>'
        : '<span class="badge badge--cancelled"><i class="badge-dot"></i>معطّلة</span>';
      rows.push(`
        <div class="period-row" data-period-id="${safe(period.id)}">
          <div class="period-name">
            ${A.icon("clock", "period-ico")}
            <span>${safe(period.period_name)}</span>
            ${badge}
          </div>
          <time>${safe(period.start_time)}</time>
          <time>${safe(period.on_time_cutoff)}</time>
          <time>${safe(period.absent_cutoff_time)}</time>
          <div class="actions">
            <button class="icon-btn" type="button" data-period-edit="${safe(period.id)}" title="تعديل الفترة" aria-label="تعديل ${safe(period.period_name)}">
              ${A.icon("edit")}
            </button>
            <button class="icon-btn" type="button" data-period-delete="${safe(period.id)}" title="حذف الفترة" aria-label="حذف ${safe(period.period_name)}">
              ${A.icon("trash")}
            </button>
          </div>
        </div>`);
    });
    list.innerHTML = rows.join("");
  }

  function toSeconds(value) {
    const parts = String(value || "").split(":");
    if (parts.length < 2) return NaN;
    return Number(parts[0]) * 3600 + Number(parts[1]) * 60;
  }

  function openPeriodModal(period) {
    const isEdit = Boolean(period);
    const current = period || {
      period_name: "",
      start_time: "08:00",
      on_time_cutoff: "08:10",
      absent_cutoff_time: "08:30",
      is_active: true,
      sort_order: state.periods.length + 1,
    };
    A.openModal({
      title: isEdit ? "تعديل الفترة الدراسية" : "إضافة فترة دراسية",
      subtitle: "حدد اسم الفترة وأوقات البداية وحدود الحضور والغياب.",
      icon: "clock",
      size: "lg",
      body: `
        <div class="form-grid">
          <div class="field form-span-2">
            <label for="period-name">اسم الفترة</label>
            <input class="input" id="period-name" maxlength="80" value="${safe(current.period_name)}" placeholder="الفترة الصباحية" autofocus>
          </div>
          <div class="field">
            <label for="period-start">وقت البداية</label>
            <input class="input mono" id="period-start" type="time" value="${safe(current.start_time)}">
          </div>
          <div class="field">
            <label for="period-cutoff">حد الحضور</label>
            <input class="input mono" id="period-cutoff" type="time" value="${safe(current.on_time_cutoff)}">
            <span class="field-hint">المسح بعد هذا الوقت يُسجّل متأخرًا.</span>
          </div>
          <div class="field">
            <label for="period-absent">حد الغياب</label>
            <input class="input mono" id="period-absent" type="time" value="${safe(current.absent_cutoff_time)}">
            <span class="field-hint">بعد هذا الوقت يصبح التلميذ غائبًا عند الرصد التلقائي.</span>
          </div>
          <div class="field">
            <label for="period-sort">ترتيب العرض</label>
            <input class="input" id="period-sort" type="number" min="0" max="999" step="1" value="${safe(current.sort_order)}">
          </div>
          <label class="switch form-span-2" style="padding-top:4px">
            <input type="checkbox" id="period-active" ${current.is_active ? "checked" : ""}>
            <span class="switch-track"></span>
            <span class="switch-text">
              <strong>فترة نشطة</strong>
              <span>تظهر في بوابة المسح وسجل الحضور. عطّل الفترة لإخفائها دون حذف سجلاتها.</span>
            </span>
          </label>
        </div>`,
      footer: `
        <button class="btn btn-secondary" type="button" data-modal-close>إلغاء</button>
        <button class="btn btn-primary" type="button" data-save-period>
          ${A.icon("check")}<span>${isEdit ? "حفظ التعديلات" : "إضافة الفترة"}</span>
        </button>`,
      onMount(modal, { close }) {
        const saveButton = modal.querySelector("[data-save-period]");
        const submit = async () => {
          const name = modal.querySelector("#period-name").value.trim();
          const start = modal.querySelector("#period-start").value;
          const cutoff = modal.querySelector("#period-cutoff").value;
          const absent = modal.querySelector("#period-absent").value;
          const sort = Number(modal.querySelector("#period-sort").value);
          if (!name) {
            A.toast("اسم الفترة مطلوب.", "warning");
            modal.querySelector("#period-name").focus();
            return;
          }
          if (!start || !cutoff || !absent) {
            A.toast("أدخل جميع أوقات الفترة.", "warning");
            return;
          }
          if (!(toSeconds(start) <= toSeconds(cutoff) && toSeconds(cutoff) <= toSeconds(absent))) {
            A.toast("يجب أن يكون وقت البداية ≤ حد الحضور ≤ حد الغياب.", "warning");
            return;
          }
          const payload = {
            period_name: name,
            start_time: start,
            on_time_cutoff: cutoff,
            absent_cutoff_time: absent,
            sort_order: Number.isFinite(sort) && sort >= 0 ? Math.floor(sort) : state.periods.length + 1,
            is_active: modal.querySelector("#period-active").checked,
          };
          A.setBusy(saveButton, true);
          try {
            const data = await A.api(
              isEdit ? `/api/periods/${current.id}` : "/api/periods",
              { method: isEdit ? "PUT" : "POST", body: payload }
            );
            close(true);
            A.toast(data.message || "تم حفظ الفترة.", "success");
            await loadPeriods();
          } catch (error) {
            A.toast(error.message || "تعذر حفظ الفترة.", "error");
          } finally {
            A.setBusy(saveButton, false);
          }
        };
        saveButton.addEventListener("click", submit);
        modal.querySelectorAll("input").forEach((input) => {
          input.addEventListener("keydown", (event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              submit();
            }
          });
        });
      },
    });
  }

  function bindPeriods() {
    $("period-add").addEventListener("click", () => openPeriodModal(null));
    $("periods-list").addEventListener("click", async (event) => {
      const edit = event.target.closest("[data-period-edit]");
      if (edit) {
        const period = state.periods.find((item) => String(item.id) === String(edit.dataset.periodEdit));
        if (period) openPeriodModal(period);
        return;
      }
      const remove = event.target.closest("[data-period-delete]");
      if (!remove) return;
      const period = state.periods.find((item) => String(item.id) === String(remove.dataset.periodDelete));
      if (!period) return;
      const confirmed = await A.confirm({
        title: "حذف الفترة",
        message: `هل تريد حذف «${period.period_name}»؟ لا يمكن التراجع عن الحذف، وستُرفض العملية إذا كانت مرتبطة بسجلات حضور.`,
        danger: true,
        confirmLabel: "حذف الفترة",
      });
      if (!confirmed) return;
      try {
        await A.api(`/api/periods/${period.id}`, { method: "DELETE" });
        A.toast("تم حذف الفترة.", "success");
        await loadPeriods();
      } catch (error) {
        A.toast(error.message || "تعذر حذف الفترة.", "error");
      }
    });
  }

  /* ------------------------------------------------------------------ */
  /* Data + navigation                                                  */
  /* ------------------------------------------------------------------ */

  function bindDataActions() {
    $("settings-backup").addEventListener("click", () => {
      A.download("/api/system/backup");
      A.toast("جارٍ تجهيز نسخة احتياطية من قاعدة البيانات.", "info", "نسخ احتياطي");
    });
    $("settings-refresh").addEventListener("click", async () => {
      const button = $("settings-refresh");
      A.setBusy(button, true);
      try {
        await loadAll(false);
      } finally {
        A.setBusy(button, false);
      }
    });
  }

  function initNavSpy() {
    const links = Array.from(document.querySelectorAll(".settings-nav a[href^='#']"));
    const sections = links
      .map((link) => document.querySelector(link.getAttribute("href")))
      .filter(Boolean);
    if (!sections.length) return;
    const setActive = (id) => {
      links.forEach((link) => link.classList.toggle("is-active", link.getAttribute("href") === `#${id}`));
    };
    if (!("IntersectionObserver" in window)) return;
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort((a, b) => b.intersectionRatio - a.intersectionRatio);
        if (visible[0]) setActive(visible[0].target.id);
      },
      { rootMargin: "-20% 0px -65% 0px", threshold: [0, 0.1, 0.5] }
    );
    sections.forEach((section) => observer.observe(section));
    setActive(sections[0].id);
  }

  function init() {
    bindInstitutionForm();
    bindBranding();
    bindScheduleForm();
    bindSmsForm();
    bindPeriods();
    bindDataActions();
    initNavSpy();
    loadAll(true);
  }

  init();
})();
