/* Attendance register and parent SMS operations. */
(function () {
  "use strict";

  const A = window.App;
  const WF = window.AbsenceWorkflow;

  const SMS_LABELS = {
    PENDING: "في الانتظار",
    SENT: "تم الإرسال",
    FAILED: "فشل",
    CANCELLED: "ملغاة",
  };
  const STATUS_LABELS = {
    PRESENT: "حاضر",
    LATE: "متأخر",
    ABSENT: "غائب",
    EXCUSED: "غياب مبرر",
  };

  const $ = (id) => document.getElementById(id);
  const els = {
    date: $("att-date"),
    refresh: $("att-refresh"),
    reportLink: $("att-report-link"),
    period: $("att-period"),
    close: $("att-close"),
    statusIcon: $("att-status-icon"),
    statusTitle: $("att-status-title"),
    statusText: $("att-status-text"),
    statusTags: $("att-status-tags"),
    tabs: Array.from(document.querySelectorAll("[data-att-tab]")),
    logsPanel: $("att-panel-logs"),
    smsPanel: $("att-panel-sms"),
    logsCount: $("att-logs-count"),
    smsCount: $("att-sms-count"),
    search: $("att-search"),
    classFilter: $("att-class-filter"),
    statusFilter: $("att-status-filter"),
    perPage: $("att-per-page"),
    chips: {
      PRESENT: $("att-chip-present"),
      LATE: $("att-chip-late"),
      ABSENT: $("att-chip-absent"),
      EXCUSED: $("att-chip-excused"),
    },
    logsLabel: $("att-logs-label"),
    tableBody: $("att-table-body"),
    pagination: $("att-pagination"),
    smsSearch: $("sms-search"),
    smsStatusFilter: $("sms-status-filter"),
    smsPerPage: $("sms-per-page"),
    smsTestChip: $("sms-test-chip"),
    smsDispatch: $("sms-dispatch"),
    smsGateway: $("sms-gateway-badge"),
    smsLabel: $("sms-logs-label"),
    smsTableBody: $("sms-table-body"),
    smsPagination: $("sms-pagination"),
    smsCounts: {
      PENDING: $("sms-count-pending"),
      SENT: $("sms-count-sent"),
      FAILED: $("sms-count-failed"),
      CANCELLED: $("sms-count-cancelled"),
    },
    job: $("sms-job-progress"),
    jobTitle: $("sms-job-title"),
    jobNote: $("sms-job-note"),
    jobBar: $("sms-job-bar"),
    jobMeta: $("sms-job-meta"),
  };

  const state = {
    date: (document.body.dataset.today || A.todayISO()),
    periodId: els.period ? els.period.value : "",
    classGroup: "",
    status: "",
    q: "",
    page: 1,
    perPage: 25,
    logsTotal: 0,
    logsSummary: {},
    logsRequest: 0,
    smsStatus: "",
    smsQ: "",
    smsPage: 1,
    smsPerPage: 20,
    smsCounts: { PENDING: 0, SENT: 0, FAILED: 0, CANCELLED: 0, total: 0 },
    smsRequest: 0,
    logs: [],
    smsItems: [],
    tab: "logs",
    preview: null,
    smsTestMode: false,
    gateway: "local",
    jobId: "",
    jobWatcher: null,
    periodsLoaded: false,
  };

  function safe(value) {
    return A.escapeHtml(value === null || value === undefined ? "" : value);
  }

  function paramsWith(base) {
    return A.apiUrl(base, base === "/api/attendance/list"
      ? {
          date: state.date,
          period_id: state.periodId,
          class_group: state.classGroup,
          status: state.status,
          q: state.q,
          page: state.page,
          per_page: state.perPage,
        }
      : {
          date: state.date,
          period_id: state.periodId,
          status: state.smsStatus,
          q: state.smsQ,
          page: state.smsPage,
          per_page: state.smsPerPage,
        });
  }

  function syncUrl() {
    const params = new URLSearchParams();
    if (state.date) params.set("date", state.date);
    if (state.periodId) params.set("period_id", state.periodId);
    if (state.tab !== "logs") params.set("tab", state.tab);
    if (state.status) params.set("status", state.status);
    if (state.classGroup) params.set("class_group", state.classGroup);
    if (state.q) params.set("q", state.q);
    const url = `${window.location.pathname}${params.toString() ? `?${params}` : ""}`;
    window.history.replaceState({}, "", url);
  }

  function readUrl() {
    const params = new URLSearchParams(window.location.search);
    const dateParam = params.get("date");
    if (dateParam && /^\d{4}-\d{2}-\d{2}$/.test(dateParam) && dateParam <= (document.body.dataset.today || A.todayISO())) {
      state.date = dateParam;
    }
    els.date.value = state.date;
    const periodParam = params.get("period_id");
    if (periodParam && Array.from(els.period.options).some((option) => option.value === periodParam)) {
      els.period.value = periodParam;
    }
    state.periodId = els.period.value;
    const tab = params.get("tab");
    if (tab === "sms" || tab === "logs") state.tab = tab;
    const status = params.get("status");
    if (status && STATUS_LABELS[status]) {
      state.status = status;
      els.statusFilter.value = status;
    }
    const classGroup = params.get("class_group");
    if (classGroup && Array.from(els.classFilter.options).some((option) => option.value === classGroup)) {
      state.classGroup = classGroup;
      els.classFilter.value = classGroup;
    }
    const q = params.get("q");
    if (q) {
      state.q = q;
      els.search.value = q;
    }
    state.perPage = Number(els.perPage.value) || 25;
  }

  /* ---------------------------------------------------------------------- */
  /* Periods + closure status                                                */
  /* ---------------------------------------------------------------------- */

  async function loadPeriods() {
    try {
      const data = await A.api(`/api/periods?date=${encodeURIComponent(state.date)}`);
      const periods = Array.isArray(data.periods) ? data.periods : [];
      if (!periods.length) {
        els.period.innerHTML = '<option value="">لا توجد فترات</option>';
        state.periodId = "";
        els.close.disabled = true;
        return;
      }
      const previous = state.periodId;
      els.period.innerHTML = periods
        .map((period) => {
          const closed = period.closure ? " · مغلقة" : "";
          return `<option value="${period.id}">${safe(period.period_name)}${safe(closed)}</option>`;
        })
        .join("");
      const hasPrevious = periods.some((period) => String(period.id) === previous);
      const fallback = periods.find((period) => period.is_active) || periods[0];
      state.periodId = String(hasPrevious ? previous : fallback.id);
      els.period.value = state.periodId;
      els.close.disabled = false;
      els.close.querySelector("span").textContent = "إغلاق الفترة ومعالجة الغياب";
    } catch (error) {
      els.period.innerHTML = '<option value="">تعذر تحميل الفترات</option>';
      A.toast(error.message, "error");
    } finally {
      state.periodsLoaded = true;
    }
  }

  function renderStatus(preview) {
    if (!preview) {
      els.statusIcon.classList.remove("is-closed");
      els.statusTitle.textContent = "لا توجد فترة محددة";
      els.statusText.textContent = "أضف فترة دراسية من الإعدادات لبدء متابعة الحضور وإغلاق الغياب.";
      els.statusTags.innerHTML = `<a class="chip is-warning" href="/settings#periods">${A.icon("settings")} إعداد الفترات</a>`;
      els.close.disabled = true;
      return;
    }
    const closed = Boolean(preview.closed);
    const periodName = preview.period ? preview.period.period_name : "الفترة";
    const pending = Number((preview.sms_summary || {}).PENDING || 0);
    const absent = closed ? Number(preview.existing_absences || 0) : Number(preview.missing_count || 0);
    const withPhone = closed ? Number(preview.absent_phone_count || 0) : Number(preview.with_phone || 0);
    const withoutPhone = Number(preview.without_phone || 0);

    els.statusIcon.classList.toggle("is-closed", closed);
    els.statusIcon.innerHTML = A.icon(closed ? "shield-check" : "clock");
    els.statusTitle.textContent = closed ? `${periodName} — الفترة مغلقة` : `${periodName} — الفترة مفتوحة`;
    els.statusText.textContent = closed
      ? `تم تثبيت ${A.formatInt(absent)} حالة غياب لهذا اليوم، ${pending > 0 ? `${A.formatInt(pending)} رسالة SMS في انتظار الإرسال.` : "ولا توجد رسائل معلّقة."}`
      : `${A.formatInt(absent)} تلميذًا لم يُمسح بعد. عند إغلاق الفترة سيُسجَّلون غائبين ويمكن إشعار الأولياء بعد موافقتك.`;
    const tags = [`<span class="chip">${A.icon("calendar")} ${A.formatDate(preview.date)}</span>`];
    tags.push(`<span class="chip ${closed ? "" : "is-warning"}">${A.icon(closed ? "check" : "warning")} ${closed ? "مغلقة" : "بانتظار الإغلاق"}</span>`);
    tags.push(`<span class="chip">${A.icon("phone")} ${A.formatInt(withPhone)} ولي له هاتف</span>`);
    if (withoutPhone > 0) tags.push(`<span class="chip is-danger">${A.icon("warning")} ${A.formatInt(withoutPhone)} بدون هاتف</span>`);
    if (pending > 0) tags.push(`<span class="chip is-warning">${A.icon("message")} ${A.formatInt(pending)} رسالة معلقة</span>`);
    els.statusTags.innerHTML = tags.join("");
    els.close.disabled = false;
    const label = closed ? "متابعة الغياب والرسائل" : "إغلاق الفترة ومعالجة الغياب";
    els.close.querySelector("span").textContent = label;
    state.smsTestMode = Boolean(preview.test_mode);
    updateSmsChrome();
  }

  async function loadStatus() {
    if (!state.periodId) {
      renderStatus(null);
      return;
    }
    try {
      const preview = await A.api(
        A.apiUrl("/api/attendance/absence-preview", { period_id: state.periodId, date: state.date })
      );
      state.preview = preview;
      renderStatus(preview);
    } catch (error) {
      renderStatus(null);
      els.statusTitle.textContent = "تعذر تحميل حالة الفترة";
      els.statusText.textContent = error.message || "أعد المحاولة.";
    }
  }

  /* ---------------------------------------------------------------------- */
  /* Attendance log                                                          */
  /* ---------------------------------------------------------------------- */

  function renderLogSummary(summary) {
    Object.keys(els.chips).forEach((key) => {
      const count = Number((summary || {})[key] || 0);
      els.chips[key].innerHTML = `<strong>${A.formatInt(count)}</strong>&nbsp;${STATUS_LABELS[key]}`;
    });
  }

  function logSummaryLabel(data) {
    const bits = [A.t(`${A.formatInt(data.total)} سجل`)];
    bits.push(state.periodId
      ? els.period.options[els.period.selectedIndex]?.textContent || A.t("الفترة")
      : A.t("كل الفترات"));
    if (state.classGroup === "__none__") bits.push(A.t("بدون قسم"));
    else if (state.classGroup) bits.push(A.t(`القسم: ${state.classGroup}`));
    if (state.status) bits.push(A.t(STATUS_LABELS[state.status]));
    if (state.q) bits.push(A.t(`بحث: «${state.q}»`));
    if (data.pages > 1) bits.push(A.t(`صفحة ${data.page} من ${data.pages}`));
    return bits.join(" — ");
  }

  function emptyLogsRow(afterFilter) {
    const text = afterFilter
      ? "لا توجد سجلات مطابقة للفلاتر الحالية. جرّب تغيير الحالة أو القسم."
      : "لم يُسجَّل أي حضور بعد لهذا اليوم. افتح بوابة المسح لتسجيل الوصول، أو أغلق الفترة لرصد الغياب.";
    const action = afterFilter
      ? '<button class="btn btn-secondary btn-sm" type="button" data-reset-logs>مسح الفلاتر</button>'
      : `<a class="btn btn-primary btn-sm" href="/gate">${A.icon("scan")} فتح بوابة المسح</a>`;
    return `<tr><td colspan="7" class="empty-cell">${A.emptyState("list", afterFilter ? "لا توجد نتائج" : "لا توجد سجلات بعد", text, action)}</td></tr>`;
  }

  function renderLogs(data) {
    state.logsTotal = Number(data.total) || 0;
    state.logsSummary = data.summary || {};
    els.logsCount.textContent = A.formatInt(state.logsTotal);
    renderLogSummary(state.logsSummary);
    els.logsLabel.textContent = logSummaryLabel(data);
    const items = Array.isArray(data.items) ? data.items : [];
    state.logs = items;
    const filtered = Boolean(state.status || state.q || state.classGroup);
    if (!items.length) {
      els.tableBody.innerHTML = emptyLogsRow(filtered);
    } else {
      els.tableBody.innerHTML = items
        .map((item) => {
          const sms = item.sms_status
            ? `${A.statusBadge(item.sms_status, SMS_LABELS[item.sms_status] || item.sms_status)}${item.sms_is_test ? '<span class="chip is-accent" style="margin-top:3px">اختبار</span>' : ""}`
            : '<span class="muted tiny">—</span>';
          const note = item.note
            ? `<span class="message-preview" title="${safe(item.note)}">${safe(item.note)}</span>`
            : '<span class="muted tiny">—</span>';
          const actions = [];
          if (item.status === "ABSENT" && item.whatsapp_url) {
            actions.push(`<a class="icon-btn is-whatsapp" href="${safe(item.whatsapp_url)}" target="_blank" rel="noopener noreferrer" title="مراسلة الولي عبر واتساب" aria-label="مراسلة ولي ${safe(item.full_name)} عبر واتساب">${A.icon("whatsapp")}</a>`);
          }
          if (item.status === "ABSENT") {
            actions.push(`<button class="icon-btn" type="button" data-excuse="${item.id}" data-name="${safe(item.full_name)}" title="تبرير الغياب" aria-label="تبرير غياب ${safe(item.full_name)}">${A.icon("shield-check")}</button>`);
          }
          if (item.note) {
            actions.push(`<button class="icon-btn" type="button" data-log-note="${item.id}" title="عرض الملاحظة" aria-label="عرض ملاحظة ${safe(item.full_name)}">${A.icon("eye")}</button>`);
          }
          return `<tr>
            <td>
              <div class="student-identity">
                ${A.avatar(item)}
                <div>
                  <strong>${safe(item.full_name || "بدون اسم")}</strong>
                  <small class="student-nin">${safe(item.student_nin)}</small>
                </div>
              </div>
            </td>
            <td>
              <span class="chip">${safe(item.class_group)}</span>
              <div class="tiny muted" style="margin-top:2px">${safe(item.period_name)}</div>
            </td>
            <td><span class="time-chip">${A.icon("clock")} ${safe(item.scan_time || "—")}</span></td>
            <td>${A.statusBadge(item.status, item.status_label)}</td>
            <td>${sms}</td>
            <td class="col-note">${note}</td>
            <td>
              <div class="actions">
                ${actions.length ? actions.join("") : '<span class="muted tiny">—</span>'}
              </div>
            </td>
          </tr>`;
        })
        .join("");
    }
    A.pagination(els.pagination, {
      page: data.page,
      pages: data.pages,
      total: data.total,
      onChange(page) {
        state.page = page;
        loadLogs();
        window.scrollTo({ top: 0, behavior: "smooth" });
      },
    });
  }

  async function loadLogs(options = {}) {
    const requestId = ++state.logsRequest;
    if (!options.silent) {
      els.tableBody.innerHTML = '<tr><td colspan="7" class="empty-cell"><div class="loading-state"><span class="spinner"></span><span>جارٍ تحميل السجلات…</span></div></td></tr>';
    }
    try {
      const data = await A.api(paramsWith("/api/attendance/list"));
      if (requestId !== state.logsRequest) return;
      if (state.page > (data.pages || 1)) {
        state.page = data.pages || 1;
        return loadLogs(options);
      }
      renderLogs(data);
    } catch (error) {
      if (requestId !== state.logsRequest) return;
      els.tableBody.innerHTML = `<tr><td colspan="7" class="empty-cell">${A.emptyState("warning", "تعذر تحميل سجل الحضور", error.message, '<button class="btn btn-secondary btn-sm" type="button" data-retry-logs>إعادة المحاولة</button>')}</td></tr>`;
      els.logsLabel.textContent = "تعذر تحميل السجلات.";
      els.pagination.innerHTML = "";
      if (!options.silent) A.toast(error.message, "error");
    }
  }

  /* ---------------------------------------------------------------------- */
  /* SMS queue                                                               */
  /* ---------------------------------------------------------------------- */

  function updateSmsChrome() {
    els.smsTestChip.classList.toggle("hidden", !state.smsTestMode);
    els.smsGateway.textContent = state.gateway === "cloud" ? "بوابة سحابية" : "بوابة محلية";
    if (state.smsTestMode) {
      els.smsGateway.className = "badge badge--update";
    } else {
      els.smsGateway.className = "badge badge--present";
    }
    const pending = Number(state.smsCounts.PENDING || 0);
    els.smsDispatch.disabled = pending === 0;
    els.smsDispatch.querySelector("span").textContent = pending
      ? "إرسال الرسائل المعلّقة"
      : "لا توجد رسائل معلّقة";
    els.smsCount.textContent = A.formatInt(pending);
  }

  function renderSmsCounts(counts) {
    state.smsCounts = counts || { PENDING: 0, SENT: 0, FAILED: 0, CANCELLED: 0, total: 0 };
    Object.keys(els.smsCounts).forEach((key) => {
      els.smsCounts[key].textContent = A.formatInt(state.smsCounts[key] || 0);
    });
    updateSmsChrome();
  }

  function timeOf(value) {
    const text = String(value || "");
    return /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}/.test(text) ? text.slice(11, 16) : "";
  }

  function smsRow(item) {
    const canRetry = item.delivery_status === "FAILED" || item.delivery_status === "PENDING";
    const created = timeOf(item.created_at);
    const sent = timeOf(item.sent_at);
    return `<tr>
      <td>
        <div class="student-identity">
          ${A.avatar(item)}
          <div>
            <strong>${safe(item.full_name || "بدون اسم")}</strong>
            <small class="student-nin">${safe(item.student_nin)}</small>
          </div>
        </div>
      </td>
      <td><span class="chip">${safe(item.class_group)}</span><div class="tiny muted" style="margin-top:2px">${safe(item.period_name)}</div></td>
      <td><span class="mono">${safe(item.parent_phone || "—")}</span></td>
      <td><span class="message-preview" title="${safe(item.message_body)}">${safe(item.message_body)}</span></td>
      <td>
        ${A.statusBadge(item.delivery_status, item.delivery_status_label)}
        ${item.is_test ? '<div class="queue-status-line">محاكاة اختبار</div>' : ""}
        ${item.error_msg ? `<div class="queue-status-line" title="${safe(item.error_msg)}">${A.icon("warning")} ${safe(item.error_msg.slice(0, 42))}${item.error_msg.length > 42 ? "…" : ""}</div>` : ""}
      </td>
      <td>
        <div class="tiny">${A.formatInt(item.attempts || 0)} محاولة</div>
        <div class="queue-status-line">${sent ? `أُرسلت ${safe(sent)}` : created ? `أُنشئت ${safe(created)}` : ""}</div>
      </td>
      <td>
        <div class="actions">
          <button class="icon-btn" type="button" data-sms-view="${item.id}" title="عرض الرسالة" aria-label="عرض رسالة ${safe(item.full_name)}">${A.icon("eye")}</button>
          ${item.whatsapp_url ? `<a class="icon-btn is-whatsapp" href="${safe(item.whatsapp_url)}" target="_blank" rel="noopener noreferrer" title="إرسال عبر واتساب" aria-label="إرسال رسالة ${safe(item.full_name)} عبر واتساب">${A.icon("whatsapp")}</a>` : ""}
          ${canRetry ? `<button class="icon-btn" type="button" data-sms-retry="${item.id}" title="إعادة المحاولة" aria-label="إعادة محاولة رسالة ${safe(item.full_name)}">${A.icon("refresh")}</button>` : ""}
        </div>
      </td>
    </tr>`;
  }

  function renderSms(data) {
    state.smsTestMode = Boolean(data.test_mode);
    state.gateway = data.gateway || "local";
    renderSmsCounts(data.counts || {});
    const pending = Number((data.counts || {}).PENDING || 0);
    els.smsLabel.textContent = `${A.formatInt(data.total)} رسالة${pending ? ` — ${A.formatInt(pending)} في الانتظار` : ""}${state.smsTestMode ? " — وضع الاختبار" : ""}`;
    const items = Array.isArray(data.items) ? data.items : [];
    state.smsItems = items;
    if (!items.length) {
      const filtered = Boolean(state.smsStatus || state.smsQ);
      const text = filtered
        ? "لا توجد رسائل مطابقة للفلاتر الحالية."
        : "لم تُنشأ رسائل غياب بعد. أغلق الفترة من الأعلى ثم وافق على إشعار الأولياء.";
      const action = filtered
        ? '<button class="btn btn-secondary btn-sm" type="button" data-reset-sms>مسح الفلاتر</button>'
        : "";
      els.smsTableBody.innerHTML = `<tr><td colspan="7" class="empty-cell">${A.emptyState("message", filtered ? "لا توجد نتائج" : "صندوق الرسائل فارغ", text, action)}</td></tr>`;
    } else {
      els.smsTableBody.innerHTML = items.map(smsRow).join("");
    }
    A.pagination(els.smsPagination, {
      page: data.page,
      pages: data.pages,
      total: data.total,
      onChange(page) {
        state.smsPage = page;
        loadSms();
        window.scrollTo({ top: 0, behavior: "smooth" });
      },
    });
    if (data.active_job && data.active_job.status === "running") {
      showJob(data.active_job);
      watchJob(data.active_job.id);
    } else if (data.latest_job && data.latest_job.id === state.jobId && data.latest_job.status === "done") {
      showJob(data.latest_job);
    }
  }

  async function loadSms(options = {}) {
    const requestId = ++state.smsRequest;
    if (!options.silent) {
      els.smsTableBody.innerHTML = '<tr><td colspan="7" class="empty-cell"><div class="loading-state"><span class="spinner"></span><span>جارٍ تحميل الرسائل…</span></div></td></tr>';
    }
    try {
      const data = await A.api(paramsWith("/api/sms/queue"));
      if (requestId !== state.smsRequest) return;
      if (state.smsPage > (data.pages || 1)) {
        state.smsPage = data.pages || 1;
        return loadSms(options);
      }
      renderSms(data);
    } catch (error) {
      if (requestId !== state.smsRequest) return;
      els.smsTableBody.innerHTML = `<tr><td colspan="7" class="empty-cell">${A.emptyState("warning", "تعذر تحميل صندوق الرسائل", error.message, '<button class="btn btn-secondary btn-sm" type="button" data-retry-sms>إعادة المحاولة</button>')}</td></tr>`;
      els.smsLabel.textContent = "تعذر تحميل الرسائل.";
      els.smsPagination.innerHTML = "";
      if (!options.silent) A.toast(error.message, "error");
    }
  }

  /* ---------------------------------------------------------------------- */
  /* SMS dispatch + job progress                                             */
  /* ---------------------------------------------------------------------- */

  function setJobIcon(done) {
    const spinner = els.job.querySelector(".spinner");
    if (done) {
      if (spinner) {
        const holder = document.createElement("div");
        holder.style.cssText = "display:grid;place-items:center;width:20px;height:20px;color:var(--success)";
        holder.innerHTML = A.icon("check");
        spinner.replaceWith(holder);
      }
    } else if (!spinner) {
      const holder = document.createElement("div");
      holder.style.cssText = "display:grid;place-items:center;width:20px;height:20px;color:var(--primary)";
      holder.innerHTML = A.icon("send");
      const first = els.job.firstElementChild;
      if (first) first.replaceWith(holder);
    }
  }

  function showJob(job) {
    if (!job) return;
    const total = Number(job.total || 0);
    const processed = Number(job.processed || 0);
    const percent = total ? Math.round((processed / total) * 100) : 0;
    const done = job.status === "done";
    els.job.classList.add("is-active");
    setJobIcon(done);
    els.jobTitle.textContent = done ? "اكتملت معالجة الرسائل" : "جارٍ إرسال الرسائل إلى الأولياء";
    els.jobNote.textContent = job.message || (job.current_name ? `جارٍ معالجة: ${job.current_name}` : "جارٍ التحضير…");
    els.jobBar.style.width = `${done ? 100 : percent}%`;
    els.jobMeta.textContent = `${A.formatInt(job.sent || 0)} ناجحة · ${A.formatInt(job.failed || 0)} فاشلة${job.simulated ? ` · ${A.formatInt(job.simulated)} اختبار` : ""}${job.error ? ` · ${job.error}` : ""}`;
  }

  function hideJob() {
    els.job.classList.remove("is-active");
    els.jobBar.style.width = "0%";
  }

  function watchJob(jobId) {
    if (!jobId || state.jobId === jobId || !WF) return;
    state.jobId = jobId;
    if (state.jobWatcher && state.jobWatcher.stop) state.jobWatcher.stop();
    state.jobWatcher = WF.watchJob(
      jobId,
      (job) => {
        if (job.id === state.jobId) showJob(job);
      },
      (job) => {
        if (job.id !== state.jobId) return;
        showJob(job);
        A.toast(job.message || "اكتملت معالجة الرسائل.", job.failed ? "warning" : "success");
        state.jobId = "";
        window.setTimeout(hideJob, 2600);
        loadLogs({ silent: true });
        loadStatus();
        loadSms({ silent: true });
      }
    );
  }

  async function openDispatchModal() {
    if (els.smsDispatch.disabled) return;
    A.setBusy(els.smsDispatch, true);
    let pending = 0;
    try {
      const data = await A.api(
        A.apiUrl("/api/sms/queue", {
          date: state.date,
          period_id: state.periodId,
          status: "PENDING",
          per_page: 5,
        })
      );
      pending = Number((data.counts || {}).PENDING || 0);
    } catch (error) {
      A.toast(error.message, "error");
      A.setBusy(els.smsDispatch, false);
      return;
    }
    A.setBusy(els.smsDispatch, false);
    if (!pending) {
      A.toast("لا توجد رسائل معلّقة لهذا التاريخ وهذه الفترة.", "info");
      loadSms({ silent: true });
      return;
    }
    const periodLabel = els.period.options[els.period.selectedIndex]?.textContent || "الفترة المحددة";
    const modal = A.openModal({
      title: "إرسال رسائل الغياب المعلّقة",
      subtitle: `${A.formatInt(pending)} رسالة جاهزة للإرسال`,
      icon: "send",
      body: `
        <div class="info-banner is-warning">${A.icon("warning")}<div>سيتم إرسال <strong>${A.formatInt(pending)} رسالة SMS</strong> إلى أولياء التلاميذ الغائبين ضمن <strong>${safe(periodLabel)}</strong> بتاريخ <strong>${A.formatDate(state.date)}</strong>. لا يمكن التراجع بعد بدء الإرسال.</div></div>
        ${state.smsTestMode ? `<div class="test-mode-note" style="margin-top:12px">${A.icon("shield-check")}<div>وضع الاختبار مفعّل: ستُسجَّل الرسائل كمحاكاة ناجحة دون إرسال حقيقي أو استهلاك رصيد.</div></div>` : `<div class="info-banner is-danger" style="margin-top:12px">${A.icon("warning")}<div>وضع الإرسال الحقيقي مفعّل. تأكد من صحة أرقام الأولياء وبوابة SMS قبل المتابعة.</div></div>`}
        <div class="approval-box" style="margin-top:14px">
          <input type="checkbox" id="sms-approve" autocomplete="off">
          <label for="sms-approve"><strong>موافقة صريحة على الإرسال</strong>أوافق على إرسال الرسائل المعلّقة الآن إلى أولياء التلاميذ المعنيين.</label>
        </div>`,
      footer: `<button class="btn btn-secondary" type="button" data-modal-close>إلغاء</button>
               <button class="btn btn-warning" type="button" id="sms-approve-send" disabled>${A.icon("send")} تأكيد الإرسال</button>`,
    });
    const checkbox = modal.element.querySelector("#sms-approve");
    const button = modal.element.querySelector("#sms-approve-send");
    checkbox.addEventListener("change", () => {
      button.disabled = !checkbox.checked;
    });
    button.addEventListener("click", async () => {
      if (!checkbox.checked) return;
      A.setBusy(button, true);
      try {
        const data = await A.api("/api/sms/dispatch", {
          method: "POST",
          body: { approved: true, date: state.date, period_id: state.periodId || undefined },
        });
        A.closeModal();
        A.toast(data.message || "بدأ إرسال الرسائل.", "info");
        if (data.job && data.job.status === "running") {
          showJob(data.job);
          watchJob(data.job.id);
        }
        loadLogs({ silent: true });
        loadSms({ silent: true });
      } catch (error) {
        A.toast(error.message, "error");
        A.setBusy(button, false);
      }
    });
  }

  function openSmsDetail(smsId) {
    const item = state.smsItems.find((entry) => Number(entry.id) === Number(smsId));
    if (!item) return;
    A.openModal({
      title: `رسالة إلى ولي ${item.full_name}`,
      subtitle: `${item.period_name} — ${A.formatDate(item.attendance_date)}`,
      icon: "message",
      size: "lg",
      body: `
        <div class="flex between" style="margin-bottom:10px;flex-wrap:wrap;gap:8px">
          ${A.statusBadge(item.delivery_status, item.delivery_status_label)}
          ${item.is_test ? '<span class="chip is-accent">محاكاة اختبار</span>' : ""}
          ${item.gateway ? `<span class="chip">${safe(item.gateway)}</span>` : ""}
        </div>
        <div class="field">
          <span class="field-label">نص الرسالة</span>
          <div style="padding:12px 14px;border:1px solid var(--border);border-radius:11px;background:var(--surface-2);font-size:12px;line-height:1.8;white-space:pre-wrap">${safe(item.message_body)}</div>
        </div>
        <div class="form-grid" style="margin-top:14px">
          <div class="field"><span class="field-label">هاتف الولي</span><span class="mono">${safe(item.parent_phone || "—")}</span></div>
          <div class="field"><span class="field-label">عدد المحاولات</span><span>${A.formatInt(item.attempts || 0)}</span></div>
          <div class="field"><span class="field-label">تاريخ الإنشاء</span><span>${safe(item.created_at || "—")}</span></div>
          <div class="field"><span class="field-label">وقت الإرسال</span><span>${safe(item.sent_at || "—")}</span></div>
        </div>
        ${item.error_msg ? `<div class="info-banner is-warning" style="margin-top:14px">${A.icon("warning")}<div><strong>ملاحظة النظام:</strong> ${safe(item.error_msg)}</div></div>` : ""}
        ${item.whatsapp_url ? `<div class="info-banner" style="margin-top:14px">${A.icon("whatsapp")}<div><strong>المراسلة عبر واتساب:</strong> يفتح واتساب مع نص الرسالة جاهزًا (${safe(item.whatsapp_phone)}). راجع النص ثم اضغط «إرسال» داخل واتساب. لا يُرسل شيء تلقائيًا.</div></div>` : ""}`,
      footer: `<button class="btn btn-secondary" type="button" data-modal-close>إغلاق</button>
               ${item.whatsapp_url ? `<a class="btn btn-whatsapp" href="${safe(item.whatsapp_url)}" target="_blank" rel="noopener noreferrer">${A.icon("whatsapp")} فتح واتساب</a>` : ""}
               ${item.delivery_status === "FAILED" ? `<button class="btn btn-primary" type="button" id="sms-detail-retry">${A.icon("refresh")} إعادة المحاولة</button>` : ""}`,
    });
    const retry = modal.element.querySelector("#sms-detail-retry");
    if (retry) {
      retry.addEventListener("click", () => {
        A.closeModal();
        retrySms(item.id, item.full_name);
      });
    }
  }

  async function retrySms(smsId, name) {
    const confirmed = await A.confirm({
      title: "إعادة محاولة إرسال الرسالة",
      message: `سيُعاد إرسال رسالة SMS إلى ولي ${name || "التلميذ"} بعد موافقتك. هل تريد المتابعة؟`,
      confirmLabel: "إعادة المحاولة",
    });
    if (!confirmed) return;
    try {
      const data = await A.api(`/api/sms/${encodeURIComponent(smsId)}/retry`, {
        method: "POST",
        body: { approved: true },
      });
      A.toast(data.message || "تمت جدولة إعادة المحاولة.", "info");
      await loadSms({ silent: true });
      if (data.job && data.job.status === "running") {
        showJob(data.job);
        watchJob(data.job.id);
      }
    } catch (error) {
      A.toast(error.message, "error");
    }
  }

  /* ---------------------------------------------------------------------- */
  /* Tabs + events                                                           */
  /* ---------------------------------------------------------------------- */

  function setTab(tab) {
    state.tab = tab === "sms" ? "sms" : "logs";
    els.tabs.forEach((button) => {
      const active = button.dataset.attTab === state.tab;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-selected", active ? "true" : "false");
    });
    els.logsPanel.hidden = state.tab !== "logs";
    els.smsPanel.hidden = state.tab !== "sms";
    syncUrl();
    if (state.tab === "sms") {
      loadSms();
      loadStatus();
    } else {
      loadLogs();
      loadStatus();
    }
  }

  els.tabs.forEach((button) => {
    button.addEventListener("click", () => setTab(button.dataset.attTab));
  });

  els.date.addEventListener("change", () => {
    state.date = els.date.value || (document.body.dataset.today || A.todayISO());
    els.reportLink.href = `/reports?date=${encodeURIComponent(state.date)}`;
    state.page = 1;
    state.smsPage = 1;
    loadPeriods().then(() => {
      loadLogs();
      loadSms({ silent: true });
      loadStatus();
    });
    syncUrl();
  });

  els.refresh.addEventListener("click", () => {
    A.setBusy(els.refresh, true);
    Promise.allSettled([
      loadPeriods().then(() => Promise.allSettled([loadLogs(), loadSms({ silent: true }), loadStatus()])),
    ]).finally(() => {
      A.setBusy(els.refresh, false);
      A.toast("تم تحديث بيانات المتابعة.", "info");
    });
  });

  els.period.addEventListener("change", () => {
    state.periodId = els.period.value;
    state.page = 1;
    state.smsPage = 1;
    loadLogs({ silent: true });
    loadSms({ silent: true });
    loadStatus();
    syncUrl();
  });

  els.close.addEventListener("click", () => {
    if (!WF) return;
    WF.openCloseModal({
      periodId: state.periodId,
      date: state.date,
      closed: Boolean(state.preview && state.preview.closed),
      trigger: els.close,
      onDone() {
        loadStatus();
        loadLogs({ silent: true });
        loadSms({ silent: true });
      },
    });
  });

  els.search.addEventListener(
    "input",
    A.debounce(() => {
      state.q = els.search.value.trim();
      state.page = 1;
      loadLogs();
      syncUrl();
    }, 320)
  );
  els.classFilter.addEventListener("change", () => {
    state.classGroup = els.classFilter.value;
    state.page = 1;
    loadLogs();
    syncUrl();
  });
  els.statusFilter.addEventListener("change", () => {
    state.status = els.statusFilter.value;
    state.page = 1;
    loadLogs();
    syncUrl();
  });
  els.perPage.addEventListener("change", () => {
    state.perPage = Number(els.perPage.value) || 25;
    state.page = 1;
    loadLogs();
  });

  els.smsSearch.addEventListener(
    "input",
    A.debounce(() => {
      state.smsQ = els.smsSearch.value.trim();
      state.smsPage = 1;
      loadSms();
    }, 320)
  );
  els.smsStatusFilter.addEventListener("change", () => {
    state.smsStatus = els.smsStatusFilter.value;
    state.smsPage = 1;
    loadSms();
  });
  els.smsPerPage.addEventListener("change", () => {
    state.smsPerPage = Number(els.smsPerPage.value) || 20;
    state.smsPage = 1;
    loadSms();
  });
  els.smsDispatch.addEventListener("click", openDispatchModal);

  document.addEventListener("click", (event) => {
    const target = event.target.closest("button");
    if (!target) return;
    if (target.hasAttribute("data-excuse")) {
      const id = target.getAttribute("data-excuse");
      const name = target.getAttribute("data-name") || "";
      if (WF) {
        WF.openExcuseModal(id, name, () => {
          loadLogs({ silent: true });
          loadStatus();
        });
      }
      return;
    }
    if (target.hasAttribute("data-log-note")) {
      const id = Number(target.getAttribute("data-log-note"));
      const item = state.logs.find((entry) => Number(entry.id) === id);
      if (item) {
        A.openModal({
          title: `ملاحظة سجل ${item.full_name}`,
          subtitle: `${item.period_name} — ${A.formatDate(item.attendance_date)}`,
          icon: "file",
          body: `<div class="info-banner">${A.icon(item.status === "EXCUSED" ? "shield-check" : "file")}<div><strong>${safe(item.status_label)}</strong><br>${safe(item.note)}</div></div>`,
        });
      }
      return;
    }
    if (target.hasAttribute("data-sms-view")) {
      openSmsDetail(target.getAttribute("data-sms-view"));
      return;
    }
    if (target.hasAttribute("data-sms-retry")) {
      const id = Number(target.getAttribute("data-sms-retry"));
      const item = state.smsItems.find((entry) => Number(entry.id) === id);
      retrySms(id, item ? item.full_name : "");
      return;
    }
    if (target.hasAttribute("data-reset-logs")) {
      state.status = "";
      state.classGroup = "";
      state.q = "";
      els.search.value = "";
      els.classFilter.value = "";
      els.statusFilter.value = "";
      state.page = 1;
      loadLogs();
      syncUrl();
      return;
    }
    if (target.hasAttribute("data-reset-sms")) {
      state.smsStatus = "";
      state.smsQ = "";
      els.smsSearch.value = "";
      els.smsStatusFilter.value = "";
      state.smsPage = 1;
      loadSms();
      return;
    }
    if (target.hasAttribute("data-retry-logs")) {
      loadLogs();
      return;
    }
    if (target.hasAttribute("data-retry-sms")) {
      loadSms();
    }
  });

  /* ---------------------------------------------------------------------- */
  /* Bootstrap                                                               */
  /* ---------------------------------------------------------------------- */

  readUrl();
  els.reportLink.href = `/reports?date=${encodeURIComponent(state.date)}`;
  els.logsCount.textContent = "…";
  state.smsTestMode = false;
  updateSmsChrome();
  loadPeriods().then(() => {
    setTab(state.tab);
    loadSms({ silent: true });
  });
})();
