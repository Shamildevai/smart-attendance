/* Phone-as-scanner bridge: polls the desktop API which pulls scans from the Android app. */
(function () {
  "use strict";

  const POLL_MS = 1000;
  const RETRY_MS = 450;

  const state = {
    running: false,
    timer: null,
    busy: false,
    pumping: false,
    queue: [],
    callbacks: null,
    toggle: null,
    note: null,
    label: null,
    lastState: "",
  };

  function t(value) {
    return window.App && typeof window.App.t === "function" ? window.App.t(value) : value;
  }

  function setNote(message, tone) {
    if (!state.note) return;
    state.note.hidden = !message;
    state.note.textContent = message || "";
    state.note.classList.toggle("is-error", tone === "error");
    state.note.classList.toggle("is-ok", tone === "ok");
  }

  function stateMessage(data) {
    const pending = Number(data.pending || 0);
    switch (data.state) {
      case "ok":
        return pending > 0
          ? { text: "جارٍ نقل المسوحات من الهاتف…", tone: "" }
          : { text: "الهاتف متصل — جاهز للمسح", tone: "ok" };
      case "offline":
        return {
          text: "تعذر الوصول إلى الهاتف. تأكد من الواي فاي وتشغيل البوابة.",
          tone: "error",
        };
      case "not_configured":
        return {
          text: "لم يُضبط رابط بوابة الهاتف بعد. اضبطه من صفحة الرسائل.",
          tone: "error",
        };
      case "invalid_url":
        return { text: "رابط بوابة الهاتف غير صالح.", tone: "error" };
      case "unauthorized":
        return { text: "مفتاح واجهة الهاتف غير صحيح. أعد الاقتران.", tone: "error" };
      case "outdated":
        return {
          text: "حدّث تطبيق الهاتف إلى الإصدار 1.1.0 لتفعيل القارئ اللاسلكي.",
          tone: "error",
        };
      case "resynced":
        return {
          text: "أُعيدت مزامنة المسوحات بعد إعادة تشغيل الهاتف.",
          tone: "",
        };
      case "partial":
        return {
          text: "بعض هواتف المسح غير متصلة — ما زال المسح يعمل من الهواتف المتصلة.",
          tone: "error",
        };
      case "error":
        return { text: "تعذر قراءة المسوحات من الهاتف.", tone: "error" };
      default:
        return null;
    }
  }

  function renderState(data) {
    if (!state.running || !data) return;
    if (data.state === "busy") return;
    if (data.state !== state.lastState) {
      state.lastState = data.state;
      const message = stateMessage(data);
      if (message) setNote(t(message.text), message.tone);
    } else if (data.state === "ok") {
      const message = stateMessage(data);
      if (message) setNote(t(message.text), message.tone);
    }
  }

  function renderButton() {
    if (!state.toggle) return;
    state.toggle.classList.toggle("is-on", state.running);
    if (state.label) {
      state.label.textContent = t(state.running ? "إيقاف قارئ الهاتف" : "قارئ الهاتف");
    }
    state.toggle.setAttribute("aria-pressed", state.running ? "true" : "false");
  }

  async function poll() {
    if (!state.running || state.busy || document.hidden) return;
    state.busy = true;
    let data = null;
    try {
      const response = await fetch("/api/scanner/bridge/poll", {
        headers: { Accept: "application/json" },
        cache: "no-store",
        credentials: "same-origin",
      });
      if (response.ok) data = await response.json();
    } catch (error) {
      data = { ok: false, state: "offline" };
    }
    state.busy = false;
    if (!state.running) return;
    if (data && data.ok) {
      renderState(data);
      for (const scan of data.scans || []) {
        if (scan && scan.code) enqueue(String(scan.code));
      }
    }
  }

  function enqueue(code) {
    if (state.queue[state.queue.length - 1] !== code) state.queue.push(code);
    pump();
  }

  async function pump() {
    if (state.pumping || !state.running) return;
    state.pumping = true;
    while (state.running && state.queue.length) {
      const code = state.queue[0];
      let handled = true;
      try {
        handled = await state.callbacks.onCode(code);
      } catch (error) {
        handled = true;
      }
      if (!state.running) break;
      if (handled === false) {
        await new Promise((resolve) => window.setTimeout(resolve, RETRY_MS));
        continue;
      }
      state.queue.shift();
    }
    state.pumping = false;
  }

  function start(options) {
    if (state.running) return;
    state.running = true;
    state.callbacks = options;
    state.lastState = "";
    setNote(t("جارٍ الاتصال بالهاتف…"), "");
    renderButton();
    poll();
    state.timer = window.setInterval(poll, POLL_MS);
  }

  function stop() {
    state.running = false;
    state.queue = [];
    window.clearInterval(state.timer);
    state.timer = null;
    if (state.note) {
      state.note.hidden = true;
      state.note.textContent = "";
    }
    renderButton();
  }

  function mount(options) {
    state.toggle = document.getElementById(options.toggleId);
    state.note = document.getElementById(options.noteId);
    if (!state.toggle) return;
    state.label = state.toggle.querySelector("span");
    renderButton();
    state.toggle.addEventListener("click", () => {
      if (state.running) {
        stop();
      } else {
        start(options);
      }
    });
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && state.running) poll();
    });
  }

  window.PhoneScanner = {
    mount,
    stop,
    isRunning: () => state.running,
  };
})();
