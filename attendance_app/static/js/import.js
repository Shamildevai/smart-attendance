/* Import wizard: parse a spreadsheet, map columns, preview validation, commit. */
(function () {
  "use strict";

  const A = window.App;
  const escape = A.escapeHtml;
  const $ = (id) => document.getElementById(id);

  const els = {
    error: $("import-error"),
    errorText: $("import-error-text"),
    upload: $("step-upload"),
    dropzone: $("import-dropzone"),
    file: $("import-file"),
    uploading: $("import-uploading"),
    uploadingName: $("import-uploading-name"),
    mapping: $("step-mapping"),
    fileHead: $("import-file-head"),
    mappingGrid: $("import-mapping-grid"),
    mappingCount: $("import-mapping-count"),
    mappingStatus: $("import-mapping-status"),
    rawTable: $("import-raw-table"),
    rawHint: $("import-raw-hint"),
    changeFile: $("import-change-file"),
    analyze: $("import-analyze"),
    review: $("step-review"),
    summary: $("import-summary"),
    reviewChips: $("import-review-chips"),
    reviewNotes: $("import-review-notes"),
    reviewTable: $("import-review-table"),
    reviewCaption: $("import-review-caption"),
    reviewBadge: $("import-review-badge"),
    issueTabs: $("import-issue-tabs"),
    issueList: $("import-issue-list"),
    issueMore: $("import-issue-more"),
    backMapping: $("import-back-mapping"),
    cancel: $("import-cancel"),
    commit: $("import-commit"),
    commitHint: $("import-commit-hint"),
    done: $("step-done"),
    doneMessage: $("import-done-message"),
    doneStats: $("import-done-stats"),
    doneNotes: $("import-done-notes"),
    again: $("import-again"),
    steps: $("import-steps"),
  };

  const state = {
    file: null,
    parsed: null,
    mapping: {},
    preview: null,
    issueTab: "errors",
    busy: false,
  };

  const FIELD_ICONS = {
    student_nin: "id-card",
    last_name: "user",
    first_name: "user",
    birth_date: "calendar",
    class_group: "users",
    parent_phone: "phone",
    photo_path: "camera",
  };

  function show(el) {
    if (el) el.classList.remove("hidden");
  }

  function hide(el) {
    if (el) el.classList.add("hidden");
  }

  function clearError() {
    hide(els.error);
    els.errorText.textContent = "";
  }

  function showError(message) {
    els.errorText.textContent = message || "حدث خطأ غير متوقع. أعد المحاولة.";
    show(els.error);
    if (els.error.scrollIntoView) {
      els.error.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }

  function columnLabel(column) {
    return column.label || `عمود ${column.index + 1}`;
  }

  function mappedPayload() {
    const mapping = {};
    Object.entries(state.mapping).forEach(([field, value]) => {
      if (value !== "" && value !== null && value !== undefined) {
        mapping[field] = Number(value);
      }
    });
    return mapping;
  }

  function setStep(step) {
    const sections = [
      [1, els.upload],
      [2, els.mapping],
      [3, els.review],
      [4, els.done],
    ];
    sections.forEach(([number, section]) => {
      section.classList.toggle("hidden", number !== step);
    });
    els.steps.querySelectorAll(".import-step").forEach((item) => {
      const number = Number(item.dataset.step);
      item.classList.toggle("is-active", number === step);
      item.classList.toggle("is-done", number < step);
      const badge = item.querySelector(".step-number");
      if (badge) {
        badge.innerHTML = number < step ? A.icon("check") : String(number);
      }
    });
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  /* ---------- Step 1: upload ---------- */

  async function handleFile(file) {
    if (!file) return;
    clearError();
    if (file.size > 25 * 1024 * 1024) {
      showError("حجم الملف يتجاوز 25 ميغابايت. قسّم القائمة إلى ملفات أصغر.");
      return;
    }
    state.file = file;
    state.parsed = null;
    state.preview = null;
    state.mapping = {};
    els.uploadingName.textContent = `${file.name} · ${(file.size / 1024).toFixed(0)} كيلوبايت`;
    hide(els.dropzone);
    show(els.uploading);
    hide(els.error);

    const form = new FormData();
    form.append("file", file);
    try {
      const data = await A.api("/api/import/parse", { method: "POST", form });
      state.parsed = data;
      Object.entries(data.suggested_mapping || {}).forEach(([field, index]) => {
        if (index !== null && index !== undefined && index !== "") {
          state.mapping[field] = String(index);
        }
      });
      renderFileHead();
      renderMapping();
      renderRawPreview();
      setStep(2);
      A.toast(
        `تم التعرف على ${A.formatInt(data.total_rows)} صفًا و${A.formatInt(data.columns.length)} عمودًا.`,
        "success",
        "الملف جاهز للمطابقة"
      );
    } catch (error) {
      showError(error.message);
    } finally {
      hide(els.uploading);
      show(els.dropzone);
      els.file.value = "";
    }
  }

  els.file.addEventListener("change", () => {
    const file = els.file.files && els.file.files[0];
    if (file) handleFile(file);
  });

  ["dragenter", "dragover"].forEach((eventName) => {
    els.dropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      els.dropzone.classList.add("is-dragover");
    });
  });
  ["dragleave", "dragend", "drop"].forEach((eventName) => {
    els.dropzone.addEventListener(eventName, () => els.dropzone.classList.remove("is-dragover"));
  });
  els.dropzone.addEventListener("drop", (event) => {
    event.preventDefault();
    const file = event.dataTransfer && event.dataTransfer.files ? event.dataTransfer.files[0] : null;
    if (file) handleFile(file);
  });

  /* ---------- Step 2: mapping ---------- */

  function renderFileHead() {
    const parsed = state.parsed;
    const extension = (parsed.file_name.split(".").pop() || "").toUpperCase();
    els.fileHead.innerHTML = `
      <div class="file-icon">${A.icon("file")}</div>
      <div>
        <strong title="${escape(parsed.file_name)}">${escape(parsed.file_name)}</strong>
        <span>${escape(parsed.file_type)} · ${A.formatInt(parsed.total_rows)} صفًا · العناوين في السطر ${A.formatInt(parsed.header_row)}</span>
      </div>
      ${extension ? `<span class="badge badge--primary" dir="ltr">${escape(extension)}</span>` : ""}`;
  }

  function renderMapping() {
    const parsed = state.parsed;
    els.mappingGrid.innerHTML = parsed.fields
      .map((field) => {
        const options = parsed.columns
          .map((column) => {
            const selected = state.mapping[field.key] === String(column.index);
            return `<option value="${column.index}" ${selected ? "selected" : ""}>${escape(columnLabel(column))}</option>`;
          })
          .join("");
        return `
          <div class="mapping-row ${field.required ? "is-required" : ""}">
            <span class="field-ico">${A.icon(FIELD_ICONS[field.key] || "file")}</span>
            <div class="mapping-label">
              <strong>${escape(field.label)}${field.required ? ' <span class="required">*</span>' : ""}</strong>
              <span>${escape(field.hint)}</span>
            </div>
            <select class="select" data-map-field="${escape(field.key)}" aria-label="عمود ${escape(field.label)}">
              <option value="">— غير مربوط —</option>
              ${options}
            </select>
          </div>`;
      })
      .join("");

    els.mappingGrid.querySelectorAll("select[data-map-field]").forEach((select) => {
      select.addEventListener("change", () => {
        const field = select.dataset.mapField;
        if (select.value === "") {
          delete state.mapping[field];
        } else {
          state.mapping[field] = select.value;
        }
        syncMappingSelects();
        updateMappingState();
        updateRawHighlights();
      });
    });

    syncMappingSelects();
    updateMappingState();
  }

  function syncMappingSelects() {
    const used = {};
    Object.entries(state.mapping).forEach(([field, value]) => {
      if (value !== "" && value !== null && value !== undefined) used[String(value)] = field;
    });
    els.mappingGrid.querySelectorAll("select[data-map-field]").forEach((select) => {
      const field = select.dataset.mapField;
      Array.from(select.options).forEach((option) => {
        if (!option.value) return;
        const owner = used[option.value];
        option.disabled = owner !== undefined && owner !== field;
      });
      const row = select.closest(".mapping-row");
      if (row) row.classList.toggle("is-mapped", Boolean(select.value));
    });
  }

  function updateMappingState() {
    const total = state.parsed.fields.length;
    const mapped = Object.values(state.mapping).filter((value) => value !== "" && value !== null && value !== undefined).length;
    const hasNin = Boolean(state.mapping.student_nin);
    els.mappingCount.textContent = `${A.formatInt(mapped)} / ${A.formatInt(total)}`;
    els.mappingStatus.classList.toggle("is-ok", hasNin);
    els.mappingStatus.innerHTML = hasNin
      ? `${A.icon("check")} رقم التعريف الوطني مربوط. يمكنك متابعة التحليل.`
      : `${A.icon("warning")} اربط عمود رقم التعريف الوطني للمتابعة.`;
    els.analyze.disabled = !hasNin;
  }

  function renderRawPreview() {
    const parsed = state.parsed;
    const rows = parsed.preview || [];
    const head = parsed.columns
      .map((column) => `<th data-col="${column.index}">${escape(columnLabel(column))}</th>`)
      .join("");
    const body = rows
      .map(
        (row) => `
        <tr>
          <td class="row-num">${A.formatInt(row.row)}</td>
          ${parsed.columns
            .map((column) => {
              const value = row.cells[column.index] || "";
              return `<td data-col="${column.index}" title="${escape(value)}">${escape(value || "—")}</td>`;
            })
            .join("")}
        </tr>`
      )
      .join("");
    els.rawTable.innerHTML = `
      <thead><tr><th class="row-num">#</th>${head}</tr></thead>
      <tbody>${body}</tbody>`;
    els.rawHint.textContent = `عرض ${A.formatInt(rows.length)} صفوف من ${A.formatInt(parsed.total_rows)} · ${A.formatInt(parsed.columns.length)} عمودًا`;
    updateRawHighlights();
  }

  function updateRawHighlights() {
    const mapped = new Set(
      Object.values(state.mapping)
        .filter((value) => value !== "" && value !== null && value !== undefined)
        .map(String)
    );
    els.rawTable.querySelectorAll("[data-col]").forEach((cell) => {
      cell.classList.toggle("is-mapped", mapped.has(cell.dataset.col));
    });
  }

  async function analyzeImport() {
    if (state.busy || !state.parsed) return;
    if (!state.mapping.student_nin) {
      showError("يجب ربط عمود رقم التعريف الوطني قبل التحليل.");
      return;
    }
    clearError();
    state.busy = true;
    A.setBusy(els.analyze, true);
    try {
      const data = await A.api("/api/import/preview", {
        method: "POST",
        body: { token: state.parsed.token, mapping: mappedPayload() },
      });
      state.preview = data;
      state.issueTab = data.errors_total ? "errors" : "warnings";
      renderReview();
      setStep(3);
    } catch (error) {
      showError(error.message);
    } finally {
      state.busy = false;
      A.setBusy(els.analyze, false);
    }
  }

  els.analyze.addEventListener("click", analyzeImport);

  /* ---------- Step 3: review ---------- */

  function statTile(label, value, tone, hint) {
    return `
      <div class="import-stat ${tone || ""}">
        <strong>${A.formatInt(value)}</strong>
        <span>${escape(label)}</span>
        ${hint ? `<small>${escape(hint)}</small>` : ""}
      </div>`;
  }

  function chip(label, iconName, tone) {
    return `<span class="chip ${tone || ""}">${A.icon(iconName || "sparkle")}${escape(label)}</span>`;
  }

  function banner(iconName, text, tone) {
    return `<div class="info-banner ${tone || ""}">${A.icon(iconName || "sparkle")}<div>${escape(text)}</div></div>`;
  }

  function renderReview() {
    const data = state.preview;
    const s = data.summary;

    els.summary.innerHTML = [
      statTile("صفوف الملف", s.rows, "", ""),
      statTile("صالح للاستيراد", s.valid, s.valid ? "is-success" : "is-danger", ""),
      statTile("تلميذ جديد", s.new, "is-new", "سيُضاف"),
      statTile("تلميذ موجود", s.existing, "is-update", "سيُحدَّث"),
      statTile("صفوف مرفوضة", s.invalid, s.invalid ? "is-danger" : "", ""),
      statTile("بدون هاتف صالح", s.no_phone, s.no_phone ? "is-warning" : "", ""),
    ].join("");

    const chips = [];
    if (s.duplicates) chips.push(chip(`${A.formatInt(s.duplicates)} رقم مكرر داخل الملف`, "warning", "is-warning"));
    if (s.bad_phone) chips.push(chip(`${A.formatInt(s.bad_phone)} هاتف غير صالح`, "phone", "is-warning"));
    if (s.bad_birth) chips.push(chip(`${A.formatInt(s.bad_birth)} تاريخ ميلاد غير مفهوم`, "calendar", "is-warning"));
    if (s.no_class) chips.push(chip(`${A.formatInt(s.no_class)} بدون قسم`, "users", ""));
    if (s.no_name) chips.push(chip(`${A.formatInt(s.no_name)} بدون اسم`, "user", "is-warning"));
    if (s.photos) chips.push(chip(`${A.formatInt(s.photos)} صورة للمعالجة`, "camera", "is-accent"));
    els.reviewChips.innerHTML = chips.join("");

    const notes = [];
    if (!s.valid) {
      notes.push(banner("warning", "لا يوجد أي صف صالح للاستيراد. صحح الأخطاء في الملف أو أعد ربط الأعمدة ثم كرر التحليل.", "is-danger"));
    } else {
      if (s.invalid) {
        notes.push(banner("warning", `${A.formatInt(s.invalid)} صفًا سيُستبعد لاحتوائه على أخطاء. بقية الصفوف ستُستورد بشكل طبيعي.`, "is-warning"));
      }
      if (s.no_phone) {
        notes.push(banner("message", `${A.formatInt(s.no_phone)} تلميذًا بدون رقم هاتف صالح — سيُحفظ التلميذ لكن لن تُرسل له رسائل SMS حتى تُحدَّث بياناته.`, "is-warning"));
      }
      if (s.existing) {
        notes.push(banner("refresh", `${A.formatInt(s.existing)} تلميذًا موجودًا سيُحدَّث بالحقول المربوطة فقط، مع الحفاظ على سجلات الحضور والرسائل السابقة.`));
      }
      if (s.photos) {
        notes.push(banner("camera", `ستُحاول نسخ ${A.formatInt(s.photos)} صورة من المسارات المحلية؛ الصور التي لا تُقرأ ستُتجاهل مع تنبيه.`));
      }
      if (!s.invalid && !s.no_phone && !s.existing) {
        notes.push(banner("shield-check", "كل الصفوف صالحة ولم تُكتشف أخطاء. راجع العينة ثم أكّد الاستيراد.", "is-success"));
      }
    }
    els.reviewNotes.innerHTML = notes.join("");

    els.reviewBadge.textContent = `${A.formatInt(s.valid)} جاهز`;
    els.reviewCaption.textContent = `عينة من ${A.formatInt(data.preview.length)} صفوف صالحة من إجمالي ${A.formatInt(s.valid)}.`;

    const previewRows = data.preview || [];
    els.reviewTable.innerHTML = `
      <thead>
        <tr>
          <th>#</th>
          <th>رقم التعريف</th>
          <th>الاسم واللقب</th>
          <th>القسم</th>
          <th>هاتف الولي</th>
          <th>تاريخ الميلاد</th>
          <th>الإجراء</th>
        </tr>
      </thead>
      <tbody>
        ${
          previewRows.length
            ? previewRows
                .map((row) => {
                  const name = `${row.last_name || ""} ${row.first_name || ""}`.trim() || "—";
                  const isUpdate = row.status === "تحديث";
                  return `
                    <tr>
                      <td>${A.formatInt(row.row)}</td>
                      <td><span class="mono">${escape(row.student_nin || "—")}</span></td>
                      <td>${escape(name)}</td>
                      <td>${escape(row.class_group || "—")}</td>
                      <td><span class="mono">${escape(row.parent_phone || "—")}</span></td>
                      <td>${escape(A.formatDate(row.birth_date))}</td>
                      <td><span class="badge ${isUpdate ? "badge--update" : "badge--new"}">${escape(row.status)}</span></td>
                    </tr>`;
                })
                .join("")
            : `<tr><td colspan="7" class="empty-cell">لا توجد صفوف صالحة لعرضها.</td></tr>`
        }
      </tbody>`;

    renderIssues();

    els.commit.disabled = !s.valid;
    els.commitHint.textContent = s.valid
      ? `سيُضاف ${A.formatInt(s.new)} ويُحدَّث ${A.formatInt(s.existing)}${s.invalid ? `، مع استبعاد ${A.formatInt(s.invalid)} صفًا` : ""}. لن تُحفظ التغييرات قبل التأكيد.`
      : "لا يمكن الاستيراد قبل وجود صفوف صالحة.";
  }

  function renderIssues() {
    const data = state.preview;
    const errors = data.errors || [];
    const warnings = data.warnings || [];
    const errorsTotal = data.errors_total || 0;
    const warningsTotal = data.warnings_total || 0;
    const tab = state.issueTab;
    const items = tab === "errors" ? errors : warnings;
    const total = tab === "errors" ? errorsTotal : warningsTotal;

    els.issueTabs.querySelectorAll("[data-issue-tab]").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.issueTab === tab);
    });
    const errorsCount = els.issueTabs.querySelector('[data-issue-count="errors"]');
    const warningsCount = els.issueTabs.querySelector('[data-issue-count="warnings"]');
    if (errorsCount) errorsCount.textContent = A.formatInt(errorsTotal);
    if (warningsCount) warningsCount.textContent = A.formatInt(warningsTotal);

    if (!items.length) {
      els.issueList.innerHTML = `
        <div class="issue-empty">
          ${A.icon(tab === "errors" ? "check" : "shield-check")}
          <span>${tab === "errors" ? "لا توجد أخطاء تمنع الاستيراد." : "لا توجد تنبيهات مسجلة."}</span>
        </div>`;
      hide(els.issueMore);
      return;
    }

    els.issueList.innerHTML = items
      .map((item) => {
        const value = item.value ? ` — القيمة: «${item.value}»` : "";
        return `
          <div class="issue-row ${tab === "errors" ? "is-error" : "is-warning"}">
            <span class="issue-badge" title="الصف ${escape(String(item.row))}">#${escape(String(item.row))}</span>
            <span>${escape(item.message)}${escape(value)}</span>
          </div>`;
      })
      .join("");

    if (total > items.length) {
      els.issueMore.textContent = `يتم عرض أول ${A.formatInt(items.length)} من ${A.formatInt(total)} فقط. صحح الملف ثم أعد التحليل لرؤية الباقي.`;
      show(els.issueMore);
    } else {
      hide(els.issueMore);
    }
  }

  els.issueTabs.addEventListener("click", (event) => {
    const button = event.target.closest("[data-issue-tab]");
    if (!button || !state.preview) return;
    state.issueTab = button.dataset.issueTab;
    renderIssues();
  });

  els.backMapping.addEventListener("click", () => {
    clearError();
    setStep(2);
  });

  els.cancel.addEventListener("click", async () => {
    const confirmed = await A.confirm({
      title: "إلغاء الاستيراد",
      message: "سيتم تجاهل الملف الحالي ومسح المعاينة. لم تُحفظ أي بيانات بعد. هل تريد المتابعة؟",
      confirmLabel: "إلغاء الاستيراد",
    });
    if (confirmed) resetWizard();
  });

  els.changeFile.addEventListener("click", () => {
    resetWizard();
    els.file.click();
  });

  /* ---------- Commit ---------- */

  async function commitImport() {
    if (state.busy || !state.preview || !state.parsed) return;
    const s = state.preview.summary;
    if (!s.valid) {
      showError("لا يوجد أي صف صالح للاستيراد.");
      return;
    }
    const confirmed = await A.confirm({
      title: "تأكيد استيراد القائمة",
      subtitle: state.parsed.file_name,
      message: `سيتم إضافة ${A.formatInt(s.new)} تلميذًا جديدًا وتحديث ${A.formatInt(s.existing)} تلميذًا موجودًا، مع الحفاظ على سجلات الحضور والرسائل.${s.invalid ? ` وسيُستبعد ${A.formatInt(s.invalid)} صفًا غير صالح.` : ""}`,
      confirmLabel: "بدء الاستيراد",
    });
    if (!confirmed) return;

    clearError();
    state.busy = true;
    A.setBusy(els.commit, true);
    const token = state.parsed.token;
    const mapping = mappedPayload();
    try {
      const data = await A.api("/api/import/commit", {
        method: "POST",
        body: { token, mapping },
      });
      renderDone(data);
      setStep(4);
      state.parsed = null;
      state.preview = null;
      A.toast(
        `أُضيف ${A.formatInt(data.inserted)} وتم تحديث ${A.formatInt(data.updated)} تلميذًا.`,
        "success",
        "اكتمل الاستيراد"
      );
    } catch (error) {
      showError(error.message);
    } finally {
      state.busy = false;
      A.setBusy(els.commit, false);
    }
  }

  els.commit.addEventListener("click", commitImport);

  function renderDone(data) {
    els.doneMessage.textContent = `أُضيف ${A.formatInt(data.inserted)} تلميذًا جديدًا وحُدِّثت بيانات ${A.formatInt(data.updated)} تلميذًا في قاعدة البيانات المحلية.`;
    els.doneStats.innerHTML = [
      `<div class="is-success"><strong>${A.formatInt(data.inserted)}</strong><span>تلميذ جديد</span></div>`,
      `<div class="is-update"><strong>${A.formatInt(data.updated)}</strong><span>تلميذ محدَّث</span></div>`,
      `<div><strong>${A.formatInt(data.total)}</strong><span>الإجمالي</span></div>`,
      `<div><strong>${A.formatInt(data.photos_copied)}</strong><span>صورة منسوخة</span></div>`,
    ].join("");

    const notes = [];
    if (data.errors_total) {
      notes.push(banner("warning", `${A.formatInt(data.errors_total)} صفًا لم يُستورد بسبب أخطاء في البيانات. صححه في الملف واستورده مجددًا إذا لزم.`, "is-warning"));
    }
    if (data.warnings_total) {
      const warnings = data.warnings || [];
      const shown = warnings.slice(0, 6);
      notes.push(banner("warning", `سُجِّل ${A.formatInt(data.warnings_total)} تنبيهًا أثناء الاستيراد، منها مشاكل الصور أو الهواتف.`, "is-warning"));
      if (shown.length) {
        notes.push(`
          <div class="issue-list">
            ${shown
              .map(
                (item) => `
                <div class="issue-row is-warning">
                  <span class="issue-badge">#${escape(String(item.row))}</span>
                  <span>${escape(item.message)}</span>
                </div>`
              )
              .join("")}
          </div>`);
      }
      if (data.warnings_total > shown.length) {
        notes.push(`<span class="tiny muted">تظهر أول ${A.formatInt(shown.length)} تنبيهات فقط. راجع صفحة التلاميذ لاستكمال البيانات الناقصة.</span>`);
      }
    }
    if (!data.errors_total && !data.warnings_total) {
      notes.push(banner("shield-check", "اكتملت العملية دون أخطاء أو تنبيهات. يمكنك الآن طباعة بطاقات التلاميذ أو بدء المسح.", "is-success"));
    }
    els.doneNotes.innerHTML = notes.join("");
  }

  /* ---------- Reset ---------- */

  function resetWizard() {
    state.file = null;
    state.parsed = null;
    state.mapping = {};
    state.preview = null;
    state.issueTab = "errors";
    state.busy = false;
    els.file.value = "";
    els.fileHead.innerHTML = "";
    els.mappingGrid.innerHTML = "";
    els.rawTable.innerHTML = "";
    els.rawHint.textContent = "";
    els.summary.innerHTML = "";
    els.reviewChips.innerHTML = "";
    els.reviewNotes.innerHTML = "";
    els.reviewTable.innerHTML = "";
    els.issueList.innerHTML = "";
    els.issueMore.textContent = "";
    hide(els.issueMore);
    els.doneStats.innerHTML = "";
    els.doneNotes.innerHTML = "";
    els.doneMessage.textContent = "";
    els.commit.disabled = false;
    els.analyze.disabled = false;
    clearError();
    setStep(1);
  }

  els.again.addEventListener("click", () => {
    resetWizard();
    els.file.click();
  });

  setStep(1);
})();
