/* Settings: institution identity, branding, schedule, periods, SMS gateway, system data. */
(function () {
  "use strict";

  const A = window.App;
  const $ = (id) => document.getElementById(id);
  const state = {
    settings: null,
    periods: [],
    saving: false,
    gateway: null,
    gatewayLoading: false,
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
    const gateway = s.sms_gateway === "cloud" ? "cloud" : "local";
    $("sms-gateway").value = gateway;
    $("sms-mobile-url").value =
      s.sms_mobile_url || (gateway === "local" ? s.sms_api_url || "" : "");
    $("sms-cloud-url").value =
      s.sms_cloud_url || (gateway === "cloud" ? s.sms_api_url || "" : "");
    $("sms-cloud-key").value =
      s.sms_cloud_key || (gateway === "cloud" ? s.sms_api_key || "" : "");
    const paired = Boolean(s.sms_mobile_key || (gateway === "local" && s.sms_api_key));
    const pairState = $("sms-pair-state");
    if (pairState) {
      pairState.className = `chip ${paired ? "is-accent" : "is-warning"}`;
      pairState.textContent = paired
        ? "الهاتف مقترن — المفتاح محفوظ"
        : "لم يتم الاقتران بعد";
    }
    $("sms-timeout").value = s.sms_timeout || 10;
    $("sms-retry").value = s.sms_retry_max || 3;
    $("sms-headers").value = s.sms_headers || "{}";
    $("sms-payload").value = s.sms_payload_template || "";
    $("sms-test-mode").checked = Boolean(s.sms_test_mode);
    $("whatsapp-enabled").checked = s.whatsapp_enabled !== false;
    $("whatsapp-country-code").value = s.whatsapp_country_code || "213";
    $("whatsapp-provider").value = s.whatsapp_provider === "cloud" ? "cloud" : "manual";
    $("whatsapp-phone-number-id").value = s.whatsapp_phone_number_id || "";
    $("whatsapp-access-token").value = s.whatsapp_access_token || "";
    $("whatsapp-template-name").value = s.whatsapp_template_name || "absence_notice";
    $("whatsapp-template-lang").value = s.whatsapp_template_lang || "ar";
    $("whatsapp-api-version").value = s.whatsapp_api_version || "v23.0";

    renderBrand("logo");
    renderBrand("stamp");
    syncGatewayUI();
    syncWhatsAppUI();
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
    badge.textContent = gateway === "cloud" ? "الافتراضي: الحاسوب/السحابة" : "الافتراضي: تطبيق الهاتف";
    const activeEndpoint = gateway === "cloud" ? "cloud" : "mobile";
    document.querySelectorAll(".channel-endpoint").forEach((card) => {
      card.classList.toggle("is-active", card.dataset.endpoint === activeEndpoint);
    });
    if (gateway === "cloud") $("gateway-cloud").open = true;
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

  function syncWhatsAppUI() {
    const cloud = $("whatsapp-provider").value === "cloud";
    $("whatsapp-manual-note").classList.toggle("hidden", cloud);
    $("whatsapp-cloud-panel").classList.toggle("hidden", !cloud);
  }

  const GATEWAY_TITLES = {
    connected: "الهاتف متصل وجاهز لإرسال SMS",
    outdated: "الهاتف متصل — يلزم تحديث تطبيق البوابة",
    auth_required: "الهاتف متصل — يلزم الاقتران برمز 4 أرقام",
    offline: "الهاتف غير متصل",
    wrong_service: "عنوان البوابة غير صحيح",
    invalid_url: "رابط بوابة الهاتف غير صالح",
    not_configured: "لم يُضبط رابط بوابة الهاتف",
    error: "تعذر فحص بوابة الهاتف",
  };

  function renderGatewayStatus() {
    const gateway = state.gateway;
    if (!gateway) return;
    const mobile = gateway.mobile || {};
    const app = gateway.gateway_app || {};
    let tone = "is-offline";
    if (mobile.status === "connected") tone = "is-online";
    else if (mobile.status === "outdated" || mobile.status === "auth_required") tone = "is-warning";
    else if (!mobile.configured && gateway.test_mode) tone = "is-pending";
    $("sms-live-dot").className = `gateway-dot ${tone}`;
    $("sms-live-title").textContent = GATEWAY_TITLES[mobile.status] || "حالة بوابة الهاتف";
    let detail = mobile.detail || "";
    if (gateway.test_mode && mobile.status !== "connected") {
      detail += " وضع الاختبار مفعّل، لذا يمكنك تجربة الإرسال دون اتصال؛ عطّله لإرسال SMS حقيقي.";
    }
    $("sms-live-detail").textContent = detail.trim();
    $("sms-live-hint").textContent = mobile.hint || "";
    $("sms-live-hint").classList.toggle("hidden", !mobile.hint);
    const version = app.version || "";
    if (mobile.version && version && mobile.version !== version) {
      $("sms-live-version").textContent = `إصدار الهاتف: ${mobile.version} · المطلوب: ${version}`;
    } else {
      $("sms-live-version").textContent = `تطبيق الهاتف: ${version || "—"}`;
    }
    $("sms-live-version").classList.remove("hidden");
    const desktop = gateway.whatsapp_desktop || {};
    const waCloud = gateway.whatsapp_cloud || {};
    const waChip = $("sms-live-wa");
    waChip.classList.remove("hidden");
    if ($("whatsapp-provider").value === "cloud") {
      if (waCloud.configured) {
        waChip.className = "chip is-accent";
        waChip.textContent = `Meta Cloud API مهيأ — القالب ${waCloud.template_name || ""}`;
      } else if (gateway.test_mode) {
        waChip.className = "chip is-warning";
        waChip.textContent = "Meta Cloud API غير مكتمل — وضع الاختبار مفعّل";
      } else {
        waChip.className = "chip is-danger";
        waChip.textContent = "Meta Cloud API غير مكتمل";
      }
    } else if (desktop.installed === true) {
      waChip.className = "chip is-accent";
      waChip.textContent = "واتساب لسطح المكتب مثبّت";
    } else if (desktop.installed === false) {
      waChip.className = "chip is-danger";
      waChip.textContent = "واتساب لسطح المكتب غير مثبّت";
    } else {
      waChip.className = "chip is-warning";
      waChip.textContent = "حالة واتساب لسطح المكتب غير معروفة";
    }
    if (app.download_url) {
      $("sms-live-download").href = app.download_url;
      $("sms-live-download").textContent = `تحميل تطبيق الهاتف ${version}`;
    }
  }

  async function loadGatewayStatus(force = false) {
    if (state.gatewayLoading) return;
    state.gatewayLoading = true;
    const button = $("sms-live-check");
    if (force) A.setBusy(button, true);
    try {
      const data = await A.api(`/api/messages/gateway${force ? "?force=1" : ""}`);
      state.gateway = data.gateway;
      renderGatewayStatus();
    } catch (error) {
      if (force) A.toast(error.message || "تعذر فحص اتصال الهاتف.", "error");
    } finally {
      state.gatewayLoading = false;
      if (force) A.setBusy(button, false);
    }
  }

  function insertTemplateVariable(variable) {
    const textarea = $("sms-template");
    const start = textarea.selectionStart ?? textarea.value.length;
    const end = textarea.selectionEnd ?? start;
    textarea.setRangeText(variable, start, end, "end");
    textarea.focus();
    updateTemplateMeta();
  }

  const PAIR_DIGITS = "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹";
  const WEST_DIGITS = "01234567890123456789";

  function normalizePin(value) {
    return String(value || "")
      .split("")
      .map((char) => {
        const index = PAIR_DIGITS.indexOf(char);
        return index >= 0 ? WEST_DIGITS[index] : char;
      })
      .join("")
      .replace(/\D/g, "")
      .slice(0, 4);
  }

  function setPairState(paired) {
    const chip = $("sms-pair-state");
    if (!chip) return;
    chip.className = `chip ${paired ? "is-accent" : "is-warning"}`;
    chip.textContent = paired ? "الهاتف مقترن — المفتاح محفوظ" : "لم يتم الاقتران بعد";
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
    const mobileUrl = $("sms-mobile-url").value.trim();
    const cloudUrl = $("sms-cloud-url").value.trim();
    const cloudKey = $("sms-cloud-key").value;
    const whatsappProvider = $("whatsapp-provider").value === "cloud" ? "cloud" : "manual";
    const whatsappPhoneId = $("whatsapp-phone-number-id").value.replace(/\D/g, "");
    const whatsappToken = $("whatsapp-access-token").value;
    const whatsappTemplate = $("whatsapp-template-name").value.trim();
    const whatsappLang = $("whatsapp-template-lang").value.trim() || "ar";
    const whatsappApiVersion = $("whatsapp-api-version").value.trim() || "v23.0";
    if (mobileUrl && /:\/\//.test(mobileUrl) && !/^https?:\/\//i.test(mobileUrl)) {
      A.toast("رابط بوابة الهاتف يجب أن يكون http:// أو https:// أو عنوان IP:منفذ.", "warning");
      $("sms-mobile-url").focus();
      return null;
    }
    if (cloudUrl && !/^https?:\/\//i.test(cloudUrl)) {
      A.toast("رابط الواجهة السحابية يجب أن يبدأ بـ http:// أو https://.", "warning");
      $("sms-cloud-url").focus();
      return null;
    }
    const activeUrl = gateway === "cloud" ? cloudUrl : mobileUrl;
    const whatsappCloudReady = Boolean(
      whatsappProvider === "cloud" && whatsappPhoneId && whatsappToken && whatsappTemplate
    );
    if (!testMode && !activeUrl && !whatsappCloudReady) {
      A.toast(
        gateway === "cloud"
          ? "أدخل رابط واجهة الحاسوب/السحابة أو أكمل بيانات Meta WhatsApp Cloud API قبل تعطيل وضع الاختبار."
          : "أدخل رابط بوابة الهاتف أو أكمل بيانات Meta WhatsApp Cloud API قبل تعطيل وضع الاختبار.",
        "warning"
      );
      $(gateway === "cloud" ? "sms-cloud-url" : "sms-mobile-url").focus();
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
      sms_api_url: activeUrl,
      sms_mobile_url: mobileUrl,
      sms_cloud_url: cloudUrl,
      sms_cloud_key: cloudKey,
      sms_test_mode: testMode,
      sms_timeout: Math.floor(timeout),
      sms_retry_max: Math.floor(retry),
      whatsapp_enabled: $("whatsapp-enabled").checked,
      whatsapp_country_code: whatsappCountry,
      whatsapp_provider: whatsappProvider,
      whatsapp_phone_number_id: whatsappPhoneId,
      whatsapp_access_token: whatsappToken,
      whatsapp_template_name: whatsappTemplate,
      whatsapp_template_lang: whatsappLang,
      whatsapp_api_version: whatsappApiVersion,
    };
    if (gateway === "cloud") {
      payload.sms_api_key = cloudKey;
    }
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
    $("whatsapp-provider").addEventListener("change", syncWhatsAppUI);
    document.querySelectorAll(".var-chip").forEach((button) => {
      button.addEventListener("click", () => insertTemplateVariable(button.dataset.var || ""));
    });
    [
      ["sms-cloud-key-toggle", "sms-cloud-key", "مفتاح واجهة الحاسوب/السحابة"],
      ["whatsapp-token-toggle", "whatsapp-access-token", "رمز وصول واتساب"],
    ].forEach(([toggleId, inputId, label]) => {
      const toggle = $(toggleId);
      if (!toggle) return;
      toggle.addEventListener("click", () => {
        const input = $(inputId);
        const show = input.type === "password";
        input.type = show ? "text" : "password";
        toggle.setAttribute("aria-label", show ? `إخفاء ${label}` : `إظهار ${label}`);
      });
    });
    $("sms-pair-now").addEventListener("click", async () => {
      const pin = normalizePin($("sms-pair-pin").value);
      if (pin.length !== 4) {
        A.toast("أدخل رمز الاقتران المكوّن من 4 أرقام الظاهر في تطبيق الهاتف.", "warning");
        $("sms-pair-pin").focus();
        return;
      }
      const button = $("sms-pair-now");
      A.setBusy(button, true);
      try {
        const data = await A.api("/api/messages/gateway/pair", {
          method: "POST",
          body: { pin, url: $("sms-mobile-url").value.trim() },
        });
        state.settings = state.settings || {};
        state.settings.sms_mobile_url = data.pairing.endpoint;
        state.settings.sms_mobile_key = "paired";
        $("sms-mobile-url").value = data.pairing.endpoint;
        $("sms-pair-pin").value = "";
        setPairState(true);
        state.gateway = data.gateway;
        renderGatewayStatus();
        A.toast(data.message || "تم اقتران الهاتف وحفظ المفتاح تلقائيًا.", data.pairing.outdated ? "warning" : "success", "اقتران الهاتف");
      } catch (error) {
        A.toast(error.message || "تعذر إكمال اقتران الهاتف.", "error");
      } finally {
        A.setBusy(button, false);
      }
    });
    $("sms-pair-pin").addEventListener("input", () => {
      $("sms-pair-pin").value = normalizePin($("sms-pair-pin").value);
    });
    $("sms-pair-pin").addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      $("sms-pair-now").click();
    });
    $("whatsapp-test-send").addEventListener("click", async () => {
      const phone = $("whatsapp-test-phone").value.trim();
      if (!phone) {
        A.toast("أدخل رقم هاتف لإجراء اختبار واتساب.", "warning");
        $("whatsapp-test-phone").focus();
        return;
      }
      const payload = smsPayload();
      if (!payload) return;
      const button = $("whatsapp-test-send");
      const saved = await saveSettings(payload, button, "تم حفظ الإعدادات قبل الاختبار.");
      if (!saved) return;
      A.setBusy(button, true);
      try {
        const data = await A.api("/api/settings/test-whatsapp", {
          method: "POST",
          body: { phone },
        });
        const result = data.result || {};
        A.toast(result.message || "اكتمل اختبار واتساب.", result.sent ? "success" : "info", "اختبار واتساب");
      } catch (error) {
        A.toast(error.message || "فشل اختبار واتساب.", "error");
      } finally {
        A.setBusy(button, false);
      }
    });
    $("sms-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const payload = smsPayload();
      if (!payload) return;
      const saved = await saveSettings(
        payload,
        $("sms-form").querySelector('button[type="submit"]'),
        "تم حفظ إعدادات رسائل SMS."
      );
      if (saved) {
        $("sms-mobile-url").value = (state.settings && state.settings.sms_mobile_url) || "";
        $("sms-cloud-url").value = (state.settings && state.settings.sms_cloud_url) || "";
        loadGatewayStatus(true);
      }
    });
    $("sms-live-check").addEventListener("click", () => loadGatewayStatus(true));
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
      loadGatewayStatus(true);
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
            <input class="input mono" id="period-start" type="time" lang="en-GB" dir="ltr" value="${safe(current.start_time)}">
          </div>
          <div class="field">
            <label for="period-cutoff">حد الحضور</label>
            <input class="input mono" id="period-cutoff" type="time" lang="en-GB" dir="ltr" value="${safe(current.on_time_cutoff)}">
            <span class="field-hint">المسح بعد هذا الوقت يُسجّل متأخرًا.</span>
          </div>
          <div class="field">
            <label for="period-absent">حد الغياب</label>
            <input class="input mono" id="period-absent" type="time" lang="en-GB" dir="ltr" value="${safe(current.absent_cutoff_time)}">
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
    loadGatewayStatus();
  }

  init();
})();
