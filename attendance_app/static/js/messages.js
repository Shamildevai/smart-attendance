/* Bulk parent messaging center: recipients, channels, WhatsApp hand-off, SMS jobs. */
(function () {
  "use strict";

  const A = window.App;
  const $ = (id) => document.getElementById(id);

  const els = {
    statusChips: $("msg-status-chips"),
    gatewayStrip: $("msg-gateway-strip"),
    gatewayDot: $("msg-gateway-dot"),
    gatewayTitle: $("msg-gateway-title"),
    gatewayDetail: $("msg-gateway-detail"),
    gatewayHint: $("msg-gateway-hint"),
    gatewayUrl: $("msg-gateway-url"),
    gatewayVersion: $("msg-gateway-version"),
    gatewayTestChip: $("msg-gateway-testchip"),
    gatewayDownload: $("msg-gateway-download"),
    gatewayCheck: $("msg-gateway-check"),
    gatewayPair: $("msg-gateway-pair"),
    scanPhonesList: $("scan-phones-list"),
    scanPhonesAdd: $("scan-phones-add"),
    scanPhonesRefresh: $("scan-phones-refresh"),
    scanPhonesNote: $("scan-phones-note"),
    whatsappSubtitle: $("msg-whatsapp-subtitle"),
    whatsappDesc: $("msg-whatsapp-desc"),
    whatsappDesktopExtra: $("msg-whatsapp-desktop-extra"),
    whatsappDesktopChip: $("msg-whatsapp-desktop-chip"),
    totalChip: $("msg-total-chip"),
    refresh: $("msg-refresh"),
    scope: $("msg-scope"),
    scopeButtons: Array.from(document.querySelectorAll("[data-scope]")),
    scopeAllCount: $("msg-scope-all-count"),
    scopeSelectedCount: $("msg-scope-selected-count"),
    classPanel: $("msg-class-panel"),
    classFilter: $("msg-class-filter"),
    selectedPanel: $("msg-selected-panel"),
    pick: $("msg-pick-students"),
    clearSelected: $("msg-clear-selected"),
    selectedSummary: $("msg-selected-summary"),
    selectedChips: $("msg-selected-chips"),
    summaryTotal: $("msg-summary-total"),
    summaryEligible: $("msg-summary-eligible"),
    summaryEligibleLabel: $("msg-summary-eligible-label"),
    summarySkipped: $("msg-summary-skipped"),
    summaryClasses: $("msg-summary-classes"),
    skippedNote: $("msg-skipped-note"),
    skippedText: $("msg-skipped-text"),
    channels: $("msg-channels"),
    channelCards: Array.from(document.querySelectorAll(".channel-card")),
    channelRadios: Array.from(document.querySelectorAll('input[name="msg-channel"]')),
    testNote: $("msg-test-note"),
    channelWarning: $("msg-channel-warning"),
    channelWarningText: $("msg-channel-warning-text"),
    body: $("msg-body"),
    bodyCount: $("msg-body-count"),
    preview: $("msg-preview"),
    previewBody: $("msg-preview-body"),
    segmentChip: $("msg-segment-chip"),
    approve: $("msg-approve"),
    approvalText: $("msg-approval-text"),
    send: $("msg-send"),
    submitHint: $("msg-submit-hint"),
    progress: $("msg-progress"),
    history: $("msg-history"),
    historyLabel: $("msg-history-label"),
    historyRefresh: $("msg-history-refresh"),
  };

  const state = {
    students: [],
    counts: { total: 0, sms_ready: 0, whatsapp_ready: 0, missing_phone: 0 },
    classes: [],
    channels: { channels: [], test_mode: true, whatsapp_enabled: true },
    defaults: {},
    scope: "all",
    classGroup: "",
    selected: new Set(),
    channel: "mobile",
    preview: null,
    previewRequest: 0,
    previewTimer: null,
    sending: false,
    historyPage: 1,
    jobWatcher: null,
    gateway: null,
    gatewayLoading: false,
    gatewayTimer: null,
    scanPhones: [],
    scanPhonesPrimary: null,
    scanPhonesLoading: false,
    loading: true,
  };

  const CHANNEL_ICONS = { mobile: "phone", cloud: "dashboard", whatsapp: "whatsapp" };
  const CHANNEL_SHORT = { mobile: "SMS من الهاتف", cloud: "SMS من الحاسوب", whatsapp: "واتساب" };

  function safe(value) {
    return A.escapeHtml(value === null || value === undefined ? "" : value);
  }

  function channelInfo(id) {
    return (state.channels.channels || []).find((item) => item.id === id) || {};
  }

  function whatsappProvider() {
    return state.channels.whatsapp_provider === "cloud" || channelInfo("whatsapp").mode === "cloud"
      ? "cloud"
      : "manual";
  }

  function whatsappCloud() {
    return whatsappProvider() === "cloud";
  }

  function bodyLimit() {
    return whatsappCloud() && state.channel === "whatsapp" ? 1024 : 2000;
  }

  function renderBodyCount() {
    els.bodyCount.textContent = `${A.formatInt(els.body.value.length)} / ${A.formatInt(bodyLimit())}`;
  }

  function gatewayMobile() {
    return state.gateway && state.gateway.mobile ? state.gateway.mobile : null;
  }

  function gatewayCanSend() {
    const mobile = gatewayMobile();
    return Boolean(mobile && mobile.can_send);
  }

  function eligible(student, channel) {
    return channel === "whatsapp" ? Boolean(student.whatsapp_ready) : Boolean(student.sms_ready);
  }

  function recipients() {
    if (state.scope === "class") {
      return state.students.filter((student) =>
        state.classGroup === "__none__"
          ? !student.class_group_raw
          : student.class_group_raw === state.classGroup
      );
    }
    if (state.scope === "selected") {
      return state.students.filter((student) => state.selected.has(student.student_nin));
    }
    return state.students;
  }

  function eligibleRecipients() {
    return recipients().filter((student) => eligible(student, state.channel));
  }

  function uniqueClasses(students) {
    const map = new Map();
    students.forEach((student) => {
      const key = student.class_group_raw || "__none__";
      const label = student.class_group || "بدون قسم";
      if (!map.has(key)) map.set(key, { value: key, label, count: 0 });
      map.get(key).count += 1;
    });
    return Array.from(map.values()).sort((a, b) => {
      if (a.value === "__none__") return 1;
      if (b.value === "__none__") return -1;
      return a.label.localeCompare(b.label, "ar");
    });
  }

  /* ---------------------------------------------------------------------- */
  /* Loading                                                                 */
  /* ---------------------------------------------------------------------- */

  async function loadStudents(options = {}) {
    state.loading = true;
    if (!options.silent) {
      els.submitHint.textContent = "جارٍ تحميل بيانات التلاميذ…";
      updateSendState();
    }
    try {
      const data = await A.api("/api/messages/students");
      state.students = Array.isArray(data.students) ? data.students : [];
      state.counts = data.counts || state.counts;
      state.classes = Array.isArray(data.classes) ? data.classes : [];
      state.channels = data.channels || state.channels;
      state.defaults = data.defaults || {};
      populateClasses();
      renderStatus();
      renderChannels();
      renderChannelLiveStates();
      renderScope();
      renderRecipientSummary();
      renderSelected();
      if (!els.body.value && state.defaults.sms_template && state.students.length === 0) {
        els.submitHint.textContent = "لا يوجد تلاميذ بعد. أضف التلاميذ أولًا من صفحة التلاميذ.";
      }
    } catch (error) {
      A.toast(error.message || "تعذر تحميل بيانات المستلمين.", "error");
      els.submitHint.textContent = "تعذر تحميل بيانات التلاميذ. أعد المحاولة.";
    } finally {
      state.loading = false;
      updateSendState();
    }
  }

  function populateClasses() {
    const options = state.classes
      .filter((item) => Number(item.count) > 0)
      .map((item) => `<option value="${safe(item.class_group === "بدون قسم" ? "__none__" : item.class_group)}">${safe(item.class_group)} (${A.formatInt(item.count)})</option>`)
      .join("");
    if (options) {
      const previous = state.classGroup;
      els.classFilter.innerHTML = options;
      const values = Array.from(els.classFilter.options).map((option) => option.value);
      state.classGroup = values.includes(previous) ? previous : values[0];
      els.classFilter.value = state.classGroup;
      const classButton = els.scopeButtons.find((button) => button.dataset.scope === "class");
      if (classButton) classButton.disabled = false;
    } else {
      els.classFilter.innerHTML = '<option value="">لا توجد أقسام</option>';
      state.classGroup = "";
      const classButton = els.scopeButtons.find((button) => button.dataset.scope === "class");
      if (classButton) classButton.disabled = true;
      if (state.scope === "class") state.scope = "all";
    }
  }

  function renderStatus() {
    const testMode = Boolean(state.channels.test_mode);
    const mobile = channelInfo("mobile");
    const cloud = channelInfo("cloud");
    const whatsapp = channelInfo("whatsapp");
    const gateway = gatewayMobile();
    const desktop = state.gateway && state.gateway.whatsapp_desktop;
    let mobileTone = "";
    let mobileText = "جارٍ الفحص";
    if (!mobile.configured && !testMode) {
      mobileTone = "is-danger";
      mobileText = "غير مهيأ";
    } else if (!state.gateway) {
      mobileTone = testMode ? "is-warning" : "";
      mobileText = testMode ? "محاكاة فقط" : "جارٍ الفحص";
    } else if (gateway.status === "connected") {
      mobileText = "متصل وجاهز";
    } else if (gateway.status === "outdated") {
      mobileTone = "is-warning";
      mobileText = "يلزم تحديث التطبيق";
    } else if (gateway.status === "auth_required") {
      mobileTone = "is-warning";
      mobileText = "يلزم الاقتران برمز";
    } else if (gateway.status === "not_configured") {
      mobileTone = testMode ? "is-warning" : "is-danger";
      mobileText = testMode ? "محاكاة" : "غير مهيأ";
    } else {
      mobileTone = testMode ? "is-warning" : "is-danger";
      mobileText = testMode ? "غير متصل (محاكاة)" : "غير متصل";
    }
    let waTone = whatsapp.ready ? "is-accent" : "is-danger";
    let waText = whatsapp.ready
      ? whatsappCloud()
        ? (state.channels.test_mode ? "Meta Cloud — محاكاة" : "Meta Cloud جاهز")
        : "متاح يدويًا"
      : "معطّل";
    if (!whatsappCloud() && whatsapp.ready && desktop && desktop.installed === false) {
      waTone = "is-warning";
      waText = "يلزم تثبيت واتساب";
    }
    const chips = [
      `<span class="chip ${testMode ? "is-warning" : ""}" id="msg-test-chip">${A.icon(testMode ? "shield-check" : "wifi")} ${testMode ? "وضع الاختبار مفعّل" : "الإرسال الحقيقي مفعّل"}</span>`,
      `<span class="chip ${mobileTone}">${A.icon("phone")} الهاتف: ${mobileText}</span>`,
      `<span class="chip ${cloud.ready ? "" : "is-danger"}">${A.icon("dashboard")} الحاسوب: ${cloud.ready ? (cloud.configured ? "مهيأ" : "محاكاة") : "غير مهيأ"}</span>`,
      `<span class="chip ${waTone}">${A.icon("whatsapp")} واتساب: ${waText}</span>`,
    ];
    els.statusChips.innerHTML = chips.join("");
    els.totalChip.innerHTML = `<strong>${A.formatInt(state.counts.total || 0)}</strong>&nbsp;تلميذ نشط`;
    els.testNote.classList.toggle("hidden", !testMode);
    els.scopeAllCount.textContent = A.formatInt(state.counts.total || 0);
  }

  function renderChannels() {
    const cloudMode = whatsappCloud();
    if (els.whatsappSubtitle) {
      els.whatsappSubtitle.textContent = cloudMode
        ? "إرسال تلقائي عبر Meta WhatsApp Cloud API"
        : "فتح محادثة جاهزة لكل ولي";
    }
    if (els.whatsappDesc) {
      els.whatsappDesc.textContent = cloudMode
        ? "يرسل النظام رسائل واتساب تلقائيًا بعد موافقتك عبر Meta Cloud API باستخدام قالب معتمد ومتغير واحد. اضبط Phone Number ID والرمز والقالب من إعدادات الرسائل."
        : "لا يُرسل النظام رسائل واتساب تلقائيًا في النمط اليدوي. يتطلب تثبيت تطبيق واتساب لسطح المكتب، ثم تفتح محادثة كل ولي بنص الرسالة وتراجعها وتضغط «إرسال» داخل واتساب.";
    }
    if (els.whatsappDesktopExtra) {
      els.whatsappDesktopExtra.classList.toggle("hidden", cloudMode);
    }
    let firstReady = "";
    els.channelCards.forEach((card) => {
      const id = card.dataset.channel;
      const info = channelInfo(id);
      const radio = card.querySelector('input[name="msg-channel"]');
      const badge = card.querySelector(`[data-channel-state="${id}"]`);
      const ready = Boolean(info.ready);
      const configured = Boolean(info.configured);
      card.classList.toggle("is-disabled", !ready);
      radio.disabled = !ready;
      if (badge) {
        if (id === "whatsapp" && cloudMode) {
          if (!ready) {
            badge.className = "channel-state chip is-danger";
            badge.textContent = "غير مهيأة";
          } else if (configured) {
            badge.className = "channel-state chip is-accent";
            badge.textContent = state.channels.test_mode ? "Meta Cloud — محاكاة" : "جاهزة للإرسال التلقائي";
          } else {
            badge.className = "channel-state chip is-warning";
            badge.textContent = "محاكاة فقط";
          }
        } else {
          badge.className = `channel-state chip ${ready ? (configured ? "is-accent" : "is-warning") : "is-danger"}`;
          badge.textContent = ready
            ? configured
              ? "جاهزة للإرسال"
              : "محاكاة فقط"
            : "غير مهيأة";
        }
      }
      if (ready && !firstReady) firstReady = id;
    });
    if (!channelInfo(state.channel).ready) {
      state.channel = firstReady || "mobile";
    }
    const selected = els.channelRadios.find((radio) => radio.value === state.channel);
    if (selected && !selected.disabled) selected.checked = true;
    syncChannelCards();
    syncChannelMessage();
    renderBodyCount();
  }

  function syncChannelCards() {
    els.channelCards.forEach((card) => {
      card.classList.toggle("is-active", card.dataset.channel === state.channel);
    });
  }

  function syncChannelMessage() {
    const info = channelInfo(state.channel);
    const cloudMode = whatsappCloud();
    const warnings = [];
    if (!info.ready) {
      warnings.push(
        state.channel === "whatsapp"
          ? cloudMode
            ? "إعدادات Meta WhatsApp Cloud API غير مكتملة. أكمل Phone Number ID ورمز الوصول والقالب من إعدادات الرسائل، أو فعّل وضع الاختبار."
            : "واتساب معطّل من إعدادات الرسائل. فعّله لتتمكن من فتح المحادثات."
          : "هذه القناة غير مهيأة. أضف رابط البوابة المناسب من إعدادات الرسائل، أو استخدم وضع الاختبار."
      );
    }
    if (state.channel !== "whatsapp" && state.channels.test_mode && !info.configured) {
      warnings.push("وضع الاختبار مفعّل: سيتم تسجيل الإرسال كمحاكاة ناجحة فقط، دون اتصال بأي بوابة.");
    }
    if (state.channel === "mobile" && !state.channels.test_mode) {
      const mobile = gatewayMobile();
      if (!state.gateway) {
        warnings.push("جارٍ التحقق من اتصال الهاتف… انتظر لحظة قبل الإرسال.");
      } else if (!mobile || !mobile.can_send) {
        warnings.push(
          `${(mobile && (mobile.hint || mobile.detail)) || "الهاتف غير متصل."} استخدم زر «اقتران الهاتف برمز» أو أعد الفحص بعد تشغيل تطبيق البوابة.`
        );
      }
    }
    if (state.channel === "whatsapp" && info.ready && cloudMode) {
      if (state.channels.test_mode) {
        warnings.push("وضع الاختبار مفعّل: ستُحاكى رسائل واتساب دون إرسال حقيقي عبر Meta.");
      } else {
        warnings.push("سيُرسل النظام رسائل واتساب تلقائيًا عبر Meta Cloud API بعد الموافقة. القالب المعتمد وحدود 1024 حرفًا مطبقة.");
      }
    }
    if (state.channel === "whatsapp" && info.ready && !cloudMode) {
      warnings.push("واتساب يدوي: لن يرسل النظام أي رسالة. ستفتح محادثة جاهزة لكل ولي وتؤكد الإرسال بنفسك.");
      if (state.gateway && state.gateway.whatsapp_desktop && state.gateway.whatsapp_desktop.installed === false) {
        warnings.push("تطبيق واتساب لسطح المكتب غير مثبّت على هذا الحاسوب. ثبّته من الرابط أسفل البطاقة قبل فتح المحادثات.");
      }
    }
    els.channelWarning.classList.toggle("hidden", warnings.length === 0);
    els.channelWarningText.textContent = warnings.join(" ");
    const label = state.channel === "whatsapp"
      ? cloudMode
        ? "إرسال واتساب تلقائيًا"
        : "فتح محادثات واتساب"
      : state.channel === "cloud"
        ? "إرسال SMS عبر الحاسوب"
        : "إرسال SMS عبر الهاتف";
    els.send.querySelector("span").textContent = label;
  }

  /* ---------------------------------------------------------------------- */
  /* Phone gateway status                                                    */
  /* ---------------------------------------------------------------------- */

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

  function renderGateway() {
    const gateway = state.gateway;
    if (!gateway) {
      els.gatewayDot.className = "gateway-dot is-pending";
      els.gatewayTitle.textContent = "جارٍ فحص اتصال الهاتف…";
      els.gatewayDetail.textContent = "يجب تثبيت تطبيق بوابة الرسائل على هاتف أندرويد وتشغيله على نفس شبكة Wi-Fi قبل إرسال SMS حقيقي.";
      els.gatewayHint.classList.add("hidden");
      els.gatewayUrl.classList.add("hidden");
      els.gatewayVersion.classList.add("hidden");
      els.gatewayTestChip.classList.add("hidden");
      return;
    }
    const mobile = gateway.mobile || {};
    const app = gateway.gateway_app || {};
    let tone = "is-offline";
    if (mobile.status === "connected") tone = "is-online";
    else if (mobile.status === "outdated" || mobile.status === "auth_required") tone = "is-warning";
    else if (!mobile.configured && gateway.test_mode) tone = "is-pending";
    els.gatewayDot.className = `gateway-dot ${tone}`;
    els.gatewayTitle.textContent = GATEWAY_TITLES[mobile.status] || "حالة بوابة الهاتف";
    let detail = mobile.detail || "";
    if (gateway.test_mode && mobile.status !== "connected") {
      detail += " وضع الاختبار مفعّل، لذا يمكنك تجربة الإرسال دون اتصال؛ عطّله لإرسال SMS حقيقي.";
    }
    els.gatewayDetail.textContent = detail.trim();
    els.gatewayHint.textContent = mobile.hint || "";
    els.gatewayHint.classList.toggle("hidden", !mobile.hint);
    els.gatewayUrl.textContent = mobile.url || "";
    els.gatewayUrl.classList.toggle("hidden", !mobile.url);
    const version = app.version || "";
    if (mobile.version && version && mobile.version !== version) {
      els.gatewayVersion.textContent = `إصدار الهاتف: ${mobile.version} · المطلوب: ${version}`;
    } else {
      els.gatewayVersion.textContent = `تطبيق الهاتف: ${version || "—"}`;
    }
    els.gatewayVersion.classList.remove("hidden");
    els.gatewayTestChip.classList.toggle("hidden", !gateway.test_mode);
    if (app.download_url) {
      els.gatewayDownload.href = app.download_url;
      const label = els.gatewayDownload.querySelector("span");
      if (label) label.textContent = `تحميل تطبيق الهاتف ${version}`;
    }
    els.gatewayStrip.title = gateway.checked_at ? `آخر فحص: ${gateway.checked_at}` : "";
  }

  function renderChannelLiveStates() {
    const mobileInfo = channelInfo("mobile");
    const mobile = gatewayMobile();
    const testMode = Boolean(state.channels.test_mode);
    const mobileBadge = document.querySelector('[data-channel-state="mobile"]');
    if (mobileBadge) {
      let text = "غير مهيأة";
      let tone = "is-danger";
      if (!mobileInfo.configured && testMode) {
        text = "محاكاة فقط";
        tone = "is-warning";
      } else if (!state.gateway) {
        text = testMode ? "محاكاة فقط" : "جارٍ فحص الاتصال…";
        tone = testMode ? "is-warning" : "";
      } else if (mobile.status === "connected") {
        text = testMode ? "الهاتف متصل — محاكاة" : "الهاتف متصل — جاهز";
        tone = "is-accent";
      } else if (mobile.status === "outdated") {
        text = "متصل — حدّث تطبيق الهاتف";
        tone = "is-warning";
      } else if (mobile.status === "auth_required") {
        text = "متصل — يلزم الاقتران برمز";
        tone = "is-warning";
      } else if (!mobileInfo.configured) {
        text = testMode ? "محاكاة فقط" : "غير مهيأة";
        tone = testMode ? "is-warning" : "is-danger";
      } else {
        text = testMode ? "الهاتف غير متصل — محاكاة" : "الهاتف غير متصل";
        tone = testMode ? "is-warning" : "is-danger";
      }
      mobileBadge.className = `channel-state chip ${tone}`.trim();
      mobileBadge.textContent = text;
    }
    const waInfo = channelInfo("whatsapp");
    const waBadge = document.querySelector('[data-channel-state="whatsapp"]');
    const desktop = state.gateway && state.gateway.whatsapp_desktop;
    const cloudMode = whatsappCloud();
    if (waBadge) {
      let text = waInfo.ready ? "جاهزة للإرسال" : "غير مهيأة";
      let tone = waInfo.ready ? "is-accent" : "is-danger";
      if (cloudMode) {
        if (!waInfo.ready) {
          text = "غير مهيأة";
          tone = "is-danger";
        } else if (waInfo.configured) {
          text = state.channels.test_mode ? "Meta Cloud — محاكاة" : "Meta Cloud جاهز";
          tone = "is-accent";
        } else {
          text = "محاكاة فقط";
          tone = "is-warning";
        }
      } else if (waInfo.ready && desktop) {
        if (desktop.installed === false) {
          text = "ثبّت واتساب لسطح المكتب";
          tone = "is-warning";
        } else if (desktop.installed === true) {
          text = "واتساب سطح المكتب جاهز";
          tone = "is-accent";
        }
      }
      waBadge.className = `channel-state chip ${tone}`.trim();
      waBadge.textContent = text;
    }
    if (els.whatsappDesktopChip) {
      let text = "حالة واتساب لسطح المكتب: جارٍ الفحص…";
      let tone = "";
      if (desktop) {
        if (desktop.installed === true) {
          text = "واتساب لسطح المكتب مثبّت";
          tone = "is-accent";
        } else if (desktop.installed === false) {
          text = "واتساب لسطح المكتب غير مثبّت";
          tone = "is-danger";
        } else {
          text = "حالة واتساب لسطح المكتب غير معروفة";
          tone = "is-warning";
        }
      }
      els.whatsappDesktopChip.className = `chip ${tone}`.trim();
      els.whatsappDesktopChip.textContent = text;
    }
  }

  async function loadGateway(force = false) {
    if (state.gatewayLoading) return;
    state.gatewayLoading = true;
    if (force) A.setBusy(els.gatewayCheck, true);
    try {
      const data = await A.api(`/api/messages/gateway${force ? "?force=1" : ""}`);
      state.gateway = data.gateway;
      renderGateway();
      renderStatus();
      renderChannelLiveStates();
      syncChannelMessage();
      updateSendState();
    } catch (error) {
      if (!state.gateway) {
        els.gatewayDot.className = "gateway-dot is-offline";
        els.gatewayTitle.textContent = "تعذر فحص اتصال الهاتف";
        els.gatewayDetail.textContent = error.message || "أعد المحاولة أو تحقق من إعدادات الرسائل.";
      }
      if (force) A.toast(error.message || "تعذر فحص اتصال الهاتف.", "error");
    } finally {
      state.gatewayLoading = false;
      if (force) A.setBusy(els.gatewayCheck, false);
    }
  }

  /* ---------------------------------------------------------------------- */
  /* Pairing                                                                 */
  /* ---------------------------------------------------------------------- */

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

  function openPairing(mode) {
    const scanMode = mode === "scan";
    const mobile = gatewayMobile() || {};
    const currentUrl = scanMode ? "" : mobile.url || "";
    const modal = A.openModal({
      title: scanMode ? "إضافة هاتف للمسح اللاسلكي" : "اقتران الهاتف برمز 4 أرقام",
      subtitle: scanMode
        ? "أدخل رابط الهاتف ورمز الاقتران ليشارك في المسح مع بقية الهواتف."
        : "بدل نسخ مفتاح API الطويل، أدخل الرمز الظاهر في تطبيق البوابة.",
      icon: "shield-check",
      body: `
        <div class="info-banner is-success">
          ${A.icon("shield-check")}
          <div><strong>كيف يعمل الاقتران؟</strong> افتح تطبيق بوابة SMS على هاتف أندرويد، واضغط بطاقة «رمز الاقتران» لعرض رمز من 4 أرقام. أدخله هنا ليحفظ الحاسوب المفتاح الطويل تلقائيًا.</div>
        </div>
        ${
          scanMode
            ? `<div class="field" style="margin-top:12px">
          <label for="pair-name">اسم الهاتف (اختياري)</label>
          <input class="input" id="pair-name" type="text" maxlength="40" placeholder="مثال: بوابة المدخل">
          <span class="field-hint">يظهر هذا الاسم في قائمة هواتف المسح لتسهيل معرفة كل مدخل.</span>
        </div>`
            : ""
        }
        <div class="field" style="margin-top:${scanMode ? "0" : "12px"}">
          <label for="pair-url">رابط بوابة الهاتف</label>
          <input class="input mono" id="pair-url" type="text" inputmode="url" dir="ltr" maxlength="500" placeholder="192.168.1.20:8080" value="${safe(currentUrl)}">
          <span class="field-hint">العنوان الظاهر في تطبيق الهاتف. يمكنك تركه كما هو إن كان الهاتف على نفس الشبكة.</span>
        </div>
        <div class="field">
          <label for="pair-pin">رمز الاقتران (4 أرقام)</label>
          <input class="input mono pin-input" id="pair-pin" type="text" inputmode="numeric" dir="ltr" maxlength="4" autocomplete="one-time-code" placeholder="0000">
          <span class="field-hint">الرمز يتغير عند تجديده من الهاتف. بعد نجاح الاقتران لا تحتاج لإدخاله مجددًا.</span>
        </div>
        <div class="info-banner is-warning hidden" id="pair-error">
          ${A.icon("warning")}
          <div id="pair-error-text"></div>
        </div>`,
      footer: `<button class="btn btn-secondary" type="button" data-modal-close>إلغاء</button>
               <button class="btn btn-primary" type="button" id="pair-submit">${A.icon("shield-check")} ${scanMode ? "إضافة الهاتف" : "اقتران الهاتف"}</button>`,
    });
    const body = modal.element;
    const pinInput = body.querySelector("#pair-pin");
    const urlInput = body.querySelector("#pair-url");
    const nameInput = body.querySelector("#pair-name");
    const errorBox = body.querySelector("#pair-error");
    const errorText = body.querySelector("#pair-error-text");
    const submit = body.querySelector("#pair-submit");
    pinInput.focus();

    async function submitPairing() {
      const pin = normalizePin(pinInput.value);
      const url = urlInput.value.trim();
      if (pin.length !== 4) {
        errorText.textContent = "أدخل رمز الاقتران المكوّن من 4 أرقام.";
        errorBox.classList.remove("hidden");
        pinInput.focus();
        return;
      }
      A.setBusy(submit, true);
      errorBox.classList.add("hidden");
      try {
        const data = await A.api(
          scanMode ? "/api/scanner/phones/pair" : "/api/messages/gateway/pair",
          {
            method: "POST",
            body: scanMode ? { pin, url, name: (nameInput.value || "").trim() } : { pin, url },
          }
        );
        if (scanMode) {
          A.closeModal();
          await loadScanPhones();
          A.toast(data.message || "أُضيف هاتف المسح بنجاح.", "success");
          return;
        }
        state.gateway = data.gateway;
        A.closeModal();
        renderGateway();
        renderStatus();
        renderChannelLiveStates();
        syncChannelMessage();
        updateSendState();
        A.toast(data.message || "تم اقتران الهاتف بنجاح وحفظ المفتاح تلقائيًا.", "success");
        loadStudents({ silent: true });
      } catch (error) {
        errorText.textContent = error.message || "تعذر إكمال الاقتران. تحقق من الرمز والرابط ثم أعد المحاولة.";
        errorBox.classList.remove("hidden");
      } finally {
        A.setBusy(submit, false);
      }
    }

    submit.addEventListener("click", submitPairing);
    pinInput.addEventListener("input", () => {
      pinInput.value = normalizePin(pinInput.value);
    });
    pinInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        submitPairing();
      }
    });
  }

  /* Wireless scanning phones                                                */

  const SCAN_PHONE_STATES = {
    ok: { dot: "is-online", label: "جاهز للمسح" },
    resynced: { dot: "is-online", label: "جاهز للمسح" },
    offline: { dot: "is-offline", label: "غير متصل" },
    unauthorized: { dot: "is-warning", label: "مفتاح غير صحيح" },
    outdated: { dot: "is-warning", label: "يتطلب تحديث التطبيق" },
    busy: { dot: "is-pending", label: "جارٍ الفحص" },
    error: { dot: "is-warning", label: "خطأ في الاستجابة" },
  };

  function scanPhoneName(phone, index) {
    if (phone.name) return phone.name;
    if (phone.primary) return "البوابة الأساسية (بوابة الرسائل)";
    return `هاتف المسح رقم ${A.formatInt(index)}`;
  }

  function scanPhoneRow(phone, label, primary) {
    const info = SCAN_PHONE_STATES[phone.state] || SCAN_PHONE_STATES.error;
    const version = phone.version ? `<span class="mono" dir="ltr">v${safe(phone.version)}</span>` : "";
    const action = primary
      ? `<span class="chip">أساسي</span>`
      : `<button class="icon-btn" type="button" data-remove-scan-phone="${safe(phone.id)}" title="إزالة الهاتف" aria-label="إزالة الهاتف">
           <svg class="ico"><use href="#i-trash"></use></svg>
         </button>`;
    return `
      <div class="scan-phone-row">
        <span class="gateway-dot ${info.dot}" aria-hidden="true"></span>
        <div class="scan-phone-copy">
          <strong dir="auto">${safe(label)}</strong>
          <span class="mono" dir="ltr">${safe(phone.url)}</span>
        </div>
        <div class="scan-phone-status">
          <span>${info.label}</span>
          ${version}
        </div>
        ${action}
      </div>`;
  }

  function renderScanPhones() {
    if (!els.scanPhonesList) return;
    const rows = [];
    const phones = state.scanPhones || [];
    if (state.scanPhonesPrimary) {
      rows.push(scanPhoneRow(state.scanPhonesPrimary, "البوابة الأساسية (بوابة الرسائل)", true));
    }
    phones.forEach((phone, index) => {
      rows.push(scanPhoneRow(phone, scanPhoneName(phone, index + 1), false));
    });
    els.scanPhonesList.innerHTML = rows.join("");
    const total = (state.scanPhonesPrimary ? 1 : 0) + phones.length;
    if (!total) {
      els.scanPhonesNote.textContent = "لا توجد هواتف مسح بعد. أضف هاتفًا أو اضبط بوابة الرسائل أولًا.";
    } else if (!phones.length) {
      els.scanPhonesNote.textContent = "الهاتف الأساسي يشارك في المسح. أضف هواتف أخرى لمداخل إضافية.";
    } else {
      els.scanPhonesNote.textContent = `هواتف المسح: ${A.formatInt(total)}.`;
    }
  }

  async function loadScanPhones(force) {
    if (!els.scanPhonesList || state.scanPhonesLoading) return;
    state.scanPhonesLoading = true;
    if (force) A.setBusy(els.scanPhonesRefresh, true);
    if (!state.scanPhonesPrimary && !state.scanPhones.length) {
      els.scanPhonesNote.textContent = "جارٍ فحص هواتف المسح…";
    }
    try {
      const data = await A.api("/api/scanner/phones");
      state.scanPhones = data.phones || [];
      state.scanPhonesPrimary = data.primary || null;
      renderScanPhones();
    } catch (error) {
      els.scanPhonesNote.textContent = error.message || "تعذر فحص هواتف المسح.";
    } finally {
      state.scanPhonesLoading = false;
      if (force) A.setBusy(els.scanPhonesRefresh, false);
    }
  }

  /* ---------------------------------------------------------------------- */
  /* Recipient scope                                                         */
  /* ---------------------------------------------------------------------- */

  function setScope(scope) {
    if (scope === "class" && !state.classGroup) {
      A.toast("لا توجد أقسام متاحة. أضف تلاميذ بأقسام أولًا.", "warning");
      return;
    }
    state.scope = scope;
    renderScope();
    renderRecipientSummary();
    renderSelected();
    schedulePreview();
  }

  function renderScope() {
    els.scopeButtons.forEach((button) => {
      const active = button.dataset.scope === state.scope;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    });
    els.classPanel.hidden = state.scope !== "class";
    els.selectedPanel.hidden = state.scope !== "selected";
    els.scopeSelectedCount.textContent = A.formatInt(state.selected.size);
  }

  function renderRecipientSummary() {
    const list = recipients();
    const ready = list.filter((student) => eligible(student, state.channel));
    const classCount = new Set(list.map((student) => student.class_group)).size;
    els.summaryTotal.textContent = A.formatInt(list.length);
    els.summaryEligible.textContent = A.formatInt(ready.length);
    els.summaryEligibleLabel.textContent = state.channel === "whatsapp" ? "لديهم واتساب" : "سيصلهم SMS";
    els.summarySkipped.textContent = A.formatInt(list.length - ready.length);
    els.summaryClasses.textContent = A.formatInt(classCount);
    const skipped = list.length - ready.length;
    els.skippedNote.classList.toggle("hidden", skipped === 0);
    if (skipped > 0) {
      els.skippedText.textContent = state.channel === "whatsapp"
        ? `سيُستبعد من المحادثات تلاميذ بلا رقم واتساب صالح (العدد: ${A.formatInt(skipped)}).`
        : `سيُستبعد من الإرسال تلاميذ بلا رقم هاتف صالح (العدد: ${A.formatInt(skipped)}). راجع أرقام الأولياء من صفحة التلاميذ.`;
    }
    els.scopeAllCount.textContent = A.formatInt(state.counts.total || 0);
    els.scopeSelectedCount.textContent = A.formatInt(state.selected.size);
    updateApprovalText();
    updateSendState();
  }

  function renderSelected() {
    const selectedStudents = state.students.filter((student) => state.selected.has(student.student_nin));
    els.selectedSummary.textContent = selectedStudents.length
      ? `تم تحديد ${A.formatInt(selectedStudents.length)}`
      : "لم يتم اختيار أحد";
    if (!selectedStudents.length) {
      els.selectedChips.innerHTML = '<span class="muted tiny">افتح قائمة الاختيار لتحديد تلاميذ بعينهم.</span>';
      return;
    }
    const shown = selectedStudents.slice(0, 18);
    const extra = selectedStudents.length - shown.length;
    els.selectedChips.innerHTML = shown
      .map(
        (student) => `<span class="selected-chip">${safe(student.full_name)}<button type="button" data-remove-selected="${safe(student.student_nin)}" aria-label="إزالة ${safe(student.full_name)}">${A.icon("close")}</button></span>`
      )
      .join("") + (extra > 0 ? `<span class="selected-chip is-more">+${A.formatInt(extra)} آخرون</span>` : "");
  }

  /* ---------------------------------------------------------------------- */
  /* Student picker                                                          */
  /* ---------------------------------------------------------------------- */

  function openPicker() {
    if (!state.students.length) {
      A.toast("لا يوجد تلاميذ نشطون. أضف التلاميذ أولًا.", "warning");
      return;
    }
    const draft = new Set(state.selected);
    const classes = uniqueClasses(state.students);
    let query = "";
    let classFilter = "";
    const modal = A.openModal({
      title: "اختيار التلاميذ",
      subtitle: "ابحث وحدد من سيستلم الرسالة. يمكنك اختيار أكثر من تلميذ.",
      size: "xl",
      icon: "users",
      body: `
        <div class="picker-toolbar">
          <div class="search-box">
            ${A.icon("search")}
            <input class="input" type="search" id="picker-search" placeholder="ابحث بالاسم أو رقم التعريف أو الهاتف…" autocomplete="off">
          </div>
          <select class="select" id="picker-class">
            <option value="">كل الأقسام</option>
            ${classes.map((item) => `<option value="${safe(item.value)}">${safe(item.label)} (${A.formatInt(item.count)})</option>`).join("")}
          </select>
          <button class="btn btn-secondary btn-sm" type="button" id="picker-select-all">تحديد كل النتائج</button>
          <button class="btn btn-ghost btn-sm" type="button" id="picker-clear-all">مسح الكل</button>
        </div>
        <div class="picker-summary" id="picker-summary"></div>
        <div class="picker-list" id="picker-list"></div>`,
      footer: `<button class="btn btn-secondary" type="button" data-modal-close>إلغاء</button>
               <button class="btn btn-primary" type="button" id="picker-apply">${A.icon("check")} تأكيد الاختيار</button>`,
    });

    const body = modal.element;
    const search = body.querySelector("#picker-search");
    const classSelect = body.querySelector("#picker-class");
    const list = body.querySelector("#picker-list");
    const summary = body.querySelector("#picker-summary");
    const apply = body.querySelector("#picker-apply");

    function filtered() {
      const term = query.trim().toLowerCase();
      return state.students.filter((student) => {
        if (classFilter === "__none__" && student.class_group_raw) return false;
        if (classFilter && classFilter !== "__none__" && student.class_group_raw !== classFilter) return false;
        if (!term) return true;
        return `${student.full_name} ${student.student_nin} ${student.parent_phone} ${student.class_group}`
          .toLowerCase()
          .includes(term);
      });
    }

    function renderList() {
      const items = filtered();
      const visible = items.slice(0, 400);
      summary.innerHTML = `<strong>${A.formatInt(items.length)}</strong> نتيجة · <strong>${A.formatInt(draft.size)}</strong> محدد`;
      apply.innerHTML = `${A.icon("check")} تأكيد الاختيار (${A.formatInt(draft.size)})`;
      apply.disabled = draft.size === 0;
      if (!items.length) {
        list.innerHTML = A.emptyState("search", "لا توجد نتائج", "جرّب اسمًا آخر أو غيّر القسم.");
        return;
      }
      list.innerHTML = visible
        .map((student) => {
          const checked = draft.has(student.student_nin);
          const phone = student.parent_phone
            ? `<span class="mono tiny" dir="ltr">${safe(student.parent_phone)}</span>`
            : '<span class="chip is-danger">بدون هاتف</span>';
          return `<label class="picker-row ${checked ? "is-checked" : ""}">
            <input type="checkbox" data-picker-nin="${safe(student.student_nin)}" ${checked ? "checked" : ""}>
            ${A.avatar(student, "avatar-sm")}
            <span class="picker-identity">
              <strong>${safe(student.full_name)}</strong>
              <small>${safe(student.student_nin)} · ${safe(student.class_group)}</small>
            </span>
            ${phone}
          </label>`;
        })
        .join("") +
        (items.length > visible.length
          ? `<p class="hint-inline" style="padding:8px">تظهر أول ${A.formatInt(visible.length)} نتيجة. استخدم البحث لتضييق القائمة.</p>`
          : "");
    }

    search.addEventListener("input", A.debounce(() => {
      query = search.value;
      renderList();
    }, 160));
    classSelect.addEventListener("change", () => {
      classFilter = classSelect.value;
      renderList();
    });
    body.querySelector("#picker-select-all").addEventListener("click", () => {
      filtered().forEach((student) => draft.add(student.student_nin));
      renderList();
    });
    body.querySelector("#picker-clear-all").addEventListener("click", () => {
      draft.clear();
      renderList();
    });
    list.addEventListener("change", (event) => {
      const input = event.target.closest("[data-picker-nin]");
      if (!input) return;
      const nin = input.dataset.pickerNin;
      if (input.checked) draft.add(nin);
      else draft.delete(nin);
      input.closest(".picker-row").classList.toggle("is-checked", input.checked);
      summary.innerHTML = `<strong>${A.formatInt(filtered().length)}</strong> نتيجة · <strong>${A.formatInt(draft.size)}</strong> محدد`;
      apply.innerHTML = `${A.icon("check")} تأكيد الاختيار (${A.formatInt(draft.size)})`;
      apply.disabled = draft.size === 0;
    });
    apply.addEventListener("click", () => {
      state.selected = new Set(draft);
      A.closeModal();
      renderRecipientSummary();
      renderSelected();
      renderScope();
      schedulePreview();
    });
    renderList();
  }

  /* ---------------------------------------------------------------------- */
  /* Approval + send state                                                   */
  /* ---------------------------------------------------------------------- */

  function updateApprovalText() {
    const count = eligibleRecipients().length;
    if (state.channel === "whatsapp" && whatsappCloud()) {
      els.approvalText.textContent = state.channels.test_mode
        ? `أوافق على محاكاة إرسال رسائل واتساب عبر Meta Cloud API إلى المستلمين في النطاق المحدد (العدد: ${A.formatInt(count)}) في وضع الاختبار، دون استخدام الشبكة.`
        : `أوافق صراحةً على إرسال رسائل واتساب تلقائيًا عبر Meta Cloud API إلى المستلمين في النطاق المحدد (العدد: ${A.formatInt(count)}).`;
    } else if (state.channel === "whatsapp") {
      els.approvalText.textContent = `أوافق على تجهيز محادثة واتساب لكل مستلم في النطاق المحدد (العدد: ${A.formatInt(count)})، وأتحمل مسؤولية مراجعة كل رسالة والضغط على «إرسال» داخل واتساب.`;
    } else if (state.channels.test_mode) {
      els.approvalText.textContent = `أوافق على محاكاة إرسال SMS إلى المستلمين في النطاق المحدد (العدد: ${A.formatInt(count)}) في وضع الاختبار، دون استخدام الشبكة أو استهلاك رصيد.`;
    } else {
      els.approvalText.textContent = `أوافق صراحةً على إرسال SMS إلى المستلمين في النطاق المحدد (العدد: ${A.formatInt(count)}) عبر ${state.channel === "mobile" ? "تطبيق الهاتف" : "واجهة الحاسوب/السحابة"}.`;
    }
  }

  function updateSendState() {
    const approved = Boolean(els.approve.checked);
    const count = eligibleRecipients().length;
    const info = channelInfo(state.channel);
    const text = els.body.value.trim();
    let hint = "";
    let can = true;
    if (state.loading) {
      hint = "جارٍ تحميل البيانات…";
      can = false;
    } else if (!state.students.length) {
      hint = "لا يوجد تلاميذ نشطون. أضف التلاميذ من صفحة التلاميذ أولًا.";
      can = false;
    } else if (!recipients().length) {
      hint = state.scope === "selected" ? "حدّد تلميذًا واحدًا على الأقل." : "لا يوجد مستلمون في النطاق المحدد.";
      can = false;
    } else if (!count) {
      hint = state.channel === "whatsapp"
        ? "لا يوجد تلميذ برقم هاتف صالح لواتساب في النطاق المحدد."
        : "لا يوجد تلميذ برقم هاتف صالح في النطاق المحدد.";
      can = false;
    } else if (!info.ready) {
      hint = "القناة غير مهيأة. راجع إعدادات الرسائل.";
      can = false;
    } else if (state.channel === "mobile" && !state.channels.test_mode && !state.gateway) {
      hint = "جارٍ التحقق من اتصال الهاتف…";
      can = false;
    } else if (state.channel === "mobile" && !state.channels.test_mode && !gatewayCanSend()) {
      const mobile = gatewayMobile();
      hint = (mobile && (mobile.hint || mobile.detail)) || "الهاتف غير متصل. شغّل تطبيق البوابة على الهاتف ثم أعد الفحص.";
      can = false;
    } else if (
      state.channel === "whatsapp" &&
      !whatsappCloud() &&
      state.gateway &&
      state.gateway.whatsapp_desktop &&
      state.gateway.whatsapp_desktop.installed === false
    ) {
      hint = "ثبّت تطبيق واتساب لسطح المكتب على هذا الحاسوب قبل فتح المحادثات.";
      can = false;
    } else if (!text) {
      hint = "اكتب نص الرسالة قبل المتابعة.";
      can = false;
    } else if (state.channel === "whatsapp" && whatsappCloud() && text.length > 1024) {
      hint = `نص رسالة واتساب التلقائية يتجاوز 1024 حرفًا (${A.formatInt(text.length)}). اختصر النص ثم أعد المحاولة.`;
      can = false;
    } else if (!approved) {
      hint = "فعّل مربع الموافقة لتأكيد الإرسال.";
      can = false;
    } else if (state.channel === "whatsapp" && whatsappCloud()) {
      hint = state.channels.test_mode
        ? `محاكاة إرسال واتساب عبر Meta Cloud API (العدد: ${A.formatInt(count)}).`
        : `سيبدأ إرسال واتساب تلقائيًا عبر Meta Cloud API (العدد: ${A.formatInt(count)}).`;
    } else {
      hint = state.channel === "whatsapp"
        ? `ستُفتح روابط واتساب للمراجعة اليدوية (العدد: ${A.formatInt(count)}).`
        : `عند الضغط سيبدأ الإرسال إلى المستلمين المؤهلين (العدد: ${A.formatInt(count)}).`;
    }
    els.submitHint.textContent = hint;
    els.send.disabled = !can || state.sending;
    els.send.classList.toggle("is-loading", state.sending);
  }

  /* ---------------------------------------------------------------------- */
  /* Preview                                                                 */
  /* ---------------------------------------------------------------------- */

  function schedulePreview() {
    window.clearTimeout(state.previewTimer);
    state.previewTimer = window.setTimeout(loadPreview, 380);
  }

  async function loadPreview() {
    const message = els.body.value.trim();
    if (!message || !recipients().length) {
      state.preview = null;
      els.previewBody.textContent = message
        ? "لا يوجد مستلمون في النطاق المحدد."
        : "اكتب الرسالة لعرض معاينة حقيقية من بيانات أول مستلم.";
      els.segmentChip.classList.add("hidden");
      return;
    }
    if (state.scope === "selected" && !state.selected.size) {
      els.previewBody.textContent = "حدّد تلاميذ أولًا لعرض المعاينة.";
      els.segmentChip.classList.add("hidden");
      return;
    }
    const requestId = ++state.previewRequest;
    try {
      const data = await A.api("/api/messages/preview", {
        method: "POST",
        body: {
          scope: state.scope,
          class_group: state.scope === "class" ? state.classGroup : "",
          student_nins: state.scope === "selected" ? Array.from(state.selected) : [],
          channel: state.channel,
          message,
        },
      });
      if (requestId !== state.previewRequest) return;
      state.preview = data.preview;
      const sample = (data.preview.samples || [])[0];
      if (!sample) {
        els.previewBody.textContent = "لا يوجد مستلم مؤهل لعرض معاينة.";
        els.segmentChip.classList.add("hidden");
        return;
      }
      els.previewBody.innerHTML =
        `<div class="preview-recipient"><strong>${safe(sample.full_name)}</strong>` +
        `<span>${safe(sample.class_group)} · <span class="mono" dir="ltr">${safe(sample.parent_phone)}</span></span></div>` +
        `<p class="preview-message">${safe(sample.message_body)}</p>`;
      if (state.channel === "whatsapp" && !whatsappCloud()) {
        els.segmentChip.classList.add("hidden");
      } else if (state.channel === "whatsapp") {
        els.segmentChip.classList.remove("hidden");
        els.segmentChip.textContent = `${A.formatInt((sample.message_body || "").length)} / 1024 حرفًا`;
      } else {
        els.segmentChip.classList.remove("hidden");
        els.segmentChip.textContent = `${A.formatInt(data.preview.segments || 1)} مقطع تقريبًا`;
      }
    } catch (error) {
      if (requestId !== state.previewRequest) return;
      els.previewBody.textContent = error.message || "تعذرت معاينة الرسالة.";
      els.segmentChip.classList.add("hidden");
    }
  }

  /* ---------------------------------------------------------------------- */
  /* Send                                                                    */
  /* ---------------------------------------------------------------------- */

  function campaignBody() {
    return {
      scope: state.scope,
      class_group: state.scope === "class" ? state.classGroup : "",
      student_nins: state.scope === "selected" ? Array.from(state.selected) : [],
      channel: state.channel,
      message: els.body.value.trim(),
      approved: true,
    };
  }

  async function sendCampaign() {
    if (state.sending || els.send.disabled) return;
    if (!els.approve.checked) {
      A.toast("فعّل مربع الموافقة قبل الإرسال.", "warning");
      return;
    }
    state.sending = true;
    updateSendState();
    A.setBusy(els.send, true);
    try {
      const data = await A.api("/api/messages/send", {
        method: "POST",
        body: campaignBody(),
      });
      els.approve.checked = false;
      if (data.campaign && data.campaign.status === "PREPARED") {
        openWhatsappResult(data);
      } else if (data.job) {
        els.progress.innerHTML = jobMarkup(data.job);
        watchJob(data.job.id);
      }
      A.toast(data.message || "تمت العملية.", data.test_mode ? "info" : "success");
      loadHistory();
    } catch (error) {
      A.toast(error.message || "تعذر بدء الإرسال.", "error");
      loadGateway(true);
    } finally {
      state.sending = false;
      A.setBusy(els.send, false);
      updateSendState();
      updateApprovalText();
    }
  }

  function openWhatsappResult(data) {
    const items = Array.isArray(data.items) ? data.items : [];
    A.openModal({
      title: "محادثات واتساب جاهزة",
      subtitle: `المستلمون المؤهلون: ${A.formatInt(items.length)} · لم يُرسل أي شيء بعد`,
      size: "lg",
      icon: "whatsapp",
      body: `
        <div class="info-banner is-warning">
          ${A.icon("warning")}
          <div><strong>واتساب لا يرسل تلقائيًا.</strong> افتح محادثة كل ولي، راجع النص، ثم اضغط «إرسال» داخل واتساب. لا يغادر جهازك أي شيء قبل ذلك.</div>
        </div>
        <div class="wa-list">
          ${items
            .map(
              (item) => `<div class="wa-row">
                <div class="student-identity">
                  ${A.avatar(item, "avatar-sm")}
                  <div><strong>${safe(item.full_name)}</strong><small>${safe(item.class_group)} · <span class="mono" dir="ltr">${safe(item.parent_phone)}</span></small></div>
                </div>
                <p class="wa-message">${safe(item.message_body)}</p>
                <div class="wa-actions">
                  ${item.whatsapp_url ? `<a class="btn btn-success btn-sm" href="${safe(item.whatsapp_url)}" target="_blank" rel="noopener noreferrer">${A.icon("whatsapp")} فتح المحادثة</a>` : '<span class="chip is-danger">رقم غير صالح</span>'}
                  <button class="icon-btn" type="button" data-copy-message="${safe(item.message_body)}" title="نسخ نص الرسالة" aria-label="نسخ رسالة ${safe(item.full_name)}">${A.icon("file")}</button>
                </div>
              </div>`
            )
            .join("")}
        </div>`,
      footer: `<button class="btn btn-secondary" type="button" data-modal-close>إغلاق</button>`,
    });
  }

  /* ---------------------------------------------------------------------- */
  /* Job progress                                                            */
  /* ---------------------------------------------------------------------- */

  function jobMarkup(job) {
    const total = Number(job.total || 0);
    const processed = Number(job.processed || 0);
    const percent = total ? Math.round((processed / total) * 100) : 0;
    const done = job.status === "done";
    return `<div class="sms-progress ${done ? "" : "is-active"}" style="margin-top:16px">
      ${done ? `<div class="stats-icon" style="display:grid;place-items:center;width:30px;height:30px;border-radius:9px;background:var(--success-soft);color:var(--success)">${A.icon("check")}</div>` : `<span class="spinner"></span>`}
      <div class="sms-progress-text">
        <strong>${done ? "اكتملت معالجة الحملة" : "جارٍ إرسال رسائل الحملة"}</strong>
        <span>${safe(job.message || (job.current_name ? `جارٍ معالجة: ${job.current_name}` : "جارٍ التحضير…"))}</span>
        ${done ? "" : `<div class="sms-progress-bar"><span id="msg-job-bar" style="width:${percent}%"></span></div>`}
      </div>
      <div class="sms-progress-meta">${A.formatInt(job.sent || 0)} ناجحة · ${A.formatInt(job.failed || 0)} فاشلة${job.simulated ? ` · ${A.formatInt(job.simulated)} محاكاة` : ""}${job.error ? ` · ${safe(job.error)}` : ""}</div>
      ${job.first_error ? `<div class="sms-progress-error">${safe(job.first_error)}</div>` : ""}
    </div>`;
  }

  function watchJob(jobId) {
    if (state.jobWatcher) state.jobWatcher.stop();
    let stopped = false;
    let failures = 0;
    const tick = async () => {
      if (stopped) return;
      try {
        const data = await A.api(`/api/messages/jobs/${encodeURIComponent(jobId)}`);
        failures = 0;
        const job = data.job;
        const markup = jobMarkup(job);
        const node = document.createElement("div");
        node.innerHTML = markup;
        const current = els.progress.querySelector(".sms-progress");
        if (current) current.replaceWith(node.firstElementChild);
        else els.progress.insertAdjacentHTML("beforeend", markup);
        if (job.status === "done") {
          A.toast(job.message || "اكتملت الحملة.", job.failed ? "warning" : "success");
          loadHistory();
          loadGateway(true);
          return;
        }
      } catch (error) {
        failures += 1;
        if (failures >= 6) {
          A.toast("تعذر متابعة تقدم الحملة. حدّث الصفحة لعرض النتيجة.", "warning");
          return;
        }
      }
      window.setTimeout(tick, 900);
    };
    state.jobWatcher = { stop() { stopped = true; } };
    tick();
  }

  async function resumeJob() {
    try {
      const data = await A.api("/api/messages/jobs/current");
      if (data.job && data.job.status === "running") {
        els.progress.innerHTML = jobMarkup(data.job);
        watchJob(data.job.id);
      }
    } catch (error) {
      /* the progress panel is optional */
    }
  }

  /* ---------------------------------------------------------------------- */
  /* Campaign history                                                        */
  /* ---------------------------------------------------------------------- */

  function formatDateTime(value) {
    const text = String(value || "");
    if (!/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}/.test(text)) return text || "—";
    return `${A.formatDate(text.slice(0, 10))} · ${text.slice(11, 16)}`;
  }

  function campaignBadge(campaign) {
    if (campaign.status === "RUNNING" || campaign.status === "PENDING") {
      return A.statusBadge("PENDING", "قيد التنفيذ");
    }
    if (campaign.status === "PREPARED") {
      return `<span class="badge badge--new"><i class="badge-dot"></i>جاهزة للإرسال اليدوي</span>`;
    }
    return `<span class="badge badge--present"><i class="badge-dot"></i>مكتملة</span>`;
  }

  function campaignStats(campaign) {
    const counts = campaign.counts || {};
    if (campaign.channel === "whatsapp" && campaign.status === "PREPARED") {
      return `محادثات جاهزة: ${A.formatInt(counts.MANUAL || 0)}`;
    }
    return `${A.formatInt(counts.SENT || 0)} ناجحة · ${A.formatInt(counts.FAILED || 0)} فاشلة${counts.PENDING ? ` · ${A.formatInt(counts.PENDING)} معلقة` : ""}`;
  }

  async function loadHistory() {
    try {
      const data = await A.api(`/api/messages/campaigns?page=1&per_page=8`);
      const items = Array.isArray(data.items) ? data.items : [];
      els.historyLabel.textContent = items.length
        ? `${A.formatInt(data.total)} حملة — الأحدث أولًا`
        : "لم تُنشأ أي حملة بعد.";
      if (!items.length) {
        els.history.innerHTML = A.emptyState(
          "message",
          "لا توجد حملات بعد",
          "أرسل أول رسالة جماعية وسيظهر سجلها هنا مع النتائج.",
        );
        return;
      }
      els.history.innerHTML = items
        .map(
          (campaign) => `<button class="history-item" type="button" data-campaign="${safe(campaign.id)}">
            <span class="history-icon is-${safe(campaign.channel)}">${A.icon(CHANNEL_ICONS[campaign.channel] || "message")}</span>
            <span class="history-main">
              <strong><span>${safe(campaign.scope_label)}</span> · <span>${safe(CHANNEL_SHORT[campaign.channel] || campaign.channel_label)}</span></strong>
              <small>${safe((campaign.message_preview || "").slice(0, 90))}${(campaign.message_preview || "").length > 90 ? "…" : ""}</small>
              <span class="history-meta">${campaignBadge(campaign)} <span>${safe(campaignStats(campaign))}</span></span>
            </span>
            <span class="history-time">${safe(formatDateTime(campaign.created_at))}</span>
          </button>`
        )
        .join("");
    } catch (error) {
      els.history.innerHTML = A.emptyState("warning", "تعذر تحميل سجل الحملات", error.message, '<button class="btn btn-secondary btn-sm" type="button" data-retry-history>إعادة المحاولة</button>');
      els.historyLabel.textContent = "تعذر تحميل سجل الحملات.";
    }
  }

  async function openCampaign(campaignId) {
    let data;
    try {
      data = await A.api(`/api/messages/campaigns/${encodeURIComponent(campaignId)}?page=1&per_page=80`);
    } catch (error) {
      A.toast(error.message || "تعذر تحميل تفاصيل الحملة.", "error");
      return;
    }
    const campaign = data.campaign;
    const counts = campaign.counts || {};
    const items = Array.isArray(data.items) ? data.items : [];
    const isWhatsapp = campaign.channel === "whatsapp";
    const manualWhatsapp = isWhatsapp && campaign.status === "PREPARED";
    const rows = items
      .map((item) => {
        const link = item.whatsapp_url
          ? `<a class="icon-btn is-whatsapp" href="${safe(item.whatsapp_url)}" target="_blank" rel="noopener noreferrer" title="فتح واتساب" aria-label="فتح محادثة ${safe(item.student_name)}">${A.icon("whatsapp")}</a>`
          : "";
        const status = item.delivery_status === "MANUAL"
          ? '<span class="badge badge--new"><i class="badge-dot"></i>جاهزة</span>'
          : A.statusBadge(item.delivery_status, item.status_label);
        return `<tr>
          <td><div class="student-identity">${A.avatar({ full_name: item.student_name, photo_url: "" }, "avatar-sm")}<div><strong>${safe(item.student_name)}</strong><small>${safe(item.student_nin)}</small></div></div></td>
          <td><span class="chip">${safe(item.class_group || "بدون قسم")}</span></td>
          <td><span class="mono" dir="ltr">${safe(item.parent_phone)}</span></td>
          <td><span class="message-preview" title="${safe(item.message_body)}">${safe(item.message_body)}</span></td>
          <td>${status}${item.error_msg ? `<div class="queue-status-line">${safe(item.error_msg)}</div>` : ""}</td>
          <td>${link || '<span class="muted tiny">—</span>'}</td>
        </tr>`;
      })
      .join("");
    A.openModal({
      title: isWhatsapp ? "حملة واتساب" : "حملة SMS",
      subtitle: `${A.formatDateTime ? A.formatDateTime(campaign.created_at) : formatDateTime(campaign.created_at)} · ${safe(campaign.scope_label)}`,
      size: "xl",
      icon: CHANNEL_ICONS[campaign.channel] || "message",
      body: `
        <div class="close-summary" style="margin-bottom:14px">
          <div><strong>${A.formatInt(campaign.recipients_total || 0)}</strong><span>في النطاق</span></div>
          <div><strong>${A.formatInt(campaign.eligible_total || 0)}</strong><span>مؤهل</span></div>
          <div><strong>${A.formatInt(campaign.skipped_total || 0)}</strong><span>مستبعد</span></div>
        </div>
        <div class="info-banner ${manualWhatsapp ? "is-warning" : "is-success"}">
          ${A.icon(isWhatsapp ? "whatsapp" : "send")}
          <div>
            <strong>${safe(campaign.channel_label)}</strong>
            ${manualWhatsapp
              ? " هذه الحملة لا ترسل تلقائيًا. استخدم زر واتساب بجانب كل ولي لفتح المحادثة ومراجعتها."
              : ` ${A.formatInt(counts.SENT || 0)} ناجحة، ${A.formatInt(counts.FAILED || 0)} فاشلة، ${A.formatInt(counts.PENDING || 0)} معلقة.`}
          </div>
        </div>
        <div class="campaign-message"><strong>نص الحملة</strong><p>${safe(campaign.message_template)}</p></div>
        <div class="table-wrap" style="margin-top:12px">
          <table class="data-table">
            <thead><tr><th>التلميذ</th><th>القسم</th><th>الهاتف</th><th>الرسالة</th><th>الحالة</th><th style="width:1%">واتساب</th></tr></thead>
            <tbody>${rows || '<tr><td colspan="6" class="empty-cell">لا توجد عناصر.</td></tr>'}</tbody>
          </table>
        </div>
        ${data.total > items.length ? `<p class="hint-inline" style="margin-top:8px">تظهر أول ${A.formatInt(items.length)} من ${A.formatInt(data.total)} عنصرًا.</p>` : ""}`,
      footer: `<button class="btn btn-secondary" type="button" data-modal-close>إغلاق</button>`,
    });
  }

  /* ---------------------------------------------------------------------- */
  /* Events                                                                  */
  /* ---------------------------------------------------------------------- */

  function bind() {
    els.scope.addEventListener("click", (event) => {
      const button = event.target.closest("[data-scope]");
      if (!button || button.disabled) return;
      setScope(button.dataset.scope);
    });
    els.classFilter.addEventListener("change", () => {
      state.classGroup = els.classFilter.value;
      renderRecipientSummary();
      schedulePreview();
    });
    els.pick.addEventListener("click", openPicker);
    els.clearSelected.addEventListener("click", () => {
      state.selected.clear();
      renderSelected();
      renderRecipientSummary();
      renderScope();
      schedulePreview();
    });
    els.selectedChips.addEventListener("click", (event) => {
      const button = event.target.closest("[data-remove-selected]");
      if (!button) return;
      state.selected.delete(button.dataset.removeSelected);
      renderSelected();
      renderRecipientSummary();
      renderScope();
      schedulePreview();
    });
    els.channelRadios.forEach((radio) => {
      radio.addEventListener("change", () => {
        if (!radio.checked) return;
        state.channel = radio.value;
        syncChannelCards();
        syncChannelMessage();
        renderRecipientSummary();
        schedulePreview();
      });
    });
    els.body.addEventListener("input", () => {
      renderBodyCount();
      updateApprovalText();
      updateSendState();
      schedulePreview();
    });
    document.querySelectorAll("[data-msg-var]").forEach((button) => {
      button.addEventListener("click", () => {
        const variable = button.dataset.msgVar || "";
        const start = els.body.selectionStart || 0;
        const end = els.body.selectionEnd || 0;
        const value = els.body.value;
        els.body.value = value.slice(0, start) + variable + value.slice(end);
        els.body.selectionStart = els.body.selectionEnd = start + variable.length;
        els.body.focus();
        els.body.dispatchEvent(new Event("input"));
      });
    });
    els.approve.addEventListener("change", updateSendState);
    els.send.addEventListener("click", sendCampaign);
    els.gatewayCheck.addEventListener("click", () => loadGateway(true));
    if (els.gatewayPair) els.gatewayPair.addEventListener("click", () => openPairing("gateway"));
    if (els.scanPhonesAdd) els.scanPhonesAdd.addEventListener("click", () => openPairing("scan"));
    if (els.scanPhonesRefresh) els.scanPhonesRefresh.addEventListener("click", () => loadScanPhones(true));
    if (els.scanPhonesList) {
      els.scanPhonesList.addEventListener("click", async (event) => {
        const button = event.target.closest("[data-remove-scan-phone]");
        if (!button) return;
        A.setBusy(button, true);
        try {
          const data = await A.api(
            `/api/scanner/phones/${encodeURIComponent(button.dataset.removeScanPhone)}`,
            { method: "DELETE" }
          );
          A.toast(data.message || "تمت إزالة هاتف المسح.", "success");
          await loadScanPhones();
        } catch (error) {
          A.toast(error.message || "تعذر إزالة هاتف المسح.", "error");
          A.setBusy(button, false);
        }
      });
    }
    els.refresh.addEventListener("click", () => {
      loadStudents();
      loadHistory();
      loadGateway(true);
      loadScanPhones(true);
    });
    els.historyRefresh.addEventListener("click", loadHistory);
    els.history.addEventListener("click", (event) => {
      const retry = event.target.closest("[data-retry-history]");
      if (retry) {
        loadHistory();
        return;
      }
      const item = event.target.closest("[data-campaign]");
      if (item) openCampaign(item.dataset.campaign);
    });
    document.addEventListener("click", (event) => {
      const copy = event.target.closest("[data-copy-message]");
      if (!copy) return;
      const text = copy.dataset.copyMessage || "";
      if (!text) return;
      const done = () => A.toast("تم نسخ نص الرسالة.", "success");
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done).catch(() => A.toast("تعذر النسخ التلقائي.", "warning"));
      } else {
        A.toast("النسخ غير متاح في هذا المتصفح.", "warning");
      }
    });
  }

  async function init() {
    bind();
    renderBodyCount();
    await loadStudents();
    loadGateway();
    loadScanPhones();
    loadHistory();
    resumeJob();
    state.gatewayTimer = window.setInterval(() => {
      if (!document.hidden) loadGateway();
    }, 15000);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) loadGateway();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
