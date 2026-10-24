(function () {
  "use strict";
  var stored = null;
  try {
    stored = localStorage.getItem("attendance-theme");
  } catch (error) {
    stored = null;
  }
  var theme = stored === "dark" || stored === "light"
    ? stored
    : (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  document.documentElement.setAttribute("data-theme", theme);
})();
