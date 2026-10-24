/* Print studio: live CR80 card / sticker preview and exact-A4 sheet launcher. */
(function () {
  "use strict";

  const A = window.App;
  const escape = A.escapeHtml;
  const $ = (id) => document.getElementById(id);

  const els = {
    bootstrap: $("studio-bootstrap"),
    modeButtons: Array.from(document.querySelectorAll("[data-studio-mode]")),
    scopeButtons: Array.from(document.querySelectorAll("#studio-scope [data-scope]")),
    classWrap: $("studio-class-wrap"),
    classSelect: $("studio-class"),
    studentWrap: $("studio-student-wrap"),
    studentSearch: $("studio-student-search"),
    studentSelect: $("studio-student-select"),
    count: $("studio-count"),
    previewCount: $("studio-preview-count"),
    cardOptions: $("studio-card-options"),
    stickerOptions: $("studio-sticker-options"),
    cardElements: $("studio-card-elements"),
    modeLabel: $("studio-mode-label"),
    showHeader: $("pc-show-header"),
    showLogo: $("pc-show-logo"),
    showPhoto: $("pc-show-photo"),
    showBirth: $("pc-show-birth"),
    showQr: $("pc-show-qr"),
    showStamp: $("pc-show-stamp"),
    accent: $("pc-accent"),
    accentHex: $("pc-accent-hex"),
    header: $("pc-header"),
    headerHex: $("pc-header-hex"),
    radius: $("pc-radius"),
    radiusValue: $("pc-radius-value"),
    save: $("studio-save"),
    print: $("studio-print"),
    printLabel: $("studio-print-label"),
    preview: $("studio-preview"),
    previewLabel: $("studio-preview-label"),
  };

  const brand = els.bootstrap
    ? {
        school: els.bootstrap.dataset.school || "اسم المؤسسة التعليمية",
        year: els.bootstrap.dataset.year || "",
        principal: els.bootstrap.dataset.principal || "مدير المؤسسة",
        logo: els.bootstrap.dataset.logo || "",
        stamp: els.bootstrap.dataset.stamp || "",
      }
    : { school: "اسم المؤسسة التعليمية", year: "", principal: "مدير المؤسسة", logo: "", stamp: "" };

  const PREVIEW_STUDENT = {
    student_nin: "",
    first_name: "اسم",
    last_name: "التلميذ",
    class_group: "القسم",
    birth_date: "",
    photo_url: "",
    full_name: "اسم التلميذ",
  };

  const state = {
    mode: "cards",
    scope: "all",
    classGroup: "",
    studentNin: "",
    selectedStudent: null,
    students: [],
    pickerItems: [],
    pickerLoaded: false,
    total: 0,
    loading: false,
    requestId: 0,
    pickerRequest: 0,
    config: {},
    settings: null,
  };

  function safeColor(value, fallback) {
    return /^#[0-9a-fA-F]{6}$/.test(String(value || "")) ? String(value) : fallback;
  }

  function assetUrl(value) {
    const url = String(value || "").trim();
    return url.startsWith("/") ? url : "";
  }

  function hexToRgba(hex, alpha) {
    const value = safeColor(hex, "#0e7c66").slice(1);
    const r = parseInt(value.slice(0, 2), 16);
    const g = parseInt(value.slice(2, 4), 16);
    const b = parseInt(value.slice(4, 6), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }

  function readConfig() {
    const cardLayout = document.querySelector('input[name="card_layout"]:checked');
    const stickerLayout = document.querySelector('input[name="sticker_layout"]:checked');
    state.config = {
      show_header: els.showHeader.checked,
      show_logo: els.showLogo.checked,
      show_photo: els.showPhoto.checked,
      show_birth_date: els.showBirth.checked,
      show_qr: els.showQr.checked,
      show_stamp: els.showStamp.checked,
      accent_color: safeColor(els.accent.value, "#0e7c66"),
      header_color: safeColor(els.header.value, "#0b3b31"),
      card_layout: Number(cardLayout ? cardLayout.value : 10),
      sticker_layout: Number(stickerLayout ? stickerLayout.value : 30),
      border_radius: Number(els.radius.value) || 0,
    };
    els.radiusValue.textContent = String(state.config.border_radius);
    return state.config;
  }

  function isDemo() {
    return state.students.length === 0;
  }

  function studentName(student) {
    return student.full_name || `${student.first_name || ""} ${student.last_name || ""}`.trim() || "تلميذ بدون اسم";
  }

  function initials(student) {
    return A.initials(student.first_name, student.last_name);
  }

  function currentSchool() {
    const settings = state.settings || {};
    return {
      school: settings.school_name || brand.school || "اسم المؤسسة التعليمية",
      year: settings.academic_year || brand.year || "",
      principal: settings.principal_name || brand.principal || "مدير المؤسسة",
      logo: assetUrl(settings.logo_url || brand.logo),
      stamp: assetUrl(settings.stamp_url || brand.stamp),
    };
  }

  /* ---------- Preview markup ---------- */

  function cardMarkup(student) {
    const c = state.config;
    const school = currentSchool();
    const accent = safeColor(c.accent_color, "#0e7c66");
    const header = safeColor(c.header_color, "#0b3b31");
    const nin = student.student_nin || "";
    const hasNin = Boolean(nin);
    const safeNin = escape(nin || "—");
    const barcodeUrl = `/api/barcode/${encodeURIComponent(nin)}`;
    const qrUrl = `/api/qr/${encodeURIComponent(nin)}`;
    const className = student.class_group || "بدون قسم";
    const birth = student.birth_date ? A.formatDate(student.birth_date) : "—";
    const photo = assetUrl(student.photo_url);
    const logo = c.show_logo ? school.logo : "";

    return `
      <div class="preview-card ${c.show_header ? "" : "no-header"}"
           style="--pc-header:${escape(header)};--pc-accent:${escape(accent)};border-radius:${Math.max(0, Number(c.border_radius) || 0)}cqw">
        ${
          c.show_header
            ? `<div class="pc-head">
                ${logo ? `<span class="pc-logo"><img src="${escape(logo)}" alt="شعار المؤسسة"></span>` : ""}
                <div class="pc-school">
                  <strong>${escape(school.school)}</strong>
                  <span>${escape(school.year || "السنة الدراسية")}</span>
                </div>
                <span class="pc-year">بطاقة التلميذ</span>
              </div>`
            : ""
        }
        <div class="pc-body">
          ${
            c.show_photo
              ? `<div class="pc-photo">
                  <span>${escape(initials(student))}</span>
                  ${photo ? `<img data-preview-photo src="${escape(photo)}" alt="${escape(studentName(student))}">` : ""}
                </div>`
              : ""
          }
          <div class="pc-info">
            <div class="pc-name" title="${escape(studentName(student))}">${escape(studentName(student))}</div>
            <span class="pc-class" style="color:${escape(accent)};background:${escape(hexToRgba(accent, 0.1))}">${escape(className)}</span>
            <div class="pc-meta"><span>رقم التعريف</span><b>${safeNin}</b></div>
            ${c.show_birth_date ? `<div class="pc-meta"><span>تاريخ الميلاد</span><b>${escape(birth)}</b></div>` : ""}
          </div>
          ${
            c.show_qr && hasNin
              ? `<div class="pc-qr" style="border-color:${escape(hexToRgba(accent, 0.28))}"><img src="${qrUrl}" alt="رمز QR"></div>`
              : ""
          }
        </div>
        <div class="pc-foot">
          <div class="pc-barcode">
            ${
              hasNin
                ? `<img src="${barcodeUrl}" alt="باركود ${safeNin}">`
                : `<span style="font-size:6.5pt;color:#7a8a85">معاينة بدون باركود</span>`
            }
            <span class="pc-nin">${safeNin}</span>
          </div>
          ${
            c.show_stamp
              ? `<div class="pc-sign">
                  ${school.stamp ? `<img src="${escape(school.stamp)}" alt="خاتم المؤسسة">` : ""}
                  <span>${escape(school.principal)}</span>
                </div>`
              : ""
          }
        </div>
      </div>`;
  }

  function stickerMarkup(student) {
    const c = state.config;
    const school = currentSchool();
    const accent = safeColor(c.accent_color, "#0e7c66");
    const header = safeColor(c.header_color, "#0b3b31");
    const nin = student.student_nin || "";
    const hasNin = Boolean(nin);
    const safeNin = escape(nin || "—");
    const logo = c.show_logo ? school.logo : "";
    return `
      <div class="preview-sticker" style="--pc-accent:${escape(accent)};--pc-header:${escape(header)}">
        <div class="st-head">
          ${logo ? `<img src="${escape(logo)}" alt="">` : ""}
          <span style="color:${escape(header)}">${escape(school.school)}</span>
        </div>
        <strong>${escape(studentName(student))}</strong>
        <span class="st-class">${escape(student.class_group || "بدون قسم")}</span>
        ${
          hasNin
            ? `<img class="st-barcode" src="/api/barcode/${encodeURIComponent(nin)}" alt="باركود ${safeNin}">`
            : `<span style="font-size:7pt;color:#7a8a85">معاينة بدون باركود</span>`
        }
        <span class="st-nin">${safeNin}</span>
      </div>`;
  }

  function renderPreview() {
    const c = state.config;
    const demo = isDemo();
    if (state.mode === "cards") {
      const student = state.students[0] || PREVIEW_STUDENT;
      els.preview.innerHTML = `<div class="preview-card-shell">${cardMarkup(student)}</div>`;
      els.previewLabel.textContent = A.t(
        `${demo ? "معاينة تصميم بدون باركود — " : ""}CR80 · 85.6 × 54 مم · ${c.card_layout} بطاقات في صفحة A4`
      );
    } else {
      const students = demo ? [PREVIEW_STUDENT] : state.students.slice(0, 4);
      els.preview.innerHTML = `<div class="preview-sticker-wrap">${students.map(stickerMarkup).join("")}</div>`;
      const height = c.sticker_layout === 30 ? "27" : "34";
      els.previewLabel.textContent = A.t(
        `${demo ? "معاينة تصميم بدون باركود — " : ""}ملصق 65 × ${height} مم · ${c.sticker_layout} ملصقًا في صفحة A4`
      );
    }
  }

  /* ---------- Counts / state ---------- */

  function updateModeUI() {
    els.modeButtons.forEach((button) => {
      button.classList.toggle("is-active", button.dataset.studioMode === state.mode);
    });
    const cards = state.mode === "cards";
    els.cardOptions.classList.toggle("hidden", !cards);
    els.stickerOptions.classList.toggle("hidden", cards);
    els.cardElements.classList.toggle("hidden", !cards);
    els.modeLabel.innerHTML = cards
      ? `${A.icon("id-card")} بطاقات CR80`
      : `${A.icon("barcode")} ملصقات باركود`;
    updatePrintState();
    renderPreview();
  }

  function updateScopeUI() {
    els.scopeButtons.forEach((button) => {
      button.classList.toggle("is-active", button.dataset.scope === state.scope);
    });
    els.classWrap.classList.toggle("hidden", state.scope !== "class");
    els.studentWrap.classList.toggle("hidden", state.scope !== "student");
    if (state.scope === "student" && !state.pickerLoaded) {
      loadStudentPicker("");
    }
    updatePrintState();
  }

  const CARD_FORMS = { one: "بطاقة", two: "بطاقتان", few: "بطاقات", many: "بطاقة" };
  const STICKER_FORMS = { one: "ملصق", two: "ملصقان", few: "ملصقات", many: "ملصقًا" };
  const CARD_READY = {
    one: "بطاقة جاهزة",
    two: "بطاقتان جاهزتان",
    few: "بطاقات جاهزة",
    many: "بطاقة جاهزة",
  };
  const STICKER_READY = {
    one: "ملصق جاهز",
    two: "ملصقان جاهزان",
    few: "ملصقات جاهزة",
    many: "ملصقًا جاهزًا",
  };

  function arabicPlural(count, forms) {
    const n = Math.abs(count);
    if (n === 1) return forms.one;
    if (n === 2) return forms.two;
    const rem100 = n % 100;
    if ((n >= 3 && n <= 10) || (rem100 >= 3 && rem100 <= 10)) return forms.few;
    return forms.many;
  }

  function updatePrintState() {
    const cards = state.mode === "cards";
    const forms = cards ? CARD_FORMS : STICKER_FORMS;
    const readyForms = cards ? CARD_READY : STICKER_READY;
    const noun = arabicPlural(state.total, forms);
    const needsClass = state.scope === "class" && !state.classGroup;
    const needsStudent = state.scope === "student" && !state.selectedStudent;
    let countText = "";
    if (needsClass) countText = "اختر القسم أولًا";
    else if (needsStudent) countText = "اختر تلميذًا أولًا";
    else if (state.loading) countText = "جارٍ حساب التلاميذ…";
    else if (state.total)
      countText = `<strong>${A.formatInt(state.total)}</strong> ${A.t(`${arabicPlural(state.total, readyForms)} للطباعة`)}`;
    else countText = "لا يوجد تلاميذ نشطون في هذا النطاق";

    els.count.innerHTML = `${A.icon(state.loading ? "clock" : "users")}<span>${countText}</span>`;
    els.previewCount.textContent = state.total ? A.t(`${A.formatInt(state.total)} ${noun}`) : "—";
    els.print.disabled = Boolean(needsClass || needsStudent || state.loading || !state.total);
    els.printLabel.textContent = state.total
      ? A.t(`طباعة ${A.formatInt(state.total)} ${noun}`)
      : A.t("معاينة وطباعة");
  }

  /* ---------- Data ---------- */

  async function loadSettings() {
    try {
      const data = await A.api("/api/settings");
      state.settings = data.settings;
      renderPreview();
    } catch (error) {
      /* branding falls back to server-rendered values */
    }
  }

  async function loadDataset() {
    const requestId = ++state.requestId;
    if (state.scope === "student") {
      if (state.selectedStudent) {
        state.students = [state.selectedStudent];
        state.total = 1;
      } else {
        state.students = [];
        state.total = 0;
      }
      state.loading = false;
      updatePrintState();
      renderPreview();
      return;
    }
    if (state.scope === "class" && !state.classGroup) {
      state.students = [];
      state.total = 0;
      state.loading = false;
      updatePrintState();
      renderPreview();
      return;
    }
    state.loading = true;
    updatePrintState();
    const params = { per_page: 4, active: 1 };
    if (state.scope === "class") params.class_group = state.classGroup;
    try {
      const data = await A.api(A.apiUrl("/api/students", params));
      if (requestId !== state.requestId) return;
      state.students = data.items || [];
      state.total = Number(data.total) || 0;
    } catch (error) {
      if (requestId !== state.requestId) return;
      state.students = [];
      state.total = 0;
      A.toast(error.message, "error");
    } finally {
      if (requestId === state.requestId) {
        state.loading = false;
        updatePrintState();
        renderPreview();
      }
    }
  }

  async function loadStudentPicker(query) {
    const requestId = ++state.pickerRequest;
    state.pickerLoaded = true;
    try {
      const data = await A.api(A.apiUrl("/api/students", { q: query, per_page: 50, active: 1 }));
      if (requestId !== state.pickerRequest) return;
      state.pickerItems = data.items || [];
      renderStudentPicker();
    } catch (error) {
      if (requestId !== state.pickerRequest) return;
      A.toast(error.message, "error");
    }
  }

  function renderStudentPicker() {
    const items = [];
    if (state.selectedStudent) items.push(state.selectedStudent);
    state.pickerItems.forEach((student) => {
      if (!state.selectedStudent || student.student_nin !== state.selectedStudent.student_nin) {
        items.push(student);
      }
    });
    const options = items
      .map((student) => {
        const label = `${studentName(student)} — ${student.student_nin}${student.class_group ? ` — ${student.class_group}` : ""}`;
        return `<option value="${escape(student.student_nin)}">${escape(label)}</option>`;
      })
      .join("");
    els.studentSelect.innerHTML = `<option value="">— اختر تلميذًا —</option>${options}`;
    els.studentSelect.value = state.selectedStudent ? state.selectedStudent.student_nin : "";
  }

  /* ---------- Events ---------- */

  els.modeButtons.forEach((button) => {
    button.addEventListener("click", () => {
      state.mode = button.dataset.studioMode === "stickers" ? "stickers" : "cards";
      updateModeUI();
    });
  });

  els.scopeButtons.forEach((button) => {
    button.addEventListener("click", () => {
      state.scope = ["class", "student"].includes(button.dataset.scope) ? button.dataset.scope : "all";
      updateScopeUI();
      loadDataset();
    });
  });

  els.classSelect.addEventListener("change", () => {
    state.classGroup = els.classSelect.value;
    loadDataset();
  });

  els.studentSearch.addEventListener(
    "input",
    A.debounce(() => {
      loadStudentPicker(els.studentSearch.value.trim());
    }, 320)
  );

  els.studentSelect.addEventListener("change", () => {
    const nin = els.studentSelect.value;
    state.studentNin = nin;
    if (!nin) {
      state.selectedStudent = null;
    } else {
      state.selectedStudent =
        state.pickerItems.find((student) => student.student_nin === nin) ||
        (state.selectedStudent && state.selectedStudent.student_nin === nin ? state.selectedStudent : null);
    }
    state.students = state.selectedStudent ? [state.selectedStudent] : [];
    state.total = state.selectedStudent ? 1 : 0;
    renderStudentPicker();
    renderPreview();
    updatePrintState();
  });

  const onDesignChange = () => {
    readConfig();
    renderPreview();
    updatePrintState();
  };

  [
    els.showHeader,
    els.showLogo,
    els.showPhoto,
    els.showBirth,
    els.showQr,
    els.showStamp,
    ...Array.from(document.querySelectorAll('input[name="card_layout"], input[name="sticker_layout"]')),
  ].forEach((control) => control.addEventListener("change", onDesignChange));

  els.radius.addEventListener("input", () => {
    readConfig();
    renderPreview();
  });

  els.accent.addEventListener("input", () => {
    els.accentHex.value = els.accent.value;
    onDesignChange();
  });
  els.header.addEventListener("input", () => {
    els.headerHex.value = els.header.value;
    onDesignChange();
  });

  function bindHexInput(colorInput, textInput) {
    textInput.addEventListener("input", () => {
      if (/^#[0-9a-fA-F]{6}$/.test(textInput.value.trim())) {
        colorInput.value = textInput.value.trim();
        onDesignChange();
      }
    });
    textInput.addEventListener("blur", () => {
      textInput.value = colorInput.value;
    });
  }
  bindHexInput(els.accent, els.accentHex);
  bindHexInput(els.header, els.headerHex);

  els.preview.addEventListener(
    "error",
    (event) => {
      if (event.target instanceof HTMLImageElement) event.target.style.display = "none";
    },
    true
  );

  els.save.addEventListener("click", async () => {
    const config = readConfig();
    A.setBusy(els.save, true);
    try {
      await A.api("/api/print/config", {
        method: "PUT",
        body: {
          show_header: config.show_header,
          show_logo: config.show_logo,
          show_photo: config.show_photo,
          show_birth_date: config.show_birth_date,
          show_qr: config.show_qr,
          show_stamp: config.show_stamp,
          card_layout: config.card_layout,
          sticker_layout: config.sticker_layout,
          border_radius: config.border_radius,
          accent_color: config.accent_color,
          header_color: config.header_color,
        },
      });
      A.toast("سيُستعمل هذا التصميم افتراضيًا في المرات القادمة.", "success", "تم حفظ التصميم");
    } catch (error) {
      A.toast(error.message, "error");
    } finally {
      A.setBusy(els.save, false);
    }
  });

  els.print.addEventListener("click", () => {
    if (els.print.disabled) return;
    const config = readConfig();
    const params = new URLSearchParams();
    params.set("mode", state.mode);
    params.set("layout", String(state.mode === "cards" ? config.card_layout : config.sticker_layout));
    params.set("scope", state.scope);
    params.set("print", "1");
    if (state.scope === "class") params.set("class_group", state.classGroup);
    if (state.scope === "student") params.set("nin", state.studentNin);
    params.set("accent_color", config.accent_color);
    params.set("header_color", config.header_color);
    params.set("border_radius", String(config.border_radius));
    params.set("show_header", config.show_header ? "1" : "0");
    params.set("show_logo", config.show_logo ? "1" : "0");
    params.set("show_photo", config.show_photo ? "1" : "0");
    params.set("show_birth_date", config.show_birth_date ? "1" : "0");
    params.set("show_qr", config.show_qr ? "1" : "0");
    params.set("show_stamp", config.show_stamp ? "1" : "0");
    const popup = window.open(`/print/sheet?${params.toString()}`, "_blank");
    if (popup) popup.opener = null;
  });

  /* ---------- Init ---------- */

  readConfig();
  updateModeUI();
  updateScopeUI();
  renderPreview();
  updatePrintState();
  loadSettings();
  loadDataset();
})();
