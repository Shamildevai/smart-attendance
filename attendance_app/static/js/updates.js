/* Signed update status: global banner + settings panel. */
(function () {
  "use strict";

  const A = window.App;
  const banner = document.getElementById("update-banner");
  const panel = document.getElementById("update-panel");
  const form = document.getElementById("update-form");
  const DISMISS_KEY = "attendance-update-dismissed";
  let status = null;
  let formLoaded = false;
  let pollTimer = null;

  const $ = (selector) => document.querySelector(selector);

  function stamp(value) {
    if (!value) return "—";
    const parts = String(value).split("T");
    const day = A.formatDate(parts[0]);
    const time = parts[1] ? parts[1].slice(0, 5) : "";
    return `${day}${time ? ` — ${time}` : ""}`;
  }

  function formatSize(bytes) {
    const value = Number(bytes) || 0;
    if (value < 1024 * 1024) return `${Math.max(1, Math.round(value / 1024))} ك.ب`;
    return `${(value / (1024 * 1024)).toFixed(1)} م.ب`;
  }

  function renderBanner() {
    if (!banner || !status) return;
    const dismissed = sessionStorage.getItem(DISMISS_KEY) === `${status.latest_version}:${status.staged}`;
    const visible = Boolean(status.update_available || status.installing) && !dismissed;
    banner.hidden = !visible;
    if (!visible) return;
    const version = banner.querySelector("[data-update-version]");
    const text = banner.querySelector("[data-update-banner-text]");
    if (version) version.textContent = status.latest_version ? `(${status.latest_version})` : "";
    if (text) {
      if (status.installing) {
        text.textContent = A.t("جارٍ تثبيت التحديث وإعادة تشغيل النظام. انتظر قليلًا…");
      } else if (status.staged) {
        text.textContent = A.t(`تم تنزيل الإصدار ${status.latest_version} والتحقق من توقيعه. سيُثبّت تلقائيًا عند توقف العمليات.`);
      } else if (status.auto_update) {
        text.textContent = A.t(`سيُحمّل الإصدار ${status.latest_version} ويُثبّت تلقائيًا بعد التحقق من توقيعه.`);
      } else {
        text.textContent = A.t(`الإصدار ${status.latest_version} متاح. افتح إعدادات التحديثات للمراجعة.`);
      }
    }
    banner.querySelectorAll("[data-update-install]").forEach((button) => {
      button.hidden = !status.can_install;
      button.disabled = !status.can_install || status.installing;
    });
  }

  function renderSettings() {
    if (!panel || !status) return;
    if (!formLoaded) {
      const auto = document.getElementById("auto-update");
      const feed = document.getElementById("update-feed-url");
      if (auto) auto.checked = Boolean(status.auto_update);
      if (feed && !feed.value) feed.value = status.feed_url || "";
      formLoaded = true;
    }
    const badge = document.getElementById("update-state-badge");
    if (badge) {
      let label = "محدّث";
      let cls = "badge--present";
      if (status.installing) {
        label = "جارٍ التثبيت";
        cls = "badge--late";
      } else if (status.staged) {
        label = "جاهز للتثبيت";
        cls = "badge--present";
      } else if (status.update_available) {
        label = "يتوفر تحديث";
        cls = "badge--pending";
      }
      badge.className = `badge ${cls}`;
      badge.innerHTML = `<i class="badge-dot"></i>${A.escapeHtml(A.t(label))}`;
      badge.hidden = false;
    }
    const summary = $("[data-update-summary]");
    if (summary) {
      if (!status.update_available) {
        summary.textContent = A.t("النظام يعمل بأحدث إصدار موقّع.");
      } else if (status.staged) {
        summary.textContent = A.t(`الإصدار ${status.latest_version} متاح ومُنزّل ومُتحقق منه.`);
      } else {
        summary.textContent = A.t(`الإصدار ${status.latest_version} متاح.`);
      }
    }
    const current = $("[data-update-current]");
    if (current) current.textContent = status.current_version || "—";
    const latest = $("[data-update-latest]");
    if (latest) latest.textContent = status.latest_version || "—";
    const checked = $("[data-update-checked]");
    if (checked) checked.textContent = stamp(status.last_check);
    const staged = $("[data-update-staged]");
    if (staged) {
      staged.textContent = status.staged
        ? A.t(`تم التنزيل والتحقق (${formatSize(status.download_size)})`)
        : status.update_available ? A.t("سيُنزّل تلقائيًا") : "—";
    }
    const notes = document.getElementById("update-notes");
    const notesText = $("[data-update-notes]");
    if (notes && notesText) {
      notes.hidden = !status.notes;
      notesText.textContent = status.notes || "";
    }
    const error = document.getElementById("update-error");
    const errorText = $("[data-update-error]");
    if (error && errorText) {
      error.hidden = !status.last_error;
      errorText.textContent = status.last_error || "";
    }
    const install = document.getElementById("update-install");
    if (install) {
      install.hidden = !status.staged;
      install.disabled = !status.can_install || status.installing;
      install.title = status.staged && !status.can_install
        ? "التثبيت التلقائي متاح في نسخة EXE المبنية فقط"
        : "";
    }
    const check = document.getElementById("update-check");
    if (check) check.disabled = status.installing;
  }

  function render() {
    renderBanner();
    renderSettings();
  }

  async function refresh(showErrors) {
    try {
      const data = await A.api("/api/update/status");
      status = data.update || null;
      if (status) render();
    } catch (error) {
      if (showErrors) A.toast(error.message || A.t("تعذر قراءة حالة التحديث."), "error");
    }
  }

  async function checkNow() {
    const button = document.getElementById("update-check");
    A.setBusy(button, true);
    try {
      const data = await A.api("/api/update/check", { method: "POST" });
      status = data.update || status;
      render();
      A.toast(data.message || A.t("تم فحص التحديثات."), "success", A.t("التحديثات"));
    } catch (error) {
      A.toast(error.message || A.t("تعذر فحص التحديثات."), "error");
      await refresh(false);
    } finally {
      A.setBusy(button, false);
    }
  }

  async function installNow() {
    if (!status || !status.staged) return;
    const confirmed = await A.confirm({
      title: A.t("تثبيت التحديث"),
      message: A.t(`سيتم تثبيت الإصدار ${status.latest_version} وإعادة تشغيل النظام. احفظ أي عمل جارٍ قبل المتابعة.`),
      confirmLabel: A.t("تثبيت وإعادة التشغيل"),
    });
    if (!confirmed) return;
    document.querySelectorAll("[data-update-install], #update-install").forEach((button) => {
      button.disabled = true;
    });
    try {
      const data = await A.api("/api/update/install", { method: "POST" });
      A.toast(data.message || A.t("سيتم تثبيت التحديث الآن."), "info", A.t("إعادة التشغيل"));
      if (banner) banner.hidden = false;
    } catch (error) {
      A.toast(error.message || A.t("تعذر تثبيت التحديث."), "error");
      await refresh(false);
    }
  }

  if (banner) {
    banner.querySelector("[data-update-dismiss]")?.addEventListener("click", () => {
      if (status) sessionStorage.setItem(DISMISS_KEY, `${status.latest_version}:${status.staged}`);
      banner.hidden = true;
    });
    banner.querySelectorAll("[data-update-install]").forEach((button) => {
      button.addEventListener("click", installNow);
    });
  }

  if (form) {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = document.getElementById("update-save");
      const auto = document.getElementById("auto-update");
      const feed = document.getElementById("update-feed-url");
      A.setBusy(button, true);
      try {
        await A.api("/api/settings", {
          method: "PUT",
          body: {
            auto_update: auto ? auto.checked : true,
            update_feed_url: feed ? feed.value.trim() : "",
          },
        });
        A.toast(A.t("تم حفظ إعدادات التحديث."), "success");
        await refresh(false);
      } catch (error) {
        A.toast(error.message || A.t("تعذر حفظ إعدادات التحديث."), "error");
      } finally {
        A.setBusy(button, false);
      }
    });
    document.getElementById("update-check")?.addEventListener("click", checkNow);
    document.getElementById("update-install")?.addEventListener("click", installNow);
  }

  function startPolling() {
    window.clearInterval(pollTimer);
    pollTimer = window.setInterval(() => {
      if (document.visibilityState === "visible") refresh(false);
    }, 60000);
  }

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") refresh(false);
  });

  refresh(false).finally(startPolling);
})();
