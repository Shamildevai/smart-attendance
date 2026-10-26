/* Verify page helpers: focus the payload field for USB QR scanners. */
(function () {
  "use strict";

  const field = document.getElementById("verify-code");
  const clear = document.getElementById("verify-clear");
  const focusButton = document.getElementById("verify-scan-focus");

  if (clear && field) {
    clear.addEventListener("click", () => {
      field.value = "";
      field.focus();
    });
  }

  if (focusButton && field) {
    focusButton.addEventListener("click", () => {
      field.focus();
      field.select();
    });
  }

  if (field && !field.value.trim()) {
    field.focus();
  }
})();
