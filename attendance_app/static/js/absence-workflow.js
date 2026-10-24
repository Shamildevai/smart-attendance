/* Absence closure + parent SMS workflow shared by dashboard and attendance. */
(function () {
  "use strict";

  const A = window.App;

  function line(label, value) {
    return `<div class="flex between" style="padding:5px 0;border-bottom:1px solid var(--border)">
      <span class="tiny muted">${A.escapeHtml(label)}</span><strong style="font-size:11.5px">${A.escapeHtml(value)}</strong></div>`;
  }

  function renderPreview(preview) {
    const closed = Boolean(preview.closed);
    const summary = preview.sms_summary || {};
    const pending = Number(summary.PENDING || 0);
    const absentCount = closed ? Number(preview.existing_absences || 0) : Number(preview.missing_count || 0);
    const phoneCount = closed ? Number(preview.absent_phone_count || 0) : Number(preview.with_phone || 0);
    const noPhone = Number(preview.without_phone || 0);
    const sample = preview.sample || [];
    const classes = preview.classes || [];
    const canText = phoneCount > 0 && (!closed || pending > 0);

    const classTags = classes.length
      ? `<div class="tag-list" style="margin-top:6px">${classes
          .slice(0, 10)
          .map((item) => `<span class="chip">${A.escapeHtml(item.class_group)} · ${A.formatInt(item.count)}</span>`)
          .join("")}</div>`
      : "";

    const sampleRows = sample.length
      ? `<div class="table-wrap" style="border:1px solid var(--border);border-radius:10px;margin-top:6px">
          <table class="preview-table">
            <thead><tr><th>التلميذ</th><th>القسم</th><th>هاتف الولي</th></tr></thead>
            <tbody>${sample
              .map(
                (student) => `<tr><td>${A.escapeHtml(student.full_name)}</td><td>${A.escapeHtml(student.class_group)}</td><td class="mono" style="font-size:9.5px">${A.escapeHtml(student.parent_phone || "—")}</td></tr>`
              )
              .join("")}</tbody>
          </table>
        </div>
        ${!closed && absentCount > sample.length ? `<p class="tiny muted" style="margin-top:4px">و${A.formatInt(absentCount - sample.length)} حالة أخرى…</p>` : ""}`
      : "";

    const stateTitle = closed
      ? `الفترة مغلقة — ${preview.closure && preview.closure.closed_by === "AUTO" ? "إغلاق آلي" : "إغلاق يدوي"}`
      : "الفترة مفتوحة ولم تُغلق بعد";
    const stateText = closed
      ? `تم تثبيت سجلات الغياب. ${pending > 0 ? `توجد ${A.formatInt(pending)} رسالة في انتظار الإرسال.` : "لا توجد رسائل SMS معلقة لهذه الفترة."}`
      : `عند الإغلاق سيُسجَّل ${A.formatInt(absentCount)} تلميذًا غير ممسوح بحالة «غائب». يمكنك إرسال الرسائل بعد الموافقة.`;

    return {
      html: `
        <div class="info-banner ${closed ? "is-warning" : "is-success"}">
          ${A.icon(closed ? "shield-check" : "clock")}
          <div><strong>${A.escapeHtml(stateTitle)}.</strong> ${A.escapeHtml(stateText)}</div>
        </div>
        <div class="close-summary" style="margin-top:14px">
          <div><strong>${A.formatInt(absentCount)}</strong><span>${closed ? "غائب مسجل" : "غائب متوقع"}</span></div>
          <div><strong>${A.formatInt(phoneCount)}</strong><span>ولي له هاتف</span></div>
          <div><strong style="color:${noPhone ? "var(--danger)" : "var(--text)"}">${A.formatInt(noPhone)}</strong><span>بدون هاتف</span></div>
        </div>
        ${classTags}
        ${noPhone > 0 ? `<div class="info-banner is-warning" style="margin-top:12px">${A.icon("warning")}<div><strong>${A.formatInt(noPhone)}</strong> تلميذًا غائبًا بدون رقم هاتف صالح. لن تصلهم رسائل SMS، ويمكن متابعتهم عبر التقرير اليومي.</div></div>` : ""}
        ${canText ? `<div class="approval-box" style="margin-top:12px">
          <input type="checkbox" id="wf-approve" autocomplete="off">
          <label for="wf-approve"><strong>موافقة صريحة على إرسال رسائل SMS</strong>
          ${closed
            ? `أوافق على إرسال ${A.formatInt(pending)} رسالة معلّقة إلى أولياء التلاميذ الغائبين الآن.`
            : `أوافق على إرسال رسائل SMS إلى أولياء التلاميذ الغائبين بعد إغلاق الفترة.`}
          </label>
        </div>` : ""}
        ${preview.test_mode ? `<div class="test-mode-note" style="margin-top:10px">${A.icon("shield-check")}<div>وضع الاختبار مفعّل: ستُسجَّل الرسائل كمحاكاة ناجحة دون إرسال حقيقي أو استهلاك رصيد. يمكن تعطيله من الإعدادات.</div></div>` : ""}
        ${sampleRows ? `<div style="margin-top:14px"><div class="tiny muted" style="font-weight:650">عينة من التلاميذ المعنيين</div>${sampleRows}</div>` : ""}
        <div style="margin-top:14px">
          ${line("الفترة", preview.period ? A.t(preview.period.period_name) : "—")}
          ${line("التاريخ", A.formatDate(preview.date))}
          ${closed ? line("وقت الإغلاق", preview.closure ? preview.closure.closed_at : "—") : ""}
          ${closed ? line("رسائل مُرسلة سابقًا", `${A.formatInt(summary.SENT || 0)}${summary.test_mode ? "" : ""}`) : ""}
        </div>`,
      closed,
      pending,
      canText,
      absentCount,
      phoneCount,
    };
  }

  async function watchJob(jobId, onUpdate, onDone) {
    let stopped = false;
    let failures = 0;
    const tick = async () => {
      if (stopped) return;
      try {
        const data = await A.api(`/api/sms/jobs/${encodeURIComponent(jobId)}`);
        failures = 0;
        const job = data.job;
        if (onUpdate) onUpdate(job);
        if (job.status === "done") {
          if (onDone) onDone(job);
          return;
        }
      } catch (error) {
        failures += 1;
        if (failures >= 6) {
          A.toast("تعذر متابعة تقدم الإرسال. حدّث الصفحة لعرض النتيجة.", "warning");
          return;
        }
      }
      window.setTimeout(tick, 900);
    };
    tick();
    return { stop() { stopped = true; } };
  }

  function jobMarkup(job) {
    const total = Number(job.total || 0);
    const processed = Number(job.processed || 0);
    const percent = total ? Math.round((processed / total) * 100) : 0;
    const done = job.status === "done";
    return `<div class="sms-progress ${done ? "" : "is-active"}" style="margin:14px 0 0">
      ${done ? `<div class="stats-icon" style="display:grid;place-items:center;width:30px;height:30px;border-radius:9px;background:var(--success-soft);color:var(--success)">${A.icon("check")}</div>` : `<span class="spinner"></span>`}
      <div class="sms-progress-text">
        <strong>${done ? "اكتملت معالجة الرسائل" : "جارٍ إرسال الرسائل إلى الأولياء"}</strong>
        <span>${A.escapeHtml(job.message || (job.current_name ? `جارٍ معالجة: ${job.current_name}` : "جارٍ التحضير…"))}</span>
        ${done ? "" : `<div class="sms-progress-bar"><span id="wf-job-bar" style="width:${percent}%"></span></div>`}
      </div>
      <div class="sms-progress-meta">${A.formatInt(job.sent || 0)} ناجحة · ${A.formatInt(job.failed || 0)} فاشلة${job.simulated ? ` · ${A.formatInt(job.simulated)} اختبار` : ""}${job.error ? ` · ${A.escapeHtml(job.error)}` : ""}</div>
    </div>`;
  }

  async function openCloseModal(options = {}) {
    if (!options.periodId) {
      A.toast("اختر فترة نشطة أولًا لمعالجة الغياب.", "warning");
      return;
    }
    if (options.trigger) A.setBusy(options.trigger, true);
    let preview;
    try {
      preview = await A.api(
        A.apiUrl("/api/attendance/absence-preview", {
          period_id: options.periodId,
          date: options.date,
        })
      );
    } catch (error) {
      A.toast(error.message, "error");
      return;
    } finally {
      if (options.trigger) A.setBusy(options.trigger, false);
    }

    const view = renderPreview(preview);
    let closed = view.closed;
    let submitting = false;

    const footer = closed
      ? `<button class="btn btn-secondary" type="button" data-modal-close>إغلاق</button>
         ${view.pending > 0 && view.canText ? `<button class="btn btn-warning" type="button" id="wf-submit" disabled>${A.icon("send")} إرسال الرسائل المعلقة</button>` : ""}`
      : `<button class="btn btn-secondary" type="button" data-modal-close>إلغاء</button>
         <button class="btn btn-warning" type="button" id="wf-submit">${A.icon("check")} إغلاق الفترة</button>`;

    const modal = A.openModal({
      title: options.closed ? "متابعة غياب الفترة" : "إغلاق الفترة ومعالجة الغياب",
      subtitle: `${preview.period ? A.t(preview.period.period_name) : ""} — ${A.formatDate(preview.date)}`,
      size: "lg",
      icon: "message",
      body: `<div id="wf-view">${view.html}</div><div id="wf-result"></div>`,
      footer,
    });

    const body = modal.element;
    const approve = body.querySelector("#wf-approve");
    const submit = body.querySelector("#wf-submit");
    const resultBox = body.querySelector("#wf-result");
    const viewBox = body.querySelector("#wf-view");

    if (approve && submit) {
      approve.addEventListener("change", () => {
        if (!closed) {
          submit.innerHTML = approve.checked
            ? `${A.icon("send")} إغلاق وإرسال SMS`
            : `${A.icon("check")} إغلاق الفترة`;
        } else {
          submit.disabled = !approve.checked;
        }
      });
      if (closed) submit.disabled = true;
    }

    if (submit) {
      submit.addEventListener("click", async () => {
        if (submitting) return;
        const approved = Boolean(approve && approve.checked);
        if (closed && !approved) return;
        submitting = true;
        A.setBusy(submit, true);
        try {
          let data;
          if (closed) {
            data = await A.api("/api/attendance/send-absences", {
              method: "POST",
              body: { period_id: options.periodId, date: options.date, approved: true },
            });
          } else {
            data = await A.api("/api/attendance/close", {
              method: "POST",
              body: {
                period_id: options.periodId,
                date: options.date,
                send_sms: approved,
                approved,
              },
            });
          }
          closed = true;
          const close = data.close;
          const smsError = data.sms_error;
          const queue = data.queue;
          let html = `<div class="divider"></div><div class="info-banner is-success">${A.icon("check")}<div><strong>تمت العملية بنجاح.</strong> ${close ? `تم رصد ${A.formatInt(close.inserted || 0)} غائبًا جديدًا.` : ""} ${queue ? `أُنشئت ${A.formatInt(queue.created || 0)} رسالة جديدة.` : ""}</div></div>`;
          if (queue && (queue.skipped_no_phone || queue.skipped_invalid_phone)) {
            html += `<div class="info-banner is-warning" style="margin-top:8px">${A.icon("warning")}<div>${A.formatInt(queue.skipped_no_phone)} بدون رقم هاتف، و${A.formatInt(queue.skipped_invalid_phone)} برقم غير صالح — لم تُنشأ لهم رسائل.</div></div>`;
          }
          if (smsError) {
            html += `<div class="info-banner is-danger" style="margin-top:8px">${A.icon("warning")}<div><strong>تم إغلاق الفترة، لكن تعذر تجهيز الإرسال:</strong> ${A.escapeHtml(smsError)}</div></div>`;
          }
          if (data.test_mode && queue && queue.created) {
            html += `<div class="test-mode-note" style="margin-top:8px">${A.icon("shield-check")}<div>وضع الاختبار: سيتم تسجيل الإرسال كمحاكاة ناجحة دون شبكة.</div></div>`;
          }
          resultBox.innerHTML = html;
          if (viewBox) viewBox.style.display = "none";
          const footerEl = body.querySelector(".modal-footer");
          if (footerEl) footerEl.innerHTML = `<button class="btn btn-primary" type="button" data-modal-close>تم</button>`;
          footerEl.querySelector("[data-modal-close]").addEventListener("click", () => A.closeModal());
          if (data.job) {
            resultBox.insertAdjacentHTML("beforeend", jobMarkup(data.job));
            watchJob(
              data.job.id,
              (job) => {
                resultBox.querySelector(".sms-progress")?.replaceWith(
                  Object.assign(document.createElement("div"), { innerHTML: jobMarkup(job) }).firstElementChild
                );
              },
              (job) => {
                A.toast(job.message || "اكتملت معالجة الرسائل.", job.failed ? "warning" : "success");
                if (options.onDone) options.onDone(job);
              }
            );
          }
          if (options.onDone) options.onDone(data);
        } catch (error) {
          A.toast(error.message, "error");
        } finally {
          submitting = false;
          if (submit.isConnected) A.setBusy(submit, false);
        }
      });
    }
  }

  async function openExcuseModal(logId, studentName, onDone) {
    if (!logId) return;
    const modal = A.openModal({
      title: "تبرير الغياب",
      subtitle: studentName || "",
      body: `
        <div class="info-banner is-warning">${A.icon("shield-check")}<div>يعتبر التبرير الغياب «مبررًا» ويمنع إرسال رسالة SMS إن لم تُرسل بعد. لا يمكن تبرير سجل حضور أو تأخر.</div></div>
        <div class="field" style="margin-top:14px">
          <label for="excuse-reason">سبب التبرير <span class="required">*</span></label>
          <textarea class="textarea" id="excuse-reason" maxlength="500" placeholder="مثال: شهادة طبية مسلمة للإدارة بتاريخ اليوم…" autofocus></textarea>
          <span class="field-hint">سيُسجل السبب في ملاحظات السجل ويظهر في التقارير.</span>
        </div>`,
      icon: "shield-check",
      footer: `<button class="btn btn-secondary" type="button" data-modal-close>إلغاء</button>
               <button class="btn btn-primary" type="button" id="excuse-submit">${A.icon("check")} تأكيد التبرير</button>`,
    });
    const button = modal.element.querySelector("#excuse-submit");
    const reason = modal.element.querySelector("#excuse-reason");
    const submit = async () => {
      const value = (reason.value || "").trim();
      if (value.length < 2) {
        A.toast("يرجى كتابة سبب التبرير (حرفان على الأقل).", "warning");
        reason.focus();
        return;
      }
      A.setBusy(button, true);
      try {
        const data = await A.api(`/api/attendance/${encodeURIComponent(logId)}/excuse`, {
          method: "POST",
          body: { reason: value },
        });
        A.toast(data.message || "تم تبرير الغياب.", data.warning ? "warning" : "success");
        A.closeModal();
        if (onDone) onDone(data);
      } catch (error) {
        A.toast(error.message, "error");
      } finally {
        A.setBusy(button, false);
      }
    };
    button.addEventListener("click", submit);
    reason.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && event.ctrlKey) submit();
    });
  }

  window.AbsenceWorkflow = { openCloseModal, watchJob, openExcuseModal };
})();
