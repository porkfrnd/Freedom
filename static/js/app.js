/* Freedom for Dance — front-end behaviors.
   All color/type decisions come from tokens.css; this file only wires
   behavior. No framework, ~60 lines of purpose. */

(function () {
  "use strict";

  const CSRF_COOKIE = "ffd_csrf";

  function cookieValue(name) {
    const match = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
    return match ? decodeURIComponent(match[1]) : "";
  }

  /* ── Toasts (§5.8 aria-live, §5.10 verb-matched copy) ─────────────────── */
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
    const dismiss = () => {
      if (dismissed) return;
      dismissed = true;
      el.remove();
    };
    // Auto-dismiss 4s, EXCEPT destructive-action confirmations (require
    // explicit dismissal, §5.11).
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

  /* ── CSRF-aware fetch (§8 double-submit token) ────────────────────────── */
  function csrfFetch(url, options) {
    options = options || {};
    options.headers = options.headers || {};
    options.headers["X-CSRF-Token"] = cookieValue(CSRF_COOKIE);
    if (options.body && !(options.body instanceof FormData)) {
      options.headers["Content-Type"] = "application/json";
    }
    setLoading(true);
    return fetch(url, options).finally(function () {
      setLoading(false);
    });
  }

  /* ── Equalizer loading indicator (the signature, §5.1) ────────────────── */
  let loadingDepth = 0;
  function setLoading(on) {
    loadingDepth = Math.max(0, loadingDepth + (on ? 1 : -1));
    document.body.classList.toggle("is-loading", loadingDepth > 0);
  }

  document.addEventListener("submit", function (e) {
    const form = e.target;
    if (form.dataset.noLoading) return;
    const submitter = form.querySelector('[type="submit"]');
    if (submitter) {
      submitter.disabled = true;
      submitter.classList.add("is-loading");
    }
    setLoading(true);
    // If the form is handled by fetch, the loader is released there; if it
    // navigates, the new page resets the body class anyway.
    window.setTimeout(function () {
      setLoading(false);
      if (submitter) {
        submitter.disabled = false;
        submitter.classList.remove("is-loading");
      }
    }, 4000);
  });

  /* ── Live countdowns (mono font, §5.3) ────────────────────────────────── */
  function tickCountdowns() {
    document.querySelectorAll("[data-countdown]").forEach(function (el) {
      const end = new Date(el.dataset.countdown).getTime();
      const diff = end - Date.now();
      if (diff <= 0) {
        el.textContent = "ended";
        el.classList.add("text-dim");
        return;
      }
      const s = Math.floor(diff / 1000);
      const d = Math.floor(s / 86400);
      const h = Math.floor((s % 86400) / 3600);
      const m = Math.floor((s % 3600) / 60);
      const sec = s % 60;
      const pad = (n) => String(n).padStart(2, "0");
      el.textContent =
        d > 0
          ? d + "d " + pad(h) + ":" + pad(m) + ":" + pad(sec)
          : pad(h) + ":" + pad(m) + ":" + pad(sec);
    });
  }
  setInterval(tickCountdowns, 1000);
  tickCountdowns();

  /* ── Copyable playlist command (Play in Discord, §5.11) ───────────────── */
  document.addEventListener("click", function (e) {
    const btn = e.target.closest("[data-copy]");
    if (!btn) return;
    const text = btn.dataset.copy;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(
        () => toast("Copied — paste it in Discord.", "success"),
        () => toast("Couldn't copy. Select the command and copy it manually.", "warning")
      );
    } else {
      toast("Copy isn't available here — select the command and copy it.", "info");
    }
  });

  /* ── Embed previews (Giveaway + Broadcast, §5.11) ─────────────────────── */
  function bindPreview(inputSelector, render) {
    const input = document.querySelector(inputSelector);
    if (!input) return;
    const update = () => render(input.value);
    input.addEventListener("input", update);
    update();
  }

  document.addEventListener("DOMContentLoaded", function () {
    bindPreview("#prize", (v) => {
      const el = document.querySelector("#giveaway-preview-title");
      if (el) el.textContent = v ? "\ud83c\udf89 Giveaway: " + v : "\ud83c\udf89 Giveaway: ...";
    });
    bindPreview("#ann-title", (v) => {
      const el = document.querySelector("#ann-preview-title");
      if (el) el.textContent = v || "Announcement title";
    });
    bindPreview("#ann-content", (v) => {
      const el = document.querySelector("#ann-preview-body");
      if (el) el.textContent = v || "The announcement body will preview here.";
    });
  });

  /* ── Playlist editor: drag-to-reorder + add track (§5.11) ─────────────── */
  let dragEl = null;
  document.addEventListener("dragstart", (e) => {
    const row = e.target.closest("[draggable='true']");
    if (!row) return;
    dragEl = row;
    row.classList.add("dragging");
  });
  document.addEventListener("dragover", (e) => {
    const row = e.target.closest("[draggable='true']");
    if (!row || row === dragEl) return;
    e.preventDefault();
    row.classList.add("drag-over");
  });
  document.addEventListener("dragleave", (e) => {
    const row = e.target.closest("[draggable='true']");
    if (row) row.classList.remove("drag-over");
  });
  document.addEventListener("drop", (e) => {
    e.preventDefault();
    const row = e.target.closest("[draggable='true']");
    document.querySelectorAll(".drag-over").forEach((el) => el.classList.remove("drag-over"));
    if (dragEl && row && row !== dragEl) {
      const list = row.parentElement;
      const rows = Array.from(list.querySelectorAll("[draggable='true']"));
      const from = rows.indexOf(dragEl);
      const to = rows.indexOf(row);
      if (from < to) {
        row.after(dragEl);
      } else {
        row.before(dragEl);
      }
      // Re-index hidden order inputs so the saved order matches the list.
      list.querySelectorAll("[draggable='true']").forEach((r, i) => {
        const orderInput = r.querySelector("input[name^='track_order']");
        if (orderInput) orderInput.value = String(i);
      });
    }
    if (dragEl) dragEl.classList.remove("dragging");
    dragEl = null;
  });
  document.addEventListener("dragend", () => {
    if (dragEl) dragEl.classList.remove("dragging");
    dragEl = null;
  });

  /* ── Mod log rows: expand Groq reasoning (§5.11) ──────────────────────── */
  document.addEventListener("click", function (e) {
    const btn = e.target.closest("[data-expand]");
    if (!btn) return;
    const target = document.getElementById(btn.dataset.expand);
    if (!target) return;
    const hidden = target.hidden;
    target.hidden = !hidden;
    btn.setAttribute("aria-expanded", String(hidden));
  });

  /* ── Now-playing poll: keep the equalizer badge honest ────────────────── */
  function pollNowPlaying() {
    const badge = document.querySelector("[data-now-playing]");
    if (!badge) return;
    fetch("/api/bot/now-playing")
      .then((r) => r.json())
      .then((data) => {
        const current = data && data.playlist_id;
        document.querySelectorAll("[data-playlist-card]").forEach((card) => {
          const isNow = card.dataset.playlistCard === current;
          const el = card.querySelector("[data-now-playing-badge]");
          if (el) el.hidden = !isNow;
        });
      })
      .catch(() => {});
  }
  setInterval(pollNowPlaying, 45000);

  /* ── Boot ─────────────────────────────────────────────────────────────── */
  document.addEventListener("DOMContentLoaded", function () {
    showFlashed();
    tickCountdowns();
  });
})();
