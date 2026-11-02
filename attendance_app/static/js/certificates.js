/* School certificates: template editor, student lookup and certificate data. */
(function () {
  "use strict";

  const A = window.App;
  const $ = (id) => document.getElementById(id);

  const form = $("cert-template-form");
  const layout = $("cert-layout");
  const officialBlock = $("cert-official-block");
  const customBlock = $("cert-custom-block");

  function syncLayout() {
    const custom = layout.value === "custom";
    officialBlock.hidden = custom;
    customBlock.hidden = !custom;
  }

  if (form && layout) {
    syncLayout();
    layout.addEventListener("change", syncLayout);

    let lastField = form.querySelector('[name="body"]');
    form.addEventListener("focusin", (event) => {
      if (event.target instanceof HTMLTextAreaElement) lastField = event.target;
    });
    form.querySelectorAll("[data-cert-token]").forEach((button) => {
      button.addEventListener("click", () => {
        const token = "{{" + button.dataset.certToken + "}}";
        const field = lastField || form.querySelector('[name="body"]');
        const start = field.selectionStart === null ? field.value.length : field.selectionStart;
        const end = field.selectionEnd === null ? field.value.length : field.selectionEnd;
        field.value = field.value.slice(0, start) + token + field.value.slice(end);
        field.focus();
        field.setSelectionRange(start + token.length, start + token.length);
      });
    });

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = $("cert-save-template");
      const extra = {};
      [
        "republic_header", "ministry_header", "directorate", "institution",
        "institution_type", "cycle_word", "state", "signature_label",
        "cert_line", "previous_name_label", "issued_line",
        "registration_current", "registration_exit",
      ].forEach((name) => {
        const field = form.querySelector(`[name="${name}"]`);
        if (field) extra[name] = field.value.trim();
      });
      extra.show_barcode = form.querySelector('[name="show_barcode"]').checked;
      extra.show_qr = form.querySelector('[name="show_qr"]').checked;
      extra.show_stamp = form.querySelector('[name="show_stamp"]').checked;
      A.setBusy(button, true);
      try {
        const data = await A.api("/api/certificates/settings", {
          method: "POST",
          body: {
            layout: layout.value,
            direction: form.querySelector('[name="direction"]').value,
            title: form.querySelector('[name="title"]').value,
            body: form.querySelector('[name="body"]').value,
            footer: form.querySelector('[name="footer"]').value,
            extra: extra,
          },
        });
        A.toast(data.message || "حُفظ قالب الشهادة.", "success");
        const chip = $("cert-template-chip");
        if (chip) {
          chip.textContent = "قالب مخصص";
          chip.classList.add("is-accent");
        }
      } catch (error) {
        A.toast(error.message || "تعذر حفظ القالب.", "error");
      } finally {
        A.setBusy(button, false);
      }
    });
  }

  const search = $("cert-search");
  const results = $("cert-results");
  const selected = $("cert-selected");
  const empty = $("cert-empty");
  const profilePanel = $("cert-profile");
  const printLink = $("cert-print-link");
  let current = null;

  function renderSelected(student) {
    current = student;
    empty.hidden = true;
    selected.hidden = false;
    selected.innerHTML = `<div class="cert-selected">
      <div>
        <strong>${A.escapeHtml(student.full_name)}</strong>
        <span>${A.escapeHtml(student.class_group || "بدون قسم")} · ${A.escapeHtml(student.student_nin)}</span>
      </div>
      <span class="chip ${student.is_active ? "is-accent" : ""}">${student.is_active ? "نشط" : "مؤرشف"}</span>
    </div>`;
    profilePanel.hidden = false;
    printLink.href = `/certificates/print/${encodeURIComponent(student.student_nin)}`;
    loadProfile(student.student_nin);
  }

  function fillProfile(profile) {
    $("cert-gender").value = profile.gender || "";
    $("cert-birth-place").value = profile.birth_place || "";
    $("cert-latin-last").value = profile.latin_last_name || "";
    $("cert-latin-first").value = profile.latin_first_name || "";
    $("cert-level").value = profile.level || "";
    $("cert-section").value = profile.section || "";
    $("cert-registration").value = profile.registration_no || "";
    $("cert-exit-date").value = profile.exit_date || "";
  }

  async function loadProfile(nin) {
    try {
      const data = await A.api(`/api/certificates/students/${encodeURIComponent(nin)}/profile`);
      fillProfile(data.profile || {});
    } catch (error) {
      A.toast(error.message || "تعذر تحميل بيانات الشهادة.", "error");
    }
  }

  if (search && results && selected) {
    const runSearch = A.debounce(async () => {
      const query = search.value.trim();
      if (query.length < 1) {
        results.hidden = true;
        results.innerHTML = "";
        return;
      }
      try {
        const data = await A.api(A.apiUrl("/api/certificates/students", { q: query }));
        const students = data.students || [];
        results.hidden = false;
        results.innerHTML = students.length
          ? students.map((student) => `<button class="cert-result" type="button" data-pick="${A.escapeHtml(student.student_nin)}">
              <strong>${A.escapeHtml(student.full_name)}</strong>
              <span>${A.escapeHtml(student.class_group || "بدون قسم")} · ${A.escapeHtml(student.student_nin)}</span>
            </button>`).join("")
          : '<div class="cert-empty" style="margin:6px">لا يوجد تلميذ يطابق البحث.</div>';
        results.querySelectorAll("[data-pick]").forEach((button) => {
          button.addEventListener("click", () => {
            const student = students.find((row) => row.student_nin === button.dataset.pick);
            if (!student) return;
            results.hidden = true;
            results.innerHTML = "";
            search.value = "";
            renderSelected(student);
          });
        });
      } catch (error) {
        results.hidden = false;
        results.innerHTML = `<div class="cert-empty" style="margin:6px">${A.escapeHtml(error.message || "تعذر البحث.")}</div>`;
      }
    }, 260);
    search.addEventListener("input", runSearch);

    document.addEventListener("click", (event) => {
      if (!results.hidden && !results.contains(event.target) && event.target !== search) {
        results.hidden = true;
      }
    });

    const profileButton = $("cert-save-profile");
    profileButton.addEventListener("click", async () => {
      if (!current) return;
      A.setBusy(profileButton, true);
      try {
        await A.api(`/api/certificates/students/${encodeURIComponent(current.student_nin)}/profile`, {
          method: "POST",
          body: {
            gender: $("cert-gender").value,
            birth_place: $("cert-birth-place").value,
            latin_last_name: $("cert-latin-last").value,
            latin_first_name: $("cert-latin-first").value,
            level: $("cert-level").value,
            section: $("cert-section").value,
            registration_no: $("cert-registration").value,
            exit_date: $("cert-exit-date").value,
          },
        });
        A.toast("حُفظت بيانات الشهادة.", "success");
      } catch (error) {
        A.toast(error.message || "تعذر حفظ البيانات.", "error");
      } finally {
        A.setBusy(profileButton, false);
      }
    });

    const initial = selected.dataset.initial;
    if (initial) {
      A.api(A.apiUrl("/api/certificates/students", { q: initial }))
        .then((data) => {
          const student = (data.students || []).find((row) => row.student_nin === initial);
          if (student) renderSelected(student);
        })
        .catch(() => {});
    }
  }
})();
