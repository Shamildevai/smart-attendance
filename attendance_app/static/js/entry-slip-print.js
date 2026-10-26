/* Thermal receipt: auto-open the print dialog once, and expose a manual button. */
(function () {
  "use strict";

  const body = document.body;
  const button = document.getElementById("receipt-print");

  function printReceipt() {
    window.print();
  }

  if (button) {
    button.addEventListener("click", printReceipt);
  }

  if (body.dataset.autoprint === "1") {
    const ready = document.fonts && document.fonts.ready
      ? document.fonts.ready
      : Promise.resolve();
    ready.then(() => window.setTimeout(printReceipt, 320));
  }
})();
