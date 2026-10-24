/* Bulk parent messaging center: recipients, channels, WhatsApp hand-off, SMS jobs. */
(function () {
  "use strict";

  const A = window.App;
  const $ = (id) => document.getElementById(id);

  const els = {
    statusChips: $("msg-status-chips"),
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
    const chips = [
      `<span class="chip ${testMode ? "is-warning" : ""}" id="msg-test-chip">${A.icon(testMode ? "shield-check" : "wifi")} ${testMode ? "وضع الاختبار مفعّل" : "الإرسال الحقيقي مفعّل"}</span>`,
      `<span class="chip ${mobile.ready ? "" : "is-danger"}">${A.icon("phone")} الهاتف: ${mobile.ready ? (mobile.configured ? "مهيأ" : "محاكاة") : "غير مهيأ"}</span>`,
      `<span class="chip ${cloud.ready ? "" : "is-danger"}">${A.icon("dashboard")} الحاسوب: ${cloud.ready ? (cloud.configured ? "مهيأ" : "محاكاة") : "غير مهيأ"}</span>`,
      `<span class="chip ${whatsapp.ready ? "is-accent" : "is-danger"}">${A.icon("whatsapp")} واتساب: ${whatsapp.ready ? "متاح يدويًا" : "معطّل"}</span>`,
    ];
    els.statusChips.innerHTML = chips.join("");
    els.totalChip.innerHTML = `<strong>${A.formatInt(state.counts.total || 0)}</strong>&nbsp;تلميذ نشط`;
    els.testNote.classList.toggle("hidden", !testMode);
    els.scopeAllCount.textContent = A.formatInt(state.counts.total || 0);
  }

  function renderChannels() {
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
        badge.className = `channel-state chip ${ready ? (configured ? "is-accent" : "is-warning") : "is-danger"}`;
        badge.textContent = ready
          ? configured
            ? "جاهزة للإرسال"
            : "محاكاة فقط"
          : "غير مهيأة";
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
  }

  function syncChannelCards() {
    els.channelCards.forEach((card) => {
      card.classList.toggle("is-active", card.dataset.channel === state.channel);
    });
  }

  function syncChannelMessage() {
    const info = channelInfo(state.channel);
    const warnings = [];
    if (!info.ready) {
      warnings.push(
        state.channel === "whatsapp"
          ? "واتساب معطّل من إعدادات الرسائل. فعّله لتتمكن من فتح المحادثات."
          : "هذه القناة غير مهيأة. أضف رابط البوابة المناسب من إعدادات الرسائل، أو استخدم وضع الاختبار."
      );
    }
    if (state.channel !== "whatsapp" && state.channels.test_mode && !info.configured) {
      warnings.push("وضع الاختبار مفعّل: سيتم تسجيل الإرسال كمحاكاة ناجحة فقط، دون اتصال بأي بوابة.");
    }
    if (state.channel === "whatsapp" && info.ready) {
      warnings.push("واتساب يدوي: لن يرسل النظام أي رسالة. ستفتح محادثة جاهزة لكل ولي وتؤكد الإرسال بنفسك.");
    }
    els.channelWarning.classList.toggle("hidden", warnings.length === 0);
    els.channelWarningText.textContent = warnings.join(" ");
    const label = state.channel === "whatsapp"
      ? "فتح محادثات واتساب"
      : state.channel === "cloud"
        ? "إرسال SMS عبر الحاسوب"
        : "إرسال SMS عبر الهاتف";
    els.send.querySelector("span").textContent = label;
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
    if (state.channel === "whatsapp") {
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
    } else if (!text) {
      hint = "اكتب نص الرسالة قبل المتابعة.";
      can = false;
    } else if (!approved) {
      hint = "فعّل مربع الموافقة لتأكيد الإرسال.";
      can = false;
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
      if (state.channel === "whatsapp") {
        els.segmentChip.classList.add("hidden");
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
      if (data.campaign && data.campaign.channel === "whatsapp") {
        openWhatsappResult(data);
      } else if (data.job) {
        els.progress.innerHTML = jobMarkup(data.job);
        watchJob(data.job.id);
      }
      A.toast(data.message || "تمت العملية.", data.test_mode ? "info" : "success");
      loadHistory();
    } catch (error) {
      A.toast(error.message || "تعذر بدء الإرسال.", "error");
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
    if (campaign.channel === "whatsapp") {
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
        <div class="info-banner ${isWhatsapp ? "is-warning" : "is-success"}">
          ${A.icon(isWhatsapp ? "whatsapp" : "send")}
          <div>
            <strong>${safe(campaign.channel_label)}</strong>
            ${isWhatsapp
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
      els.bodyCount.textContent = `${A.formatInt(els.body.value.length)} / 2000`;
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
    els.refresh.addEventListener("click", () => {
      loadStudents();
      loadHistory();
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
    els.bodyCount.textContent = "0 / 2000";
    await loadStudents();
    loadHistory();
    resumeJob();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
