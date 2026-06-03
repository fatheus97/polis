/* Polis tester-feedback widget. Vanilla JS, no deps, idempotent.
 * Loads only when an app opts in (the dashboard injects it under TESTING_MODE).
 * Captures a console ring buffer (installed early), then on submit also grabs
 * localStorage/sessionStorage/cookies/url/viewport + an optional pasted screenshot,
 * and POSTs a multipart report to window.__POLIS_INTAKE_URL || "/api/report-intake".
 */
(function () {
  if (window.__polisFeedbackLoaded) return;
  window.__polisFeedbackLoaded = true;

  // ---- early console hook (ring buffer) ----
  var MAX = 200;
  var BUF = (window.__polisConsoleBuffer = window.__polisConsoleBuffer || []);
  function strArg(a) { try { return typeof a === "string" ? a : JSON.stringify(a); } catch (e) { return String(a); } }
  function push(level, args) {
    try {
      BUF.push({ level: level, ts: Date.now(), args: args.map(function (a) { return strArg(a).slice(0, 2000); }) });
      while (BUF.length > MAX) BUF.shift();
    } catch (e) { /* never throw into the host app */ }
  }
  ["log", "info", "warn", "error", "debug"].forEach(function (level) {
    var orig = console[level] ? console[level].bind(console) : function () {};
    console[level] = function () { push(level, [].slice.call(arguments)); return orig.apply(console, arguments); };
  });
  window.addEventListener("error", function (e) { push("uncaught", [String(e.message), (e.filename || "") + ":" + (e.lineno || "")]); });
  window.addEventListener("unhandledrejection", function (e) { push("unhandledrejection", [String(e && e.reason)]); });

  var INTAKE = window.__POLIS_INTAKE_URL || "/api/report-intake";
  var MAX_SHOT = 4 * 1024 * 1024;
  var shot = null;

  function dumpStorage(s) {
    var o = {};
    try { for (var i = 0; i < s.length && i < 200; i++) { var k = s.key(i); o[k] = (s.getItem(k) || "").slice(0, 2000); } } catch (e) {}
    return o;
  }
  function captureState() {
    return {
      console: BUF.slice(-MAX),
      localStorage: dumpStorage(window.localStorage),
      sessionStorage: dumpStorage(window.sessionStorage),
      cookies: (document.cookie || "").slice(0, 4000),
      url: location.href, userAgent: navigator.userAgent, referrer: document.referrer
    };
  }

  // ---- UI ----
  function injectStyles() {
    var css = "" +
      ".polis-fb-btn{position:fixed;right:18px;bottom:18px;z-index:2147483000;background:#818cf8;color:#fff;border:none;border-radius:999px;padding:.6rem .9rem;font:600 13px system-ui,sans-serif;cursor:pointer;box-shadow:0 4px 14px rgba(0,0,0,.3)}" +
      ".polis-fb-btn:hover{background:#6d78e0}" +
      ".polis-fb-ov{position:fixed;inset:0;z-index:2147483001;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center}" +
      ".polis-fb-modal{background:#1b1f2a;color:#e5e7eb;width:min(440px,92vw);border:1px solid #818cf8;border-radius:12px;padding:16px;font:14px system-ui,sans-serif}" +
      ".polis-fb-modal h3{margin:0 0 .5rem;font-size:15px}" +
      ".polis-fb-modal textarea{width:100%;box-sizing:border-box;min-height:90px;background:#0f1320;color:#e5e7eb;border:1px solid #394150;border-radius:8px;padding:8px;font:13px system-ui}" +
      ".polis-fb-drop{margin:.5rem 0;padding:.5rem;border:1px dashed #394150;border-radius:8px;font-size:12px;color:#94a3b8;text-align:center}" +
      ".polis-fb-drop img{max-height:90px;max-width:100%;border-radius:6px;margin-top:.4rem;display:block;margin-left:auto;margin-right:auto}" +
      ".polis-fb-row{display:flex;gap:.5rem;justify-content:flex-end;align-items:center;margin-top:.5rem}" +
      ".polis-fb-row .msg{margin-right:auto;font-size:12px;color:#94a3b8}" +
      ".polis-fb-modal button{padding:.4rem .8rem;border-radius:8px;border:1px solid #394150;background:#0f1320;color:#e5e7eb;cursor:pointer;font:600 13px system-ui}" +
      ".polis-fb-modal button.primary{background:#818cf8;border-color:#818cf8;color:#fff}";
    var s = document.createElement("style"); s.textContent = css; document.head.appendChild(s);
  }

  function el(tag, cls, html) { var e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; }

  function setShot(file) {
    if (!file) return;
    if (file.size > MAX_SHOT) { msg.textContent = "screenshot too large (max 4 MB)"; return; }
    shot = file;
    var img = drop.querySelector("img") || el("img");
    img.src = URL.createObjectURL(file);
    if (!img.parentNode) drop.appendChild(img);
    label.textContent = "screenshot attached:";
  }

  var ov, textarea, drop, label, msg;
  function open() { ov.style.display = "flex"; textarea.focus(); }
  function close() { ov.style.display = "none"; }

  function submit(btn) {
    var fd = new FormData();
    fd.append("text", textarea.value || "");
    fd.append("state", JSON.stringify(captureState()));
    fd.append("url", location.href);
    fd.append("user_agent", navigator.userAgent);
    fd.append("viewport", JSON.stringify({ w: innerWidth, h: innerHeight, dpr: window.devicePixelRatio || 1 }));
    if (shot) fd.append("screenshot", shot, shot.name || "screenshot.png");
    btn.disabled = true; msg.textContent = "sending…";
    fetch(INTAKE, { method: "POST", body: fd })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json().catch(function () { return {}; }); })
      .then(function () { msg.textContent = "sent ✓"; textarea.value = ""; shot = null; setTimeout(close, 700); })
      .catch(function (e) { msg.textContent = "failed: " + e.message + " (not lost — try again)"; })
      .finally(function () { btn.disabled = false; });
  }

  function build() {
    injectStyles();
    var btn = el("button", "polis-fb-btn", "🐞 Feedback");
    btn.onclick = open;
    document.body.appendChild(btn);

    ov = el("div", "polis-fb-ov"); ov.style.display = "none";
    var modal = el("div", "polis-fb-modal");
    modal.appendChild(el("h3", null, "Report an issue or improvement"));
    textarea = el("textarea"); textarea.placeholder = "Describe the issue / what you'd like changed…";
    modal.appendChild(textarea);
    drop = el("div", "polis-fb-drop");
    label = el("div", null, "Paste (Ctrl+V) or attach a screenshot");
    drop.appendChild(label);
    var file = el("input"); file.type = "file"; file.accept = "image/*";
    file.onchange = function () { setShot(file.files && file.files[0]); };
    drop.appendChild(file);
    modal.appendChild(drop);
    var row = el("div", "polis-fb-row");
    msg = el("span", "msg");
    var cancel = el("button", null, "Cancel"); cancel.onclick = close;
    var send = el("button", "primary", "Submit"); send.onclick = function () { submit(send); };
    row.appendChild(msg); row.appendChild(cancel); row.appendChild(send);
    modal.appendChild(row);
    ov.appendChild(modal);
    ov.addEventListener("click", function (e) { if (e.target === ov) close(); });
    document.body.appendChild(ov);

    // paste a screenshot anywhere while the modal is open
    window.addEventListener("paste", function (e) {
      if (ov.style.display === "none") return;
      var items = (e.clipboardData && e.clipboardData.items) || [];
      for (var i = 0; i < items.length; i++) {
        if (items[i].type && items[i].type.indexOf("image") === 0) { setShot(items[i].getAsFile()); e.preventDefault(); break; }
      }
    });
    drop.addEventListener("dragover", function (e) { e.preventDefault(); });
    drop.addEventListener("drop", function (e) { e.preventDefault(); if (e.dataTransfer && e.dataTransfer.files[0]) setShot(e.dataTransfer.files[0]); });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", build);
  else build();
})();
