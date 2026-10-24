/* Daily and monthly reports with Excel export and print/PDF output. */
(function () {
  "use strict";

  const A = window.App;

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
    kind: $("reports-kind"),
    kindButtons: Array.from(document.querySelectorAll("[data-report-kind]")),
    refresh: $("reports-refresh"),
    export: $("reports-export"),
    exportHero: $("reports-export-hero"),
    print: $("reports-print"),
    printHero: $("reports-print-hero"),
    generated: $("reports-generated"),
    dailyDateWrap: $("reports-daily-date-wrap"),
    monthWrap: $("reports-month-wrap"),
    date: $("reports-date"),
    month: $("reports-month"),
    period: $("reports-period-filter"),
    classFilter: $("reports-class-filter"),
    status: $("reports-status-filter"),
    scopeNote: $("reports-scope-note"),
    title: $("reports-title"),
    subtitle: $("reports-subtitle"),
    statPresent: $("reports-stat-present"),
    statLate: $("reports-stat-late"),
    statAbsent: $("reports-stat-absent"),
    statExcused: $("reports-stat-excused"),
    tableTitle: $("reports-table-title"),
    tableLabel: $("reports-table-label"),
    tableHead: $("reports-table-head"),
    tableBody: $("reports-table-body"),
    tableFoot: $("reports-table-foot"),
    tableMeta: $("reports-table-meta"),
    footNote: $("reports-foot-note"),
  };

  const today = (document.body.dataset.today || A.todayISO()).slice(0, 10);

  function monthNow() {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  }

  const state = {
    kind: "daily",
    date: today,
    month: els.month.value || monthNow(),
    periodId: "",
    classGroup: "",
    status: "",
    report: null,
    requestId: 0,
    loading: false,
  };

  function safe(value) {
    return A.escapeHtml(value === null || value === undefined ? "" : value);
  }

  function readUrl() {
    const params = new URLSearchParams(window.location.search);
    if (params.get("kind") === "monthly") state.kind = "monthly";
    const date = params.get("date");
    if (date && /^\d{4}-\d{2}-\d{2}$/.test(date) && date <= today) state.date = date;
    const month = params.get("month");
    if (month && /^\d{4}-\d{2}$/.test(month) && month <= monthNow()) state.month = month;
    const period = params.get("period_id");
    if (period && Array.from(els.period.options).some((option) => option.value === period)) {
      state.periodId = period;
    }
    const classGroup = params.get("class_group");
    if (classGroup && Array.from(els.classFilter.options).some((option) => option.value === classGroup)) {
      state.classGroup = classGroup;
    }
    const status = params.get("status");
    if (status && STATUS_LABELS[status]) state.status = status;
  }

  function syncUrl() {
    const params = new URLSearchParams();
    if (state.kind !== "daily") params.set("kind", state.kind);
    if (state.kind === "daily") {
      if (state.date !== today) params.set("date", state.date);
    } else if (state.month !== monthNow()) {
      params.set("month", state.month);
    }
    if (state.periodId) params.set("period_id", state.periodId);
    if (state.classGroup) params.set("class_group", state.classGroup);
    if (state.kind === "daily" && state.status) params.set("status", state.status);
    const url = `${window.location.pathname}${params.toString() ? `?${params}` : ""}`;
    window.history.replaceState({}, "", url);
  }

  function applyControls() {
    els.date.value = state.date;
    els.month.value = state.month;
    els.period.value = state.periodId;
    els.classFilter.value = state.classGroup;
    els.status.value = state.kind === "daily" ? state.status : "";
    els.status.disabled = state.kind !== "daily";
    els.dailyDateWrap.hidden = state.kind !== "daily";
    els.monthWrap.hidden = state.kind !== "monthly";
    els.kindButtons.forEach((button) => {
      const active = button.dataset.reportKind === state.kind;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-selected", active ? "true" : "false");
    });
  }

  function filters() {
    return {
      period_id: state.periodId,
      class_group: state.classGroup,
      ...(state.kind === "daily"
        ? { date: state.date, status: state.status }
        : { month: state.month }),
    };
  }

  function reportUrl(suffix) {
    return A.apiUrl(`/api/reports/${state.kind}${suffix || ""}`, filters());
  }

  function printUrl() {
    return A.apiUrl("/reports/print", { kind: state.kind, ...filters(), print: "1" });
  }

  function currentScopeLabel() {
    const period = els.period.options[els.period.selectedIndex]?.textContent || "كل الفترات";
    const cls = els.classFilter.options[els.classFilter.selectedIndex]?.textContent || "كل الأقسام";
    return `${period} · ${cls}`;
  }

  function renderSummary(summary) {
    els.statPresent.textContent = A.formatInt(summary.PRESENT || 0);
    els.statLate.textContent = A.formatInt(summary.LATE || 0);
    els.statAbsent.textContent = A.formatInt(summary.ABSENT || 0);
    els.statExcused.textContent = A.formatInt(summary.EXCUSED || 0);
  }

  function summaryFromReport(report) {
    if (report.kind === "monthly") {
      const totals = report.totals || {};
      return {
        PRESENT: totals.present || 0,
        LATE: totals.late || 0,
        ABSENT: totals.absent || 0,
        EXCUSED: totals.excused || 0,
      };
    }
    return report.summary || {};
  }

  function renderMeta(report) {
    const chips = [
      `<span class="chip">${A.icon("calendar")} ${safe(report.kind === "monthly" ? A.t(report.month_display) : report.date_display)}</span>`,
      `<span class="chip">${A.icon("clock")} ${safe(A.t(report.period_label))}</span>`,
      `<span class="chip">${A.icon("users")} ${safe(A.t(report.class_label))}</span>`,
    ];
    if (report.kind === "daily" && report.status_filter) {
      chips.push(`<span class="chip is-warning">${safe(STATUS_LABELS[report.status_filter] || report.status_filter)}</span>`);
    }
    els.tableMeta.innerHTML = chips.join("");
    els.generated.textContent = report.generated_at ? `تم التوليد ${report.generated_at}` : "—";
    els.scopeNote.textContent = currentScopeLabel();
  }

  function emptyRow(colspan) {
    return `<tr><td colspan="${colspan}" class="empty-cell">${A.emptyState(
      "chart",
      state.kind === "daily" ? "لا توجد سجلات لهذا اليوم" : "لا توجد بيانات لهذا الشهر",
      state.kind === "daily"
        ? "لم تُسجَّل أي حالة حضور أو غياب مطابقة للفلاتر. جرّب تغيير التاريخ أو الفترة."
        : "لم تُسجَّل أي حالة خلال هذا الشهر للفلاتر المحددة. جرّب تغيير الشهر أو القسم.",
      state.kind === "daily" ? '<a class="btn btn-secondary btn-sm" href="/attendance">فتح سجل الحضور</a>' : ""
    )}</td></tr>`;
  }

  function renderDaily(report) {
    els.tableTitle.textContent = "سجلات الحضور اليومية";
    els.tableHead.innerHTML = `<tr>
      <th style="width:1%">#</th>
      <th>التلميذ</th>
      <th>القسم</th>
      <th>الفترة</th>
      <th>وقت المسح</th>
      <th>الحالة</th>
      <th>رسالة SMS</th>
      <th>ملاحظة</th>
    </tr>`;
    const items = Array.isArray(report.items) ? report.items : [];
    if (!items.length) {
      els.tableBody.innerHTML = emptyRow(8);
      els.tableFoot.innerHTML = "";
    } else {
      els.tableBody.innerHTML = items
        .map(
          (item, index) => `<tr>
            <td class="tiny muted">${A.formatInt(index + 1)}</td>
            <td>
              <div class="cell-main">
                <div>
                  <strong>${safe(item.full_name || "بدون اسم")}</strong>
                  <small>${safe(item.student_nin)}</small>
                </div>
              </div>
            </td>
            <td><span class="chip">${safe(item.class_group)}</span></td>
            <td class="tiny">${safe(item.period_name)}</td>
            <td><span class="time-chip">${A.icon("clock")} ${safe(item.scan_time || "—")}</span></td>
            <td>${A.statusBadge(item.status, item.status_label)}</td>
            <td>${item.sms_status ? A.statusBadge(item.sms_status, SMS_LABELS[item.sms_status] || item.sms_status_label) : '<span class="muted tiny">—</span>'}${item.sms_is_test ? '<span class="chip is-accent" style="margin-top:3px">اختبار</span>' : ""}</td>
            <td><span class="message-preview" title="${safe(item.note)}">${safe(item.note || "—")}</span></td>
          </tr>`
        )
        .join("");
      const summary = report.summary || {};
      els.tableFoot.innerHTML = `<tr class="summary-row">
        <td></td>
        <td colspan="4">الإجمالي — ${A.formatInt(report.total)} سجل</td>
        <td>${A.formatInt(summary.PRESENT || 0)} حاضر · ${A.formatInt(summary.LATE || 0)} متأخر</td>
        <td>${A.formatInt(summary.ABSENT || 0)} غائب</td>
        <td>${A.formatInt(summary.EXCUSED || 0)} مبرر</td>
      </tr>`;
    }
    els.tableLabel.textContent = `${report.date_display} — ${A.t(report.period_label)} — ${A.t(report.class_label)}`;
    els.footNote.textContent = `${A.formatInt(items.length)} سجل مطابق — ${A.formatInt(new Set(items.map((item) => item.student_nin)).size)} تلميذًا — ${A.t(report.period_label)}`;
  }

  function absenceRate(item) {
    const total = Number(item.total || 0);
    if (!total) return 0;
    return Math.round((Number(item.absent || 0) / total) * 1000) / 10;
  }

  function renderMonthly(report) {
    els.tableTitle.textContent = "ملخص الغياب الشهري لكل تلميذ";
    els.tableHead.innerHTML = `<tr>
      <th style="width:1%">#</th>
      <th>التلميذ</th>
      <th>القسم</th>
      <th style="text-align:center">حاضر</th>
      <th style="text-align:center">متأخر</th>
      <th style="text-align:center">غائب</th>
      <th style="text-align:center">مبرر</th>
      <th style="text-align:center">السجلات</th>
      <th style="text-align:center">نسبة الغياب</th>
    </tr>`;
    const items = Array.isArray(report.items) ? report.items : [];
    if (!items.length) {
      els.tableBody.innerHTML = emptyRow(9);
      els.tableFoot.innerHTML = "";
    } else {
      els.tableBody.innerHTML = items
        .map((item, index) => {
          const rate = absenceRate(item);
          return `<tr>
            <td class="tiny muted">${A.formatInt(index + 1)}</td>
            <td>
              <div class="cell-main">
                <div>
                  <strong>${safe(item.full_name || "بدون اسم")}</strong>
                  <small>${safe(item.student_nin)}</small>
                </div>
              </div>
            </td>
            <td><span class="chip">${safe(item.class_group)}</span></td>
            <td style="text-align:center">${A.formatInt(item.present || 0)}</td>
            <td style="text-align:center">${A.formatInt(item.late || 0)}</td>
            <td style="text-align:center;color:${Number(item.absent) ? "var(--danger)" : "inherit"};font-weight:${Number(item.absent) ? "700" : "400"}">${A.formatInt(item.absent || 0)}</td>
            <td style="text-align:center">${A.formatInt(item.excused || 0)}</td>
            <td style="text-align:center">${A.formatInt(item.total || 0)}</td>
            <td style="text-align:center">
              <span class="badge ${rate >= 20 ? "badge--failed" : rate > 0 ? "badge--pending" : "badge--present"}">${rate.toFixed(rate % 1 ? 1 : 0)}%</span>
            </td>
          </tr>`;
        })
        .join("");
      const totals = report.totals || {};
      els.tableFoot.innerHTML = `<tr class="summary-row">
        <td></td>
        <td colspan="2">إجمالي ${A.formatInt(report.total_students)} تلميذًا</td>
        <td style="text-align:center">${A.formatInt(totals.present || 0)}</td>
        <td style="text-align:center">${A.formatInt(totals.late || 0)}</td>
        <td style="text-align:center">${A.formatInt(totals.absent || 0)}</td>
        <td style="text-align:center">${A.formatInt(totals.excused || 0)}</td>
        <td style="text-align:center">${A.formatInt(totals.total || 0)}</td>
        <td style="text-align:center">${A.formatInt(totals.absent || 0)} / ${A.formatInt(totals.total || 0)}</td>
      </tr>`;
    }
    els.tableLabel.textContent = `${A.t(report.month_display)} — ${A.t(report.period_label)} — ${A.t(report.class_label)}`;
    els.footNote.textContent = `${A.formatInt(report.total_students)} تلميذًا — ${A.formatInt((report.totals || {}).total || 0)} سجل خلال الفترة ${report.start} إلى ${report.end}`;
  }

  function render(report) {
    state.report = report;
    renderSummary(summaryFromReport(report));
    renderMeta(report);
    els.title.textContent = report.title || (report.kind === "monthly" ? "تقرير الغياب الشهري" : "تقرير الحضور اليومي");
    els.subtitle.textContent = `${report.school_name || ""}${report.academic_year ? ` · ${A.t("السنة الدراسية")} ${report.academic_year}` : ""} — ${A.t(report.period_label)} — ${A.t(report.class_label)}`;
    if (report.kind === "monthly") renderMonthly(report);
    else renderDaily(report);
    syncUrl();
    setOutputState(false);
  }

  function setOutputState(failed) {
    [els.export, els.exportHero, els.print, els.printHero].forEach((button) => {
      if (button) button.disabled = Boolean(failed);
    });
  }

  async function load() {
    const requestId = ++state.requestId;
    state.loading = true;
    applyControls();
    els.tableBody.innerHTML = '<tr><td colspan="9" class="empty-cell"><div class="loading-state"><span class="spinner"></span><span>جارٍ تحميل التقرير…</span></div></td></tr>';
    els.tableFoot.innerHTML = "";
    els.tableLabel.textContent = "جارٍ التحميل…";
    setOutputState(true);
    try {
      const report = await A.api(reportUrl());
      if (requestId !== state.requestId) return;
      render(report);
    } catch (error) {
      if (requestId !== state.requestId) return;
      els.tableBody.innerHTML = `<tr><td colspan="9" class="empty-cell">${A.emptyState("warning", "تعذر تحميل التقرير", error.message, '<button class="btn btn-secondary btn-sm" type="button" data-retry-report>إعادة المحاولة</button>')}</td></tr>`;
      els.tableLabel.textContent = "تعذر تحميل البيانات.";
      els.subtitle.textContent = "تحقق من الفلاتر أو أعد المحاولة.";
      els.tableFoot.innerHTML = "";
      els.footNote.textContent = "—";
      A.toast(error.message || "تعذر تحميل التقرير.", "error");
      setOutputState(true);
    } finally {
      if (requestId === state.requestId) {
        state.loading = false;
        A.setBusy(els.refresh, false);
      }
    }
  }

  function download() {
    A.download(reportUrl(".xlsx"));
    A.toast("جارٍ تجهيز ملف Excel…", "info");
  }

  function printReport() {
    const url = printUrl();
    const win = window.open(url, "_blank", "noopener");
    if (!win) {
      A.toast("منع المتصفح فتح نافذة الطباعة. اسمح بالنوافذ المنبثقة ثم أعد المحاولة.", "warning");
      return;
    }
    win.opener = null;
  }

  els.kindButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const kind = button.dataset.reportKind === "monthly" ? "monthly" : "daily";
      if (kind === state.kind) return;
      state.kind = kind;
      applyControls();
      load();
    });
  });

  els.date.addEventListener("change", () => {
    state.date = els.date.value && els.date.value <= today ? els.date.value : today;
    load();
  });

  els.month.addEventListener("change", () => {
    state.month = els.month.value && /^\d{4}-\d{2}$/.test(els.month.value) && els.month.value <= monthNow()
      ? els.month.value
      : monthNow();
    load();
  });

  els.period.addEventListener("change", () => {
    state.periodId = els.period.value;
    load();
  });
  els.classFilter.addEventListener("change", () => {
    state.classGroup = els.classFilter.value;
    load();
  });
  els.status.addEventListener("change", () => {
    state.status = els.status.value;
    load();
  });

  [els.refresh].forEach((button) => {
    if (button) {
      button.addEventListener("click", () => {
        A.setBusy(button, true);
        load();
      });
    }
  });
  [els.export, els.exportHero].forEach((button) => {
    if (button) button.addEventListener("click", download);
  });
  [els.print, els.printHero].forEach((button) => {
    if (button) button.addEventListener("click", printReport);
  });

  document.addEventListener("click", (event) => {
    const retry = event.target.closest("[data-retry-report]");
    if (retry) load();
  });

  readUrl();
  applyControls();
  load();
})();
