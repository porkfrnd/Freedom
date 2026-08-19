/* Freedom for Dance — front-end behaviors.
   No framework, no Discord. Just what the page needs. */

(function () {
  "use strict";

  const CSRF_COOKIE = "ffd_csrf";

  function cookieValue(name) {
    const match = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
    return match ? decodeURIComponent(match[1]) : "";
  }

  /* ── Toasts ──────────────────────────────────────────────────────────── */
  function toast(message, type) {
    type = type || "info";
    const region = document.getElementById("toast-region");
    if (!region) return;
    const el = document.createElement("div");
    el.className = "toast toast--" + type;
    el.setAttribute("role", "status");
    el.setAttribute("aria-live", type === "danger" || type === "error" ? "assertive" : "polite");
    el.textContent = message;
    region.appendChild(el);
    let dismissed = false;
    const dismiss = () => { if (!dismissed) { dismissed = true; el.remove(); } };
    if (type !== "danger" && type !== "error") {
      setTimeout(dismiss, 4000);
    } else {
      const btn = document.createElement("button");
      btn.className = "toast--dismiss";
      btn.setAttribute("aria-label", "Dismiss");
      btn.textContent = "\u00d7";
      btn.addEventListener("click", dismiss);
      el.appendChild(btn);
    }
  }

  function showFlashed() {
    const flashed = window.__flashed;
    if (!flashed) return;
    flashed.forEach(function (pair) {
      toast(pair[1], pair[0] === "message" ? "info" : pair[0]);
    });
  }

  /* ── CSRF-aware fetch ────────────────────────────────────────────────── */
  window.csrfFetch = function (url, options) {
    options = options || {};
    options.headers = options.headers || {};
    options.headers["X-CSRF-Token"] = cookieValue(CSRF_COOKIE);
    if (options.body && !(options.body instanceof FormData)) {
      options.headers["Content-Type"] = "application/json";
    }
    return fetch(url, options);
  };

  /* ── Copy to clipboard ───────────────────────────────────────────────── */
  document.addEventListener("click", function (e) {
    const btn = e.target.closest("[data-copy]");
    if (!btn) return;
    const text = btn.dataset.copy;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(
        () => toast("Copied.", "success"),
        () => toast("Couldn't copy — select and copy manually.", "warning")
      );
    } else {
      toast("Copy isn't available here.", "info");
    }
  });

  /* ── Boot ────────────────────────────────────────────────────────────── */
  document.addEventListener("DOMContentLoaded", function () {
    showFlashed();
  });
})();
