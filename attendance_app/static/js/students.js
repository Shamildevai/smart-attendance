/* Student directory: filters, CRUD, photos, archive/reactivate, today's profile. */
(function () {
  "use strict";

  const A = window.App;
  const state = {
    q: "",
    classGroup: "",
    active: "all",
    page: 1,
    perPage: 20,
    items: [],
    counts: { all: 0, active: 0, inactive: 0 },
    classes: [],
    pages: 1,
    total: 0,
    loading: false,
    requestId: 0,
    autoNewNin: "",
    autoOpenProfile: false,
  };

  const $ = (id) => document.getElementById(id);
  const searchInput = $("students-search");
  const classFilter = $("students-class-filter");
  const activeFilter = $("students-active-filter");
  const perPageSelect = $("students-per-page");
  const tbody = $("students-table-body");

  function safe(value) {
    return A.escapeHtml(value === null || value === undefined ? "" : value);
  }

  function displayClass(value) {
    return value || "بدون قسم";
  }

  function printUrl(nin) {
    const params = new URLSearchParams({ scope: "student", nin, mode: "cards", layout: "10" });
    return `/print/sheet?${params.toString()}`;
  }

  function currentFilters() {
    const params = new URLSearchParams();
    if (state.q) params.set("q", state.q);
    if (state.classGroup) params.set("class_group", state.classGroup);
    if (state.active !== "all") params.set("active", state.active);
    if (state.page > 1) params.set("page", String(state.page));
    if (state.perPage !== 20) params.set("per_page", String(state.perPage));
    return params;
  }

  function syncUrl() {
    const params = currentFilters();
    const url = `${window.location.pathname}${params.toString() ? `?${params.toString()}` : ""}`;
    window.history.replaceState({}, "", url);
  }

  function readUrl() {
    const params = new URLSearchParams(window.location.search);
    state.q = params.get("q") || "";
    state.classGroup = params.get("class_group") || "";
    const active = params.get("active") || "all";
    state.active = ["1", "0"].includes(active) ? active : "all";
    const page = Number(params.get("page") || "1");
    state.page = Number.isFinite(page) && page > 0 ? Math.floor(page) : 1;
    const perPage = Number(params.get("per_page") || "20");
    state.perPage = [10, 20, 50, 100].includes(perPage) ? perPage : 20;
    state.autoNewNin = params.get("new") === "1" ? params.get("nin") || "" : "";
    state.autoOpenProfile = params.get("profile") === "1";
    searchInput.value = state.q;
    activeFilter.value = state.active;
    perPageSelect.value = String(state.perPage);
  }

  /* ---------------------------------------------------------------------- */
  /* Loading + rendering                                                     */
  /* ---------------------------------------------------------------------- */

  async function loadStudents(options = {}) {
    const requestId = ++state.requestId;
    state.loading = true;
    if (!options.silent) {
      tbody.innerHTML = `<tr><td colspan="6" class="empty-cell"><div class="loading-state"><span class="spinner"></span><span>جارٍ تحميل التلاميذ…</span></div></td></tr>`;
    }
    try {
      const params = currentFilters();
      params.set("per_page", String(state.perPage));
      if (state.page > 1) params.set("page", String(state.page));
      const data = await A.api(`/api/students?${params.toString()}`);
      if (requestId !== state.requestId) return;
      state.items = Array.isArray(data.items) ? data.items : [];
      state.counts = data.counts || { all: 0, active: 0, inactive: 0 };
      state.classes = Array.isArray(data.classes) ? data.classes : [];
      state.pages = Number(data.pages) || 1;
      state.total = Number(data.total) || 0;
      if (state.page > state.pages) {
        state.page = state.pages;
        return loadStudents(options);
      }
      applyClasses();
      renderCounts();
      renderRows();
      renderResultLabel();
      A.pagination($("students-pagination"), {
        page: state.page,
        pages: state.pages,
        total: state.total,
        onChange(page) {
          state.page = page;
          loadStudents();
        },
      });
      syncUrl();
      handleUrlActions();
    } catch (error) {
      if (requestId !== state.requestId) return;
      tbody.innerHTML = `<tr><td colspan="6" class="empty-cell">${A.emptyState(
        "warning",
        "تعذر تحميل قائمة التلاميذ",
        error.message || "أعد المحاولة.",
        '<button class="btn btn-secondary btn-sm" type="button" data-retry>إعادة المحاولة</button>'
      )}</td></tr>`;
      $("students-result-label").textContent = "تعذر تحميل البيانات.";
      $("students-pagination").innerHTML = "";
      if (!options.silent) A.toast(error.message || "تعذر تحميل القائمة.", "error");
    } finally {
      if (requestId === state.requestId) state.loading = false;
    }
  }

  function applyClasses() {
    const selected = state.classGroup;
    const options = ['<option value="">كل الأقسام</option>'];
    state.classes.forEach((item) => {
      const suffix = item.active !== item.total ? ` (${A.formatInt(item.active)}/${A.formatInt(item.total)})` : ` (${A.formatInt(item.total)})`;
      options.push(`<option value="${safe(item.value)}">${safe(item.label)}${suffix}</option>`);
    });
    classFilter.innerHTML = options.join("");
    const values = state.classes.map((item) => item.value);
    if (selected && !values.includes(selected)) {
      state.classGroup = "";
      classFilter.value = "";
      state.page = 1;
    } else {
      classFilter.value = selected;
    }
  }

  function classOptionsForForm() {
    return state.classes
      .filter((item) => item.value !== "__none__")
      .map((item) => `<option value="${safe(item.value)}"></option>`)
      .join("");
  }

  function renderCounts() {
    $("students-count-all").innerHTML = `<strong>${A.formatInt(state.counts.all)}</strong>&nbsp;إجمالي`;
    $("students-count-active").innerHTML = `<strong>${A.formatInt(state.counts.active)}</strong>&nbsp;نشط`;
    $("students-count-inactive").innerHTML = `<strong>${A.formatInt(state.counts.inactive)}</strong>&nbsp;مؤرشف`;
  }

  function filterSummary() {
    const bits = [A.t(`${A.formatInt(state.total)} تلميذ`)];
    if (state.q) bits.push(A.t(`بحث: «${state.q}»`));
    if (state.classGroup === "__none__") bits.push(A.t("بدون قسم"));
    else if (state.classGroup) bits.push(A.t(`القسم: ${state.classGroup}`));
    if (state.active === "1") bits.push(A.t("النشطون فقط"));
    if (state.active === "0") bits.push(A.t("المؤرشفون فقط"));
    if (state.pages > 1) bits.push(A.t(`صفحة ${state.page} من ${state.pages}`));
    return bits.join(" — ");
  }

  function renderResultLabel() {
    $("students-result-label").textContent = filterSummary();
  }

  function renderRows() {
    if (!state.items.length) {
      const description = state.q || state.classGroup || state.active !== "all"
        ? "لا توجد نتائج مطابقة للمعايير المحددة. جرّب تغيير البحث أو الفلاتر."
        : "لم تُضف أي تلميذ بعد. ابدأ بإضافة تلميذ يدويًا أو استيراد قائمة من Excel.";
      const action = state.q || state.classGroup || state.active !== "all"
        ? '<button class="btn btn-secondary btn-sm" type="button" data-reset-filters>مسح الفلاتر</button>'
        : '<button class="btn btn-primary btn-sm" type="button" data-add-inline>إضافة أول تلميذ</button>';
      tbody.innerHTML = `<tr><td colspan="6" class="empty-cell">${A.emptyState("users", "لا يوجد تلاميذ", description, action)}</td></tr>`;
      return;
    }
    tbody.innerHTML = state.items
      .map((student) => {
        const activeBadge = student.is_active
          ? '<span class="badge badge--present"><i class="badge-dot"></i>نشط</span>'
          : '<span class="badge badge--cancelled"><i class="badge-dot"></i>مؤرشف</span>';
        const toggleLabel = student.is_active ? "أرشفة التلميذ" : "تنشيط التلميذ";
        return `
          <tr>
            <td>
              <div class="student-identity">
                ${A.avatar(student)}
                <div>
                  <strong>${safe(student.full_name || "بدون اسم")}</strong>
                  <small class="student-nin">${safe(student.student_nin)}</small>
                </div>
              </div>
            </td>
            <td class="col-class"><span class="chip">${safe(displayClass(student.class_group))}</span></td>
            <td class="col-phone"><span class="mono">${safe(student.parent_phone || "—")}</span></td>
            <td class="col-birth">${safe(student.birth_date ? A.formatDate(student.birth_date) : "—")}</td>
            <td class="col-status">${activeBadge}</td>
            <td class="col-actions">
              <div class="actions">
                <button class="icon-btn" type="button" data-action="view" data-nin="${safe(student.student_nin)}" title="ملف التلميذ" aria-label="عرض ملف ${safe(student.full_name)}">
                  ${A.icon("eye")}
                </button>
                <button class="icon-btn" type="button" data-action="edit" data-nin="${safe(student.student_nin)}" title="تعديل البيانات" aria-label="تعديل ${safe(student.full_name)}">
                  ${A.icon("edit")}
                </button>
                <a class="icon-btn" href="${printUrl(student.student_nin)}" target="_blank" rel="noopener" title="طباعة بطاقة التعريف" aria-label="طباعة بطاقة ${safe(student.full_name)}">
                  ${A.icon("printer")}
                </a>
                <button class="icon-btn ${student.is_active ? "is-danger" : ""}" type="button" data-action="toggle" data-nin="${safe(student.student_nin)}" title="${toggleLabel}" aria-label="${toggleLabel}: ${safe(student.full_name)}">
                  ${A.icon(student.is_active ? "trash" : "refresh")}
                </button>
              </div>
            </td>
          </tr>`;
      })
      .join("");
  }

  function findStudent(nin) {
    return state.items.find((item) => item.student_nin === nin) || null;
  }

  function handleUrlActions() {
    if (state.autoNewNin !== null && state.autoNewNin !== undefined && state.autoNewNin !== "") {
      const nin = state.autoNewNin;
      state.autoNewNin = "";
      openStudentForm(null, nin);
      syncUrl();
      return;
    }
    if (state.autoOpenProfile) {
      state.autoOpenProfile = false;
      const target = state.items.find((item) => item.student_nin === state.q) || state.items[0];
      if (target) openProfile(target.student_nin);
      syncUrl();
    }
  }

  /* ---------------------------------------------------------------------- */
  /* Student form                                                            */
  /* ---------------------------------------------------------------------- */

  function openStudentForm(student, presetNin) {
    const editing = Boolean(student);
    const initial = student || {
      student_nin: presetNin || "",
      first_name: "",
      last_name: "",
      birth_date: null,
      class_group: state.classGroup && state.classGroup !== "__none__" ? state.classGroup : "",
      parent_phone: "",
      is_active: true,
    };
    const classValue = initial.class_group || "";
    const body = `
      <form id="student-form" class="form-grid" novalidate>
        <div class="field form-span-2">
          <label for="student-nin">رقم التعريف الوطني <span class="required">*</span></label>
          <input class="input mono" id="student-nin" name="student_nin" value="${safe(initial.student_nin)}"
                 ${editing ? "readonly" : "required autofocus"} maxlength="64" autocomplete="off" placeholder="مثال: 109876543210">
          <div class="field-hint">${editing
            ? "رقم التعريف غير قابل للتعديل لأنه مرتبط بسجلات الحضور والباركود المطبوع."
            : "هذا هو الرمز الذي سيُقرأ من بطاقة التلميذ. يجب أن يكون فريدًا."}</div>
        </div>
        <div class="field">
          <label for="student-last-name">اللقب</label>
          <input class="input" id="student-last-name" name="last_name" value="${safe(initial.last_name)}" maxlength="120" placeholder="لقب التلميذ">
        </div>
        <div class="field">
          <label for="student-first-name">الاسم</label>
          <input class="input" id="student-first-name" name="first_name" value="${safe(initial.first_name)}" maxlength="120" placeholder="اسم التلميذ">
        </div>
        <div class="field">
          <label for="student-class">القسم / الفوج</label>
          <input class="input" id="student-class" name="class_group" value="${safe(classValue)}" maxlength="80" list="student-class-options" placeholder="مثال: 1AS1">
          <datalist id="student-class-options">${classOptionsForForm()}</datalist>
        </div>
        <div class="field">
          <label for="student-phone">هاتف الولي</label>
          <input class="input mono" id="student-phone" name="parent_phone" value="${safe(initial.parent_phone)}" maxlength="24" type="tel" inputmode="tel" placeholder="0555123456">
          <div class="field-hint">يُستعمل لإرسال إشعارات الغياب عبر SMS.</div>
        </div>
        <div class="field">
          <label for="student-birth">تاريخ الميلاد</label>
          <input class="input" id="student-birth" name="birth_date" type="date" lang="en-GB" dir="ltr" value="${safe(initial.birth_date || "")}" max="${new Date().toISOString().slice(0, 10)}">
        </div>
        <div class="field">
          <span class="field-label">حالة التلميذ</span>
          <label class="switch">
            <input type="checkbox" name="is_active" ${initial.is_active === false ? "" : "checked"}>
            <span class="switch-track"></span>
            <span class="switch-text"><strong>تلميذ نشط</strong><span>يظهر في المسح والاستيراد والتقارير.</span></span>
          </label>
        </div>
      </form>`;
    const dialog = A.openModal({
      title: editing ? "تعديل بيانات التلميذ" : "إضافة تلميذ جديد",
      subtitle: editing ? "عدّل المعلومات الشخصية وبيانات الاتصال." : "أدخل بيانات التلميذ يدويًا. يمكنك إضافة الصورة بعد الحفظ.",
      icon: editing ? "edit" : "plus",
      size: "lg",
      body,
      footer: `
        <button class="btn btn-secondary" type="button" data-modal-close>إلغاء</button>
        <button class="btn btn-primary" type="submit" form="student-form" data-save>${editing ? "حفظ التعديلات" : "إضافة التلميذ"}</button>`,
      onMount(modal) {
        const form = modal.querySelector("#student-form");
        const saveButton = modal.querySelector("[data-save]");
        form.addEventListener("submit", async (event) => {
          event.preventDefault();
          const values = A.formValues(form);
          const nin = String(values.student_nin || "").trim();
          const firstName = String(values.first_name || "").trim();
          const lastName = String(values.last_name || "").trim();
          if (!editing && !nin) {
            A.toast("أدخل رقم التعريف الوطني أولًا.", "warning");
            modal.querySelector("#student-nin").focus();
            return;
          }
          if (!firstName && !lastName) {
            A.toast("أدخل الاسم أو اللقب على الأقل.", "warning");
            modal.querySelector("#student-first-name").focus();
            return;
          }
          const payload = {
            first_name: firstName,
            last_name: lastName,
            class_group: String(values.class_group || "").trim(),
            parent_phone: String(values.parent_phone || "").trim(),
            birth_date: String(values.birth_date || "").trim(),
            is_active: Boolean(values.is_active),
          };
          A.setBusy(saveButton, true);
          try {
            let result;
            if (editing) {
              result = await A.api(`/api/students/${encodeURIComponent(student.student_nin)}`, { method: "PUT", body: payload });
            } else {
              payload.student_nin = nin;
              result = await A.api("/api/students", { method: "POST", body: payload });
            }
            A.toast(result.message || "تم حفظ بيانات التلميذ.", "success");
            dialog.close();
            loadStudents({ silent: false });
          } catch (error) {
            A.toast(error.message || "تعذر حفظ بيانات التلميذ.", "error");
          } finally {
            A.setBusy(saveButton, false);
          }
        });
        const firstField = modal.querySelector(editing ? "#student-first-name" : "#student-nin");
        if (firstField) window.setTimeout(() => firstField.focus(), 80);
      },
    });
    return dialog;
  }

  /* ---------------------------------------------------------------------- */
  /* Profile                                                                 */
  /* ---------------------------------------------------------------------- */

  async function openProfile(nin) {
    let data;
    try {
      data = await A.api(`/api/students/${encodeURIComponent(nin)}`);
    } catch (error) {
      A.toast(error.message || "تعذر تحميل ملف التلميذ.", "error");
      return;
    }
    const student = data.student;
    const today = data.today || { periods: [], attended_periods: 0, total_periods: 0 };
    const periods = Array.isArray(today.periods) ? today.periods : [];
    const attended = periods.filter((p) => p.status === "PRESENT" || p.status === "LATE").length;
    const absent = periods.filter((p) => p.status === "ABSENT").length;
    const excused = periods.filter((p) => p.status === "EXCUSED").length;
    const unrecorded = periods.filter((p) => !p.status).length;
    const todayRows = periods.length
      ? `<div class="activity-list">${periods
          .map(
            (period) => `
          <div class="activity-row">
            <div class="activity-main">
              <strong>${safe(period.period_name)}</strong>
              <span>${safe(period.start_time || "")}${period.scan_time ? ` · ${A.t(`مسح ${safe(period.scan_time)}`)}` : ""}</span>
            </div>
            <div class="activity-side">${A.statusBadge(period.status, period.status_label)}</div>
          </div>`
          )
          .join("")}</div>`
      : A.emptyState("clock", "لا توجد فترات نشطة", "أضف فترات من الإعدادات لعرض حضور اليوم.");
    const initials = A.initials(
      student.first_name,
      student.last_name
    );
    const avatarInner = `${safe(initials)}${student.photo_url ? `<img data-avatar-img src="${safe(student.photo_url)}" alt="${safe(student.full_name)}" loading="lazy">` : ""}`;
    const statusBadge = student.is_active
      ? '<span class="badge badge--present"><i class="badge-dot"></i>نشط</span>'
      : '<span class="badge badge--cancelled"><i class="badge-dot"></i>مؤرشف</span>';
    const toggleLabel = student.is_active ? "أرشفة التلميذ" : "تنشيط التلميذ";
    const dialog = A.openModal({
      title: "ملف التلميذ",
      subtitle: "بيانات الاتصال وحضور اليوم",
      icon: "user",
      size: "lg",
      body: `
        <div class="student-detail-head">
          <div class="avatar avatar-lg" data-profile-avatar>${avatarInner}</div>
          <div style="flex:1;min-width:0">
            <h3>${safe(student.full_name || "بدون اسم")}</h3>
            <p class="mono">${safe(student.student_nin)}</p>
            <div class="student-tags mt-1">${statusBadge}<span class="chip">${safe(displayClass(student.class_group))}</span>${
              student.parent_phone ? `<span class="chip">${A.icon("phone")}<span class="mono">${safe(student.parent_phone)}</span></span>` : '<span class="chip is-warning">بدون هاتف ولي</span>'
            }</div>
          </div>
        </div>
        <div class="student-detail-stats">
          <div><strong>${A.formatInt(attended)}</strong><span>فترات حضور اليوم</span></div>
          <div><strong>${A.formatInt(absent + excused)}</strong><span>غياب اليوم</span></div>
          <div><strong>${A.formatInt(unrecorded)}</strong><span>لم تُسجل بعد</span></div>
        </div>
        <div class="student-photo-zone mb-2">
          <div><strong>صورة التلميذ</strong><small>تُستعمل في بطاقة التعريف وشاشة البوابة. الحد الأقصى 5 ميغابايت.</small></div>
          <button class="btn btn-secondary btn-sm" type="button" data-profile-photo-btn>${A.icon("camera")}<span>رفع صورة</span></button>
          <input type="file" id="profile-photo-input" accept="image/jpeg,image/png,image/webp" hidden>
        </div>
        <div class="student-detail-meta mb-2">
          <div><span>تاريخ الميلاد</span><strong>${safe(student.birth_date ? A.formatDate(student.birth_date) : "غير مسجل")}</strong></div>
          <div><span>هاتف الولي</span><strong class="mono">${safe(student.parent_phone || "—")}</strong></div>
          <div><span>القسم / الفوج</span><strong>${safe(displayClass(student.class_group))}</strong></div>
          <div><span>فترات اليوم</span><strong>${A.formatInt(today.attended_periods)} / ${A.formatInt(today.total_periods)}</strong></div>
        </div>
        <div class="section-title" style="margin-bottom:10px"><h2 style="font-size:13px">فترات اليوم</h2><span class="chip">${safe(A.formatDate(data.day))}</span></div>
        ${todayRows}`,
      footer: `
        <button class="btn btn-${student.is_active ? "danger-soft" : "success"} btn-sm" type="button" data-profile-toggle>${A.icon(student.is_active ? "trash" : "refresh")}<span>${toggleLabel}</span></button>
        <button class="btn btn-secondary btn-sm" type="button" data-profile-edit>${A.icon("edit")}<span>تعديل البيانات</span></button>
        <a class="btn btn-secondary btn-sm" href="${printUrl(student.student_nin)}" target="_blank" rel="noopener">${A.icon("printer")}<span>طباعة البطاقة</span></a>
        <button class="btn btn-primary btn-sm" type="button" data-modal-close>إغلاق</button>`,
      onMount(modal) {
        const photoInput = modal.querySelector("#profile-photo-input");
        const photoButton = modal.querySelector("[data-profile-photo-btn]");
        photoButton.addEventListener("click", () => photoInput.click());
        photoInput.addEventListener("change", async () => {
          const file = photoInput.files && photoInput.files[0];
          if (!file) return;
          if (file.size > 5 * 1024 * 1024) {
            A.toast("حجم الصورة يتجاوز 5 ميغابايت.", "warning");
            photoInput.value = "";
            return;
          }
          const form = new FormData();
          form.append("photo", file);
          A.setBusy(photoButton, true);
          try {
            const result = await A.api(`/api/students/${encodeURIComponent(student.student_nin)}/photo`, { method: "POST", form });
            student.photo_url = result.url;
            const holder = modal.querySelector("[data-profile-avatar]");
            if (holder) {
              holder.innerHTML = `${safe(A.initials(student.first_name, student.last_name))}<img data-avatar-img src="${safe(result.url)}" alt="${safe(student.full_name)}">`;
            }
            const rowStudent = findStudent(student.student_nin);
            if (rowStudent) {
              rowStudent.photo_url = result.url;
              renderRows();
            }
            A.toast(result.message || "تم تحديث صورة التلميذ.", "success");
          } catch (error) {
            A.toast(error.message || "تعذر رفع صورة التلميذ.", "error");
          } finally {
            A.setBusy(photoButton, false);
            photoInput.value = "";
          }
        });
        modal.querySelector("[data-profile-edit]").addEventListener("click", () => {
          dialog.close();
          openStudentForm(student);
        });
        modal.querySelector("[data-profile-toggle]").addEventListener("click", () => {
          toggleStudentActive(student, dialog, true);
        });
      },
    });
  }

  /* ---------------------------------------------------------------------- */
  /* Actions                                                                 */
  /* ---------------------------------------------------------------------- */

  function toggleStudentActive(student, dialog, reopenOnCancel) {
    const activate = !student.is_active;
    A.confirm({
      title: activate ? "تنشيط التلميذ" : "أرشفة التلميذ",
      message: activate
        ? `سيُعاد ${student.full_name} إلى قائمة التلاميذ النشطين ويمكن تسجيل حضوره من جديد.`
        : `سيُخفى ${student.full_name} من قوائم المسح والاستيراد والتقارير الحالية، لكن سجلات حضوره السابقة تبقى محفوظة. يمكنك تنشيطه لاحقًا.`,
      danger: !activate,
      confirmLabel: activate ? "تنشيط" : "أرشفة",
    }).then(async (confirmed) => {
      if (!confirmed) {
        if (reopenOnCancel) openProfile(student.student_nin);
        return;
      }
      try {
        const result = await A.api(`/api/students/${encodeURIComponent(student.student_nin)}`, {
          method: "PUT",
          body: { is_active: activate },
        });
        A.toast(result.message || (activate ? "تم تنشيط التلميذ." : "تمت أرشفة التلميذ."), "success");
        if (dialog) dialog.close();
        loadStudents({ silent: Boolean(dialog) });
      } catch (error) {
        A.toast(error.message || "تعذر تحديث حالة التلميذ.", "error");
        if (reopenOnCancel) openProfile(student.student_nin);
      }
    });
  }

  /* ---------------------------------------------------------------------- */
  /* Events                                                                  */
  /* ---------------------------------------------------------------------- */

  const runSearch = A.debounce(() => {
    state.q = searchInput.value.trim();
    state.page = 1;
    loadStudents();
  }, 320);

  searchInput.addEventListener("input", runSearch);
  searchInput.addEventListener("search", () => {
    state.q = searchInput.value.trim();
    state.page = 1;
    loadStudents();
  });
  classFilter.addEventListener("change", () => {
    state.classGroup = classFilter.value;
    state.page = 1;
    loadStudents();
  });
  activeFilter.addEventListener("change", () => {
    state.active = activeFilter.value;
    state.page = 1;
    loadStudents();
  });
  perPageSelect.addEventListener("change", () => {
    state.perPage = Number(perPageSelect.value) || 20;
    state.page = 1;
    loadStudents();
  });
  $("students-add").addEventListener("click", () => openStudentForm(null));
  $("students-refresh").addEventListener("click", (event) => {
    A.setBusy(event.currentTarget, true);
    loadStudents().finally(() => A.setBusy(event.currentTarget, false));
  });

  tbody.addEventListener("click", (event) => {
    const retry = event.target.closest("[data-retry]");
    if (retry) {
      loadStudents();
      return;
    }
    const addInline = event.target.closest("[data-add-inline]");
    if (addInline) {
      openStudentForm(null);
      return;
    }
    const reset = event.target.closest("[data-reset-filters]");
    if (reset) {
      state.q = "";
      state.classGroup = "";
      state.active = "all";
      state.page = 1;
      searchInput.value = "";
      activeFilter.value = "all";
      loadStudents();
      return;
    }
    const button = event.target.closest("[data-action]");
    if (!button) return;
    const student = findStudent(button.dataset.nin);
    if (!student) return;
    const action = button.dataset.action;
    if (action === "view") openProfile(student.student_nin);
    else if (action === "edit") openStudentForm(student);
    else if (action === "toggle") toggleStudentActive(student, null, false);
  });

  window.addEventListener("popstate", () => {
    readUrl();
    loadStudents();
  });

  readUrl();
  loadStudents();
})();
