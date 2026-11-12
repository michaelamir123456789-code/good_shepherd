/* ==========================================================================
   الراعي الصالح — client-side helpers.
   Vanilla JS, no dependencies, CSP-safe (no inline handlers, no eval).
   Everything is opt-in through data-* attributes.
   ========================================================================== */
(function () {
  "use strict";

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var csrf = function () { var m = $('meta[name="csrf-token"]'); return m ? m.content : ""; };

  /* ---- flash toasts ---------------------------------------------------- */
  function dismissToast(el) {
    el.classList.add("out");
    setTimeout(function () { el.remove(); }, 260);
  }
  $$(".toast").forEach(function (el) {
    var x = $(".x", el);
    if (x) x.addEventListener("click", function () { dismissToast(el); });
    var ttl = el.classList.contains("t-danger") ? 9000 : 5500;
    setTimeout(function () { if (el.isConnected) dismissToast(el); }, ttl);
  });

  /* ---- confirm dialogs: <form data-confirm="..."> / <button data-confirm> */
  document.addEventListener("submit", function (e) {
    var f = e.target, msg = f.getAttribute && f.getAttribute("data-confirm");
    if (msg && !window.confirm(msg)) { e.preventDefault(); return; }
    if (f.hasAttribute && f.hasAttribute("data-once")) {
      $$('button[type="submit"],input[type="submit"]', f).forEach(function (b) {
        setTimeout(function () { if (e.defaultPrevented) return; b.disabled = true; b.classList.add("disabled"); }, 0);
      });
    }
  }, true);

  /* ---- generic click actions ------------------------------------------- */
  document.addEventListener("click", function (e) {
    var t = e.target.closest("[data-print],[data-toggle-password],[data-generate-password],[data-sheet-open],[data-sheet-close]");
    if (!t) return;

    if (t.hasAttribute("data-print")) { window.print(); return; }

    if (t.hasAttribute("data-toggle-password")) {
      var inp = $(t.getAttribute("data-toggle-password"));
      if (inp) {
        var show = inp.type === "password";
        inp.type = show ? "text" : "password";
        t.setAttribute("aria-pressed", show ? "true" : "false");
        var on = $(".eye-on", t), off = $(".eye-off", t);
        if (on && off) { on.classList.toggle("hidden", show); off.classList.toggle("hidden", !show); }
      }
      return;
    }

    if (t.hasAttribute("data-generate-password")) {
      var target = $(t.getAttribute("data-generate-password"));
      if (target) {
        target.value = generatePassword(14);
        target.type = "text";
        target.dispatchEvent(new Event("input", { bubbles: true }));
      }
      return;
    }

    if (t.hasAttribute("data-sheet-open")) { setSheet(true); return; }
    if (t.hasAttribute("data-sheet-close")) { setSheet(false); return; }
  });

  /* ---- mobile "more" sheet ---------------------------------------------- */
  function setSheet(open) {
    var s = $("#more-sheet"), b = $("#sheet-backdrop");
    if (!s || !b) return;
    s.classList.toggle("open", open);
    b.classList.toggle("open", open);
  }

  /* ---- dropdown menus: close on outside click / Esc --------------------- */
  document.addEventListener("click", function (e) {
    $$("details.menu[open]").forEach(function (d) { if (!d.contains(e.target)) d.removeAttribute("open"); });
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      $$("details.menu[open]").forEach(function (d) { d.removeAttribute("open"); });
      setSheet(false);
    }
  });

  /* ---- CSS-variable driven bars: data-pct="72" -------------------------- */
  $$("[data-pct]").forEach(function (el) {
    el.style.setProperty("--pct", Math.max(0, Math.min(100, parseFloat(el.getAttribute("data-pct")) || 0)));
  });

  /* ---- photo lightbox: tap a kid's photo to see it big ------------------- */
  document.addEventListener("click", function (e) {
    var img = e.target.closest("img[data-zoom]");
    if (!img) return;
    var box = document.createElement("div");
    box.className = "lightbox";
    var fig = document.createElement("figure");
    var big = document.createElement("img");
    big.src = img.src; big.alt = "";
    var cap = document.createElement("figcaption");
    cap.textContent = img.getAttribute("data-name") || "";
    fig.appendChild(big); fig.appendChild(cap); box.appendChild(fig);
    var close = function () { box.remove(); document.removeEventListener("keydown", onKey); };
    var onKey = function (ev) { if (ev.key === "Escape") close(); };
    box.addEventListener("click", close);
    document.addEventListener("keydown", onKey);
    document.body.appendChild(box);
  });

  /* ---- sign-up: choosing a grade reveals that grade's admins ---------------- */
  $$("[data-grade-select]").forEach(function (sel) {
    var wrap = $(sel.getAttribute("data-grade-select"));
    if (!wrap) return;
    var picked = wrap.getAttribute("data-selected") || "";
    var sync = function () {
      $$("[data-grade-group]", wrap).forEach(function (g) {
        var show = g.getAttribute("data-grade-group") === sel.value;
        g.classList.toggle("hidden", !show);
        var radios = $$("input[type=radio]", g), match = null;
        radios.forEach(function (r) { r.disabled = !show; if (r.value === picked) match = r; });
        if (show && radios.length) (match || radios[0]).checked = true;
      });
    };
    sel.addEventListener("change", function () { picked = ""; sync(); });
    sync();
  });

  /* ---- passwords never contain spaces: block typing, strip pasting ---------- */
  $$("input[type=password]").forEach(function (i) { i.setAttribute("data-nospace", ""); });
  document.addEventListener("keydown", function (e) {
    if (e.key === " " && e.target.matches && e.target.matches("[data-nospace]")) e.preventDefault();
  });
  document.addEventListener("input", function (e) {
    var t = e.target;
    if (t.matches && t.matches("[data-nospace]") && /\s/.test(t.value)) t.value = t.value.replace(/\s+/g, "");
  });

  /* ---- "kids who missed weeks" pop-up: once per browser session -------------- */
  $$("[data-alert-modal]").forEach(function (box) {
    var key = "alert-" + (box.getAttribute("data-key") || "x");
    var seen = false;
    try { seen = window.sessionStorage.getItem(key) === "1"; } catch (e) { /* private mode */ }
    if (seen) { box.remove(); return; }
    var close = function () {
      try { window.sessionStorage.setItem(key, "1"); } catch (e) { /* ignore */ }
      box.remove();
    };
    box.addEventListener("click", function (e) { if (e.target === box) close(); });
    $$("[data-modal-close]", box).forEach(function (b) { b.addEventListener("click", close); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && box.isConnected) close(); });
  });

  /* ---- auto-submit selects ---------------------------------------------- */
  $$("[data-autosubmit]").forEach(function (el) {
    el.addEventListener("change", function () { el.form && el.form.submit(); });
  });

  /* ---- live list filter: <input data-filter="#list"> rows have data-search */
  $$("[data-filter]").forEach(function (input) {
    var box = $(input.getAttribute("data-filter"));
    if (!box) return;
    var empty = $("[data-filter-empty]", box.parentNode);
    input.addEventListener("input", function () {
      var q = input.value.trim().toLowerCase(), shown = 0;
      $$("[data-search]", box).forEach(function (row) {
        var hit = !q || row.getAttribute("data-search").toLowerCase().indexOf(q) !== -1;
        row.classList.toggle("hidden", !hit);
        if (hit) shown++;
      });
      if (empty) empty.classList.toggle("hidden", shown !== 0);
    });
  });

  /* ---- passwords: live rules checklist + generator ---------------------- */
  var SPECIAL = /[!@#$%^&*(),.?":{}|<>_\-+=]/;
  var RULES = {
    length: function (p) { return p.length >= 8 && p.length <= 128; },
    upper: function (p) { return /[A-Z]/.test(p); },
    lower: function (p) { return /[a-z]/.test(p); },
    digit: function (p) { return /\d/.test(p); },
    special: function (p) { return SPECIAL.test(p); },
    nospace: function (p) { return !/\s/.test(p); }
  };
  $$("[data-pw-rules]").forEach(function (input) {
    var list = $(input.getAttribute("data-pw-rules"));
    if (!list) return;
    var update = function () {
      var v = input.value, all = v.length > 0;
      $$("li[data-rule]", list).forEach(function (li) {
        var ok = v.length > 0 && RULES[li.getAttribute("data-rule")](v);
        li.classList.toggle("ok", ok);
      });
    };
    input.addEventListener("input", update);
    update();
  });

  function generatePassword(len) {
    var U = "ABCDEFGHJKLMNPQRSTUVWXYZ", L = "abcdefghijkmnpqrstuvwxyz", D = "23456789", S = "!@#$%&*?";
    var all = U + L + D + S;
    function pick(set) {
      var a = new Uint32Array(1);
      window.crypto.getRandomValues(a);
      return set.charAt(a[0] % set.length);
    }
    var chars = [pick(U), pick(L), pick(D), pick(S)];
    while (chars.length < len) chars.push(pick(all));
    for (var i = chars.length - 1; i > 0; i--) {         // Fisher–Yates
      var r = new Uint32Array(1); window.crypto.getRandomValues(r);
      var j = r[0] % (i + 1), tmp = chars[i]; chars[i] = chars[j]; chars[j] = tmp;
    }
    return chars.join("");
  }

  /* ---- attendance page -------------------------------------------------- */
  var att = $("#attendance");
  if (att) {
    var form = $("#attendance-form"), rows = $$(".kid-row", att);
    var total = rows.length, dirty = false, lost = false;
    var bar = $("#att-progress"), count = $("#att-count"), saveBtn = $("#att-save");
    var cP = $("#c-present"), cA = $("#c-absent"), cE = $("#c-excused");
    var msg = function (k) { return att.getAttribute("data-msg-" + k) || ""; };

    var refresh = function () {
      var n = { present: 0, absent: 0, excused: 0 }, marked = 0;
      rows.forEach(function (r) {
        var c = $("input[type=radio]:checked", r);
        r.setAttribute("data-status", c ? c.value : "");
        if (c) { n[c.value]++; marked++; r.classList.remove("missing"); }
      });
      if (bar) bar.style.setProperty("--pct", total ? (marked * 100 / total) : 0);
      if (count) count.textContent = marked + " / " + total;
      if (cP) cP.textContent = n.present; if (cA) cA.textContent = n.absent; if (cE) cE.textContent = n.excused;
      return marked;
    };
    refresh();

    att.addEventListener("change", function (e) {
      if (e.target.matches("input[type=radio]")) { dirty = true; refresh(); }
    });
    att.addEventListener("input", function (e) {
      if (e.target.matches(".note-input")) dirty = true;
    });

    var markAll = $("[data-mark-all]");
    if (markAll) markAll.addEventListener("click", function () {
      var differs = rows.some(function (r) {
        var c = $("input[type=radio]:checked", r);
        return c && c.value !== "present";
      });
      var warn = markAll.getAttribute("data-confirm-overwrite");
      if (differs && warn && !window.confirm(warn)) return;            // never silently overwrite absences
      rows.forEach(function (r) {
        var p = $("input[value=present]", r);
        if (p) p.checked = true;
      });
      dirty = true; refresh();
    });

    form.addEventListener("submit", function (e) {
      if (lost) { e.preventDefault(); return; }
      var first = null;
      rows.forEach(function (r) {
        if (!$("input[type=radio]:checked", r)) { r.classList.add("missing"); if (!first) first = r; }
      });
      if (first) {
        e.preventDefault();
        first.scrollIntoView({ behavior: "smooth", block: "center" });
        window.alert(msg("incomplete"));
        return;
      }
      dirty = false;
    });

    var rel = $("#release-form");
    if (rel) rel.addEventListener("submit", function () { dirty = false; });

    window.addEventListener("beforeunload", function (e) {
      if (dirty) { e.preventDefault(); e.returnValue = ""; }
    });

    // keep my lock alive; detect if someone else took over
    var url = att.getAttribute("data-heartbeat");
    var chip = $("#lock-chip");
    if (url) {
      setInterval(function () {
        fetch(url, { method: "POST", credentials: "same-origin",
                     headers: { "X-CSRFToken": csrf(), "Accept": "application/json" } })
          .then(function (r) { return r.json(); })
          .then(function (d) {
            if (d && d.ok === false && !lost) {
              lost = true; dirty = false;
              if (chip) { chip.classList.add("lost"); chip.textContent = msg("lost"); }
              if (saveBtn) saveBtn.disabled = true;
              var warn = $("#lost-banner"); if (warn) warn.classList.remove("hidden");
            }
          }).catch(function () { /* offline: keep working, server re-checks on save */ });
      }, 30000);
    }
  }
})();
