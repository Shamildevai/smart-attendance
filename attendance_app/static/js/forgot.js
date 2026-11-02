/* Password recovery form: format the recovery code and check the confirmation. */
(function () {
  "use strict";

  const A = window.App;
  const form = document.getElementById("forgot-form");
  if (!form) return;

  const code = document.getElementById("forgot-code");
  const password = document.getElementById("forgot-password");
  const confirm = document.getElementById("forgot-confirm");

  code.addEventListener("input", () => {
    const raw = code.value.toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 12);
    code.value = raw.replace(/(.{4})(?=.)/g, "$1-");
  });

  form.addEventListener("submit", (event) => {
    if (confirm.value !== password.value) {
      event.preventDefault();
      confirm.setCustomValidity(A.t("كلمتا المرور غير متطابقتين."));
      confirm.reportValidity();
      A.toast(A.t("كلمتا المرور غير متطابقتين."), "warning");
      return;
    }
    confirm.setCustomValidity("");
  });

  confirm.addEventListener("input", () => confirm.setCustomValidity(""));
})();
