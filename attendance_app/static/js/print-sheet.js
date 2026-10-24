(function () {
  "use strict";

  var body = document.body;
  var autoPrint = body.getAttribute("data-autoprint") === "1";
  var printed = false;

  function printNow() {
    if (printed) {
      return;
    }
    printed = true;
    try {
      window.focus();
      window.print();
    } finally {
      window.setTimeout(function () {
        printed = false;
      }, 800);
    }
  }

  function imagesSettled() {
    var images = Array.prototype.slice.call(document.images);
    return Promise.all(
      images.map(function (image) {
        if (image.complete) {
          return Promise.resolve();
        }
        return new Promise(function (resolve) {
          image.addEventListener("load", resolve, { once: true });
          image.addEventListener("error", resolve, { once: true });
        });
      })
    );
  }

  function fontsReady() {
    if (!document.fonts || !document.fonts.ready) {
      return Promise.resolve();
    }
    return document.fonts.ready.catch(function () {
      return undefined;
    });
  }

  var printButton = document.getElementById("print-now");
  if (printButton) {
    printButton.addEventListener("click", function () {
      window.print();
    });
  }

  document.addEventListener("keydown", function (event) {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "p") {
      event.preventDefault();
      printNow();
    }
  });

  if (!autoPrint) {
    return;
  }

  var timeout = new Promise(function (resolve) {
    window.setTimeout(resolve, 4000);
  });

  Promise.race([Promise.all([imagesSettled(), fontsReady()]), timeout]).then(function () {
    window.setTimeout(printNow, 220);
  });
})();
