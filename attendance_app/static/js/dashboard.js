/* Dashboard live overview. */
(function () {
  "use strict";

  const A = window.App;
  const state = { date: A.todayISO(), periodId: null, loading: false, last: null };

  const els = {};
  function grab() {
    [
      "dash-date", "dash-refresh", "dash-greeting", "dash-date-label", "dash-period-pills",
      "stat-present", "stat-late", "stat-absent", "stat-excused",
      "dash-donut", "dash-donut-value", "legend-present", "legend-late", "legend-absent",
      "legend-excused", "legend-unscanned", "dash-closure-status", "scan-progress-label",
      "scan-progress-bar", "sms-progress-label", "sms-progress-bar", "mini-present",
      "mini-late", "mini-total", "class-breakdown", "recent-scans", "absentee-list",
      "absentee-count", "absentee-subtitle", "dash-open-close", "dash-sms-pending",
      "dash-sms-sent", "dash-sms-failed", "dash-sms-cancelled", "sms-badge-pending",
      "sms-badge-sent", "sms-badge-failed", "sms-bar-pending", "sms-bar-sent",
      "sms-bar-failed", "sms-alert-badge", "dash-sms-note",
    ].forEach((id) => { els[id] = document.getElementById(id); });
  }

  function localizedDate(iso) {
    const lang = (window.AppI18n && window.AppI18n.lang) || "ar";
    const locale = { ar: "ar-DZ-u-nu-latn", en: "en-GB", fr: "fr-FR" }[lang] || "ar-DZ-u-nu-latn";
    try {
      return new Intl.DateTimeFormat(locale, {
        weekday: "long", day: "numeric", month: "long", year: "numeric",
      }).format(new Date(`${iso}T12:00:00`));
    } catch (error) {
      return A.formatDate(iso);
    }
  }

  function setText(id, value) {
    const node = els[id] || document.getElementById(id);
    if (node) node.textContent = value;
  }

  function renderPeriods(data) {
    const container = els["dash-period-pills"];
    if (!container) return;
    const selectedId = data.period ? data.period.id : null;
    const isAuto = state.periodId === null;
    container.innerHTML = `<button class="period-pill ${isAuto ? "is-active" : ""}" type="button" data-period="auto">
        <svg class="ico" style="width:12px;height:12px"><use href="#i-sparkle"></use></svg> تلقائي
      </button>` +
      (data.periods || [])
        .map((period) => `<button class="period-pill ${!isAuto && selectedId === period.id ? "is-active" : ""}" type="button" data-period="${period.id}" title="${A.escapeHtml(period.period_name)}">
          ${A.escapeHtml(period.period_name)} <time>${A.escapeHtml(period.start_time)}</time>
        </button>`)
        .join("");
    container.querySelectorAll("[data-period]").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.periodId = btn.dataset.period === "auto" ? null : Number(btn.dataset.period);
        load();
      });
    });
  }

  function renderStats(data) {
    const stats = data.stats || {};
    setText("stat-present", A.formatInt(stats.present));
    setText("stat-late", A.formatInt(stats.late));
    setText("stat-absent", A.formatInt(stats.absent));
    setText("stat-excused", A.formatInt(stats.excused));
    setText("legend-present", A.formatInt(stats.present));
    setText("legend-late", A.formatInt(stats.late));
    setText("legend-absent", A.formatInt(stats.absent));
    setText("legend-excused", A.formatInt(stats.excused));
    setText("legend-unscanned", A.formatInt(stats.unscanned));
    setText("mini-present", A.formatInt(stats.present));
    setText("mini-late", A.formatInt(stats.late));
    setText("mini-total", A.formatInt(stats.total));

    const rate = Math.max(0, Math.min(100, Number(stats.attendance_rate || 0)));
    if (els["dash-donut"]) els["dash-donut"].style.setProperty("--value", rate);
    setText("dash-donut-value", `${rate}%`);

    const total = Number(stats.total || 0);
    const logged = Number(stats.logged || 0);
    const scanPercent = total ? Math.round((logged / total) * 100) : 0;
    setText("scan-progress-label", `${A.formatInt(logged)} / ${A.formatInt(total)} تلميذ`);
    if (els["scan-progress-bar"]) els["scan-progress-bar"].style.width = `${scanPercent}%`;

    const closure = data.closure;
    if (els["dash-closure-status"]) {
      els["dash-closure-status"].className = `badge ${closure ? "badge--warning" : "badge--present"}`;
      els["dash-closure-status"].innerHTML = closure
        ? `<i class="badge-dot"></i> مغلقة ${closure.closed_at ? `· ${A.escapeHtml(String(closure.closed_at).slice(11, 16))}` : ""}`
        : `<i class="badge-dot"></i> مفتوحة`;
    }

    const sms = data.sms || {};
    const smsTotal = Number(sms.total || 0);
    const pending = Number(sms.PENDING || 0);
    setText("dash-sms-pending", A.formatInt(pending));
    setText("dash-sms-sent", A.formatInt(sms.SENT));
    setText("dash-sms-failed", A.formatInt(sms.FAILED));
    setText("dash-sms-cancelled", A.formatInt(sms.CANCELLED));
    setText("sms-badge-pending", A.formatInt(pending));
    setText("sms-badge-sent", A.formatInt(sms.SENT));
    setText("sms-badge-failed", A.formatInt(sms.FAILED));
    if (els["sms-progress-label"]) {
      els["sms-progress-label"].textContent = smsTotal
        ? `${A.formatInt(sms.SENT)} / ${A.formatInt(smsTotal)} مُرسلة`
        : "لا رسائل";
    }
    if (els["sms-progress-bar"]) els["sms-progress-bar"].style.width = smsTotal ? `${Math.round((Number(sms.SENT || 0) / smsTotal) * 100)}%` : "0%";
    const pct = (value) => (smsTotal ? `${Math.round((Number(value || 0) / smsTotal) * 100)}%` : "0%");
    if (els["sms-bar-pending"]) els["sms-bar-pending"].style.width = pct(sms.PENDING);
    if (els["sms-bar-sent"]) els["sms-bar-sent"].style.width = pct(sms.SENT);
    if (els["sms-bar-failed"]) els["sms-bar-failed"].style.width = pct(sms.FAILED);
    if (els["sms-alert-badge"]) {
      els["sms-alert-badge"].textContent = pending ? `${A.formatInt(pending)} معلقة` : "SMS";
      els["sms-alert-badge"].style.color = pending ? "var(--warning)" : "";
    }
  }

  function renderClasses(data) {
    const container = els["class-breakdown"];
    if (!container) return;
    const classes = data.classes || [];
    if (!classes.length) {
      container.innerHTML = A.emptyState("users", "لا توجد أقسام بعد", "أضف التلاميذ أو استورد قائمة لحساب الإحصاءات حسب القسم.");
      return;
    }
    container.innerHTML = `<div class="bar-chart">${classes
      .map((item) => {
        const rate = Math.max(0, Math.min(100, Number(item.attendance_rate || 0)));
        return `<div class="bar-row" title="حاضر ${item.present} · متأخر ${item.late} · غائب ${item.absent}">
          <span class="bar-label">${A.escapeHtml(item.class_group)}</span>
          <div class="bar-track"><span class="bar-fill" style="width:${rate}%"></span></div>
          <span class="bar-value">${rate}% <span class="muted">· ${A.formatInt(item.total)}</span></span>
        </div>`;
      })
      .join("")}</div>`;
  }

  function renderRecent(data) {
    const container = els["recent-scans"];
    if (!container) return;
    const recent = data.recent || [];
    if (!recent.length) {
      container.innerHTML = A.emptyState(
        "scan",
        "لا عمليات مسح بعد",
        "افتح بوابة المسح ومرر بطاقة تلميذ لتظهر أحدث العمليات هنا مباشرة.",
        `<a class="btn btn-secondary btn-sm" href="/gate">${A.icon("scan")} فتح البوابة</a>`
      );
      return;
    }
    container.innerHTML = `<div class="activity-list">${recent
      .map(
        (item) => `<div class="activity-row">
          ${A.avatar(item, "avatar-sm")}
          <div class="activity-main">
            <strong>${A.escapeHtml(item.full_name)}</strong>
            <span>${A.escapeHtml(item.class_group)} · ${A.escapeHtml(item.student_nin)}</span>
          </div>
          <div class="activity-side">
            ${A.statusBadge(item.status, item.status_label)}
            <time>${A.escapeHtml(item.scan_time || "—")}</time>
          </div>
        </div>`
      )
      .join("")}</div>`;
  }

  function renderAbsentees(data) {
    const container = els["absentee-list"];
    if (!container) return;
    const absentees = data.absentees || [];
    const absentTotal = Number((data.stats || {}).absent || 0);
    setText("absentee-count", A.formatInt(absentTotal));
    if (!absentees.length) {
      const closed = Boolean(data.closure);
      container.innerHTML = A.emptyState(
        "shield-check",
        closed ? "لا غياب مسجل في هذه الفترة" : "لم تُغلق الفترة بعد",
        closed ? "كل التلاميذ النشطين لديهم سجلات حضور أو تبرير." : "عند إغلاق الفترة سيُسجَّل التلاميذ غير الممسوحين كغائبين، ويمكنك إشعار الأولياء بعد الموافقة.",
        !closed ? `<button class="btn btn-warning btn-sm" type="button" id="dash-empty-close">${A.icon("message")} إغلاق الفترة</button>` : ""
      );
      const btn = document.getElementById("dash-empty-close");
      if (btn) btn.addEventListener("click", openWorkflow);
      setText("absentee-subtitle", closed ? "كل الحالات معالجة" : "الفترة لم تُغلق بعد");
      return;
    }
    container.innerHTML = `<div class="absentee-list">${absentees
      .map(
        (item) => `<div class="absentee-row">
          ${A.avatar({ full_name: item.full_name, first_name: "", last_name: "" }, "avatar-sm")}
          <div class="absentee-main">
            <strong>${A.escapeHtml(item.full_name)}</strong>
            <span>${A.escapeHtml(item.class_group)} · ${A.escapeHtml(item.parent_phone || "بدون هاتف")}</span>
          </div>
          <div class="absentee-right">
            ${item.sms_status ? A.statusBadge(item.sms_status) : `<span class="badge badge--absent">غائب</span>`}
            <button class="icon-btn js-excuse" type="button" data-log="${item.log_id}" data-name="${A.escapeHtml(item.full_name)}" title="تبرير الغياب" aria-label="تبرير غياب ${A.escapeHtml(item.full_name)}">${A.icon("shield-check")}</button>
          </div>
        </div>`
      )
      .join("")}</div>`;
    setText("absentee-subtitle", absentTotal > absentees.length ? `عرض ${absentees.length} من ${A.formatInt(absentTotal)} حالة` : "كل حالات الغياب");
    container.querySelectorAll(".js-excuse").forEach((btn) => {
      btn.addEventListener("click", () => {
        const logId = btn.dataset.log;
        const name = btn.dataset.name;
        window.AbsenceWorkflow.openExcuseModal(logId, name, () => load());
      });
    });
  }

  function openWorkflow(event) {
    if (!state.last || !state.last.period) {
      A.toast("لا توجد فترة نشطة. أضف فترة من الإعدادات أولًا.", "warning");
      return;
    }
    window.AbsenceWorkflow.openCloseModal({
      periodId: state.last.period.id,
      date: state.date,
      trigger: event && event.currentTarget,
      onDone: () => load(),
    });
  }

  function render(data, silent) {
    state.last = data;
    if (data.period) state.periodId = data.period.id;
    const hour = new Date().getHours();
    const greeting = hour < 12 ? "صباح الخير — إليك ملخص الحضور" : hour < 17 ? "نهارك سعيد — إليك ملخص الحضور" : "مساء الخير — إليك ملخص الحضور";
    setText("dash-greeting", greeting);
    setText("dash-date-label", localizedDate(state.date));
    if (els["dash-date"]) els["dash-date"].value = state.date;
    renderPeriods(data);
    renderStats(data);
    renderClasses(data);
    renderRecent(data);
    renderAbsentees(data);
    if (!silent) {
      const pending = Number((data.sms || {}).PENDING || 0);
      if (pending) A.toast(`توجد ${A.formatInt(pending)} رسالة SMS معلقة لهذه الفترة.`, "info", "رسائل قيد الانتظار");
    }
  }

  async function load(silent = false) {
    if (state.loading) return;
    state.loading = true;
    const refresh = els["dash-refresh"];
    if (refresh && !silent) A.setBusy(refresh, true);
    try {
      const data = await A.api(A.apiUrl("/api/dashboard", { date: state.date, period_id: state.periodId }));
      render(data, silent);
    } catch (error) {
      if (!silent) A.toast(error.message, "error");
      if (!state.last) {
        ["class-breakdown", "recent-scans", "absentee-list"].forEach((id) => {
          const node = document.getElementById(id);
          if (node) node.innerHTML = A.emptyState("warning", "تعذر تحميل البيانات", error.message);
        });
      }
    } finally {
      state.loading = false;
      if (refresh) A.setBusy(refresh, false);
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    grab();
    if (!els["dash-date"]) return;
    els["dash-date"].addEventListener("change", () => {
      const value = els["dash-date"].value;
      if (!value) return;
      if (value > A.todayISO()) {
        A.toast("لا يمكن عرض إحصاءات تاريخ مستقبلي.", "warning");
        els["dash-date"].value = state.date;
        return;
      }
      state.date = value;
      load();
    });
    els["dash-refresh"]?.addEventListener("click", () => load());
    els["dash-open-close"]?.addEventListener("click", openWorkflow);
    els["dash-sms-note"]?.addEventListener("click", () => {
      const pending = Number((state.last && state.last.sms && state.last.sms.PENDING) || 0);
      if (pending) openWorkflow();
    });
    load(true);
    let timer = null;
    const schedule = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(async () => {
        if (document.visibilityState === "visible" && !document.querySelector(".modal-backdrop")) {
          await load(true);
        }
        schedule();
      }, 45000);
    };
    schedule();
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") load(true);
    });
  });
})();
