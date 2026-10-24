/* User accounts management (admin only). */
(function () {
  "use strict";

  const A = window.App;
  const tbody = document.getElementById("users-body");
  const currentUserId = Number(document.body.dataset.userId || 0);
  let users = [];

  const USERNAME_RE = /^[A-Za-z0-9_.-]{3,32}$/;

  function roleLabel(role) {
    return A.t(role === "admin" ? "مدير" : "مستخدم");
  }

  function roleBadge(role) {
    const cls = role === "admin" ? "badge--admin" : "badge--user";
    return `<span class="badge ${cls}"><i class="badge-dot"></i>${A.escapeHtml(roleLabel(role))}</span>`;
  }

  function passwordBadge(hasPassword) {
    return hasPassword
      ? `<span class="badge badge--sent"><i class="badge-dot"></i>${A.escapeHtml(A.t("مضبوطة"))}</span>`
      : `<span class="badge badge--pending"><i class="badge-dot"></i>${A.escapeHtml(A.t("بدون كلمة مرور"))}</span>`;
  }

  function formatLastLogin(value) {
    if (!value) return A.t("لم يدخل بعد");
    const text = String(value).slice(0, 16).replace("T", " ");
    return text || A.t("لم يدخل بعد");
  }

  function initial(user) {
    const value = String(user.display_name || user.username || "?").trim();
    return value.slice(0, 1).toUpperCase();
  }

  function render() {
    document.getElementById("users-total").textContent = A.formatInt(users.length);
    document.getElementById("users-admins").textContent = A.formatInt(
      users.filter((user) => user.role === "admin" && user.is_active).length
    );
    document.getElementById("users-active").textContent = A.formatInt(
      users.filter((user) => user.is_active).length
    );

    const openAdmin = users.some(
      (user) => user.role === "admin" && user.is_active && !user.has_password
    );
    const warning = document.getElementById("users-open-admin");
    if (warning) warning.hidden = !openAdmin;

    if (!users.length) {
      tbody.innerHTML = `<tr><td colspan="6">${A.emptyState("users", "لا يوجد مستخدمون", "أضف أول حساب للدخول إلى النظام.")}</td></tr>`;
      return;
    }

    tbody.innerHTML = users
      .map((user) => {
        const isSelf = user.id === currentUserId;
        return `<tr class="${user.is_active ? "" : "is-disabled"}">
          <td>
            <div class="cell-main">
              <span class="user-avatar">${A.escapeHtml(initial(user))}</span>
              <div>
                <strong>${A.escapeHtml(user.display_name)}</strong>
                <small>@${A.escapeHtml(user.username)}${isSelf ? ` · ${A.escapeHtml(A.t("حسابك"))}` : ""}</small>
              </div>
            </div>
          </td>
          <td>${roleBadge(user.role)}</td>
          <td>${passwordBadge(user.has_password)}</td>
          <td>${user.is_active
            ? `<span class="badge badge--present"><i class="badge-dot"></i>${A.escapeHtml(A.t("نشط"))}</span>`
            : `<span class="badge badge--cancelled"><i class="badge-dot"></i>${A.escapeHtml(A.t("معطل"))}</span>`}</td>
          <td class="muted tiny">${A.escapeHtml(formatLastLogin(user.last_login_at))}</td>
          <td class="actions">
            <button class="icon-btn" type="button" data-edit-user="${user.id}" title="${A.escapeHtml(A.t("تعديل"))}" aria-label="${A.escapeHtml(A.t("تعديل"))}">${A.icon("edit")}</button>
            <button class="icon-btn is-danger" type="button" data-delete-user="${user.id}" title="${A.escapeHtml(A.t("حذف"))}" aria-label="${A.escapeHtml(A.t("حذف"))}" ${isSelf ? "disabled" : ""}>${A.icon("trash")}</button>
          </td>
        </tr>`;
      })
      .join("");
  }

  async function load() {
    try {
      const data = await A.api("/api/users");
      users = data.users || [];
      render();
    } catch (error) {
      tbody.innerHTML = `<tr><td colspan="6">${A.emptyState("warning", A.t("تعذر تحميل المستخدمين"), error.message)}</td></tr>`;
      A.toast(error.message, "error");
    }
  }

  function formHtml(user) {
    const isSelf = Boolean(user && user.id === currentUserId);
    return `<form class="form-grid user-form" data-user-form>
      <label class="field">
        <span>${A.t("اسم المستخدم")}</span>
        <input data-field="username" value="${user ? A.escapeHtml(user.username) : ""}" ${user ? "readonly" : ""}
               required spellcheck="false" autocomplete="off" placeholder="admin">
        <small class="tiny muted">${A.t("3 إلى 32 حرفًا لاتينيًا أو رقمًا أو . _ -")}</small>
      </label>
      <label class="field">
        <span>${A.t("الاسم المعروض")}</span>
        <input data-field="display_name" value="${user ? A.escapeHtml(user.display_name) : ""}" required maxlength="80">
      </label>
      <label class="field">
        <span>${A.t("الدور")}</span>
        <select data-field="role" ${isSelf ? "disabled" : ""}>
          <option value="user" ${user && user.role === "user" ? "selected" : ""}>${A.t("مستخدم")}</option>
          <option value="admin" ${user && user.role === "admin" ? "selected" : ""}>${A.t("مدير")}</option>
        </select>
      </label>
      <label class="field">
        <span>${A.t("كلمة المرور (اختيارية)")}</span>
        <input type="password" data-field="password" autocomplete="new-password" placeholder="${A.t("اتركها فارغة لعدم التغيير")}">
        <small class="tiny muted">${A.t("لا تقل عن 6 أحرف عند ضبطها.")}</small>
      </label>
      ${user ? `<label class="check-row"><input type="checkbox" data-field="clear_password"> <span>${A.t("حذف كلمة المرور الحالية")}</span></label>` : ""}
      <label class="check-row"><input type="checkbox" data-field="is_active" ${!user || user.is_active ? "checked" : ""} ${isSelf ? "disabled" : ""}> <span>${A.t("الحساب نشط ويستطيع الدخول")}</span></label>
      ${isSelf ? `<p class="tiny muted">${A.t("لا يمكنك تغيير دور حسابك أو تعطيله من هنا.")}</p>` : ""}
    </form>`;
  }

  function openUserForm(user) {
    const isEdit = Boolean(user);
    const form = A.openModal({
      title: isEdit ? A.t("تعديل المستخدم") : A.t("إضافة مستخدم"),
      subtitle: isEdit ? `@${user.username}` : A.t("أنشئ حسابًا جديدًا بصلاحية مدير أو مستخدم."),
      icon: "user",
      body: formHtml(user),
      footer: `<button class="btn btn-secondary" type="button" data-modal-close>${A.t("إلغاء")}</button>
               <button class="btn btn-primary" type="button" data-save-user>${A.t("حفظ")}</button>`,
    });
    const modal = form.element;
    const passwordInput = modal.querySelector('[data-field="password"]');
    const clearInput = modal.querySelector('[data-field="clear_password"]');
    if (clearInput && passwordInput) {
      clearInput.addEventListener("change", () => {
        passwordInput.disabled = clearInput.checked;
        if (clearInput.checked) passwordInput.value = "";
      });
    }

    modal.querySelector("[data-save-user]").addEventListener("click", async (event) => {
      const button = event.currentTarget;
      const username = modal.querySelector('[data-field="username"]').value.trim();
      const displayName = modal.querySelector('[data-field="display_name"]').value.trim();
      const role = modal.querySelector('[data-field="role"]').value;
      const password = passwordInput ? passwordInput.value : "";
      const clearPassword = Boolean(clearInput && clearInput.checked);
      const isActiveInput = modal.querySelector('[data-field="is_active"]');
      const isActive = Boolean(isActiveInput && isActiveInput.checked);

      if (!isEdit && !USERNAME_RE.test(username)) {
        A.toast(A.t("اسم المستخدم يجب أن يكون بين 3 و32 حرفًا لاتينيًا أو رقمًا أو . _ -"), "warning");
        return;
      }
      if (!displayName) {
        A.toast(A.t("أدخل الاسم المعروض."), "warning");
        return;
      }
      if (password && password.length < 6) {
        A.toast(A.t("كلمة المرور يجب ألا تقل عن 6 أحرف."), "warning");
        return;
      }

      A.setBusy(button, true);
      try {
        if (isEdit) {
          await A.api(`/api/users/${user.id}`, {
            method: "PUT",
            body: {
              display_name: displayName,
              role,
              password: password || null,
              clear_password: clearPassword,
              is_active: isActive,
            },
          });
          A.toast(A.t("تم تحديث بيانات المستخدم."), "success");
        } else {
          await A.api("/api/users", {
            method: "POST",
            body: {
              username,
              display_name: displayName,
              role,
              password: password || null,
              is_active: isActive,
            },
          });
          A.toast(A.t("تم إنشاء المستخدم بنجاح."), "success");
        }
        form.close(true);
        await load();
      } catch (error) {
        A.toast(error.message, "error");
      } finally {
        A.setBusy(button, false);
      }
    });
  }

  tbody.addEventListener("click", (event) => {
    const editButton = event.target.closest("[data-edit-user]");
    if (editButton) {
      const user = users.find((entry) => entry.id === Number(editButton.dataset.editUser));
      if (user) openUserForm(user);
      return;
    }
    const deleteButton = event.target.closest("[data-delete-user]");
    if (deleteButton) {
      const user = users.find((entry) => entry.id === Number(deleteButton.dataset.deleteUser));
      if (!user) return;
      A.confirm({
        title: A.t("حذف المستخدم"),
        message: A.t(`هل تريد حذف «${user.display_name}»؟ لن يستطيع الدخول بعد الآن.`),
        danger: true,
        confirmLabel: A.t("حذف"),
      }).then(async (confirmed) => {
        if (!confirmed) return;
        try {
          await A.api(`/api/users/${user.id}`, { method: "DELETE" });
          A.toast(A.t("تم حذف المستخدم."), "success");
          await load();
        } catch (error) {
          A.toast(error.message, "error");
        }
      });
    }
  });

  document.querySelectorAll("[data-add-user]").forEach((button) => {
    button.addEventListener("click", () => openUserForm(null));
  });
  document.querySelectorAll("[data-refresh-users]").forEach((button) => {
    button.addEventListener("click", load);
  });

  load();
})();
