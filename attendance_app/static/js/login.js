/* Login page helpers: optional password visibility. */
(function () {
  "use strict";

  document.querySelectorAll("[data-toggle-password]").forEach((button) => {
    button.addEventListener("click", () => {
      const input = document.getElementById("login-password");
      if (!input) return;
      const showing = input.type === "text";
      input.type = showing ? "password" : "text";
      button.classList.toggle("is-active", !showing);
      button.setAttribute("aria-pressed", showing ? "false" : "true");
      input.focus({ preventScroll: true });
    });
  });
})();
