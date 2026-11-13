/*
  Shared plumbing for every page in this folder:

    - ?theme=<name> loads themes/<name>.css (default kamin) - Cozy.ready(fn) runs once it is in
    - [data-label="x"] elements get the theme's --label-x text, [data-motif] the theme's drawing
    - Cozy.ambient(box) fills a box with the theme's particles (embers, rain, snow, ...)
    - Cozy.mountChat(parent) adds the chat card, Cozy.connect(handlers) the listener
      (same frames as ../bars.html and ../chat.html), or demo data with ?demo=1

  Parameters every page understands: theme=, token=, host= (127.0.0.1), port= (4457),
  source= (twitch,discord), demo=1, side=left (chat card on the left), t.NAME= (override one
  design token from :root).
*/
(function () {
  var params = new URLSearchParams(location.search);
  var root = document.documentElement;

  var THEMES = [
    { id: "kamin",          name: "Kaminabend",     blurb: "Creme-Papier, Kerze, Glut" },
    { id: "teegarten",      name: "Teegarten",      blurb: "Salbei, Pastell-Sprechblasen, Blätter" },
    { id: "regenfenster",   name: "Regenfenster",   blurb: "Lo-Fi-Nacht, Milchglas, Regen" },
    { id: "herbstlaub",     name: "Herbstlaub",     blurb: "Kürbis, Karo, fallende Blätter" },
    { id: "winterhuette",   name: "Winterhütte",    blurb: "Holz, Wolle, Schnee" },
    { id: "kaffeehaus",     name: "Kaffeehaus",     blurb: "Mokka, Milchschaum, Dampf" },
    { id: "sternennacht",   name: "Sternennacht",   blurb: "Mond, Lila, funkelnde Sterne" },
    { id: "gluehwuermchen", name: "Glühwürmchen",   blurb: "Moos, Waldnacht, Leuchtpunkte" },
    { id: "bibliothek",     name: "Bibliothek",     blurb: "Pergament, Serifen, Staub im Licht" },
    { id: "kirschbluete",   name: "Kirschblüte",    blurb: "Rosa, Creme, Blütenblätter" }
  ];

  var themeId = (params.get("theme") || "kamin").replace(/[^a-z0-9_-]/gi, "");
  root.setAttribute("data-theme", themeId);
  params.forEach(function (value, key) {
    if (key.indexOf("t.") === 0) root.style.setProperty("--" + key.slice(2), value);
  });

  var readyCallbacks = [], themeLoaded = false, domLoaded = document.readyState !== "loading";
  function flush() {
    if (!themeLoaded || !domLoaded) return;
    if (token("hero-line") === "on") document.querySelectorAll(".hero").forEach(function (h) { h.classList.add("lined"); });
    if (params.get("side") === "left") document.querySelectorAll(".stage").forEach(function (st) { st.classList.add("chat-left"); });
    fillLabels(document);
    while (readyCallbacks.length) readyCallbacks.shift()();
  }
  var link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = "themes/" + themeId + ".css";
  link.onload = link.onerror = function () { themeLoaded = true; flush(); };
  document.head.appendChild(link);
  if (!domLoaded) document.addEventListener("DOMContentLoaded", function () { domLoaded = true; flush(); });

  // --- tokens -----------------------------------------------------------------------------
  function token(name) {
    return getComputedStyle(root).getPropertyValue("--" + name).trim();
  }
  function label(name) {
    return token("label-" + name).replace(/^["']|["']$/g, "").replace(/\\'/g, "'");
  }
  function list(name) {
    var v = token(name);
    if (!v || v === "none") return [];
    // split on commas that are not inside rgba(...)
    var out = [], depth = 0, cur = "";
    for (var i = 0; i < v.length; i++) {
      var ch = v[i];
      if (ch === "(") depth++;
      if (ch === ")") depth--;
      if (ch === "," && !depth) { out.push(cur.trim()); cur = ""; } else cur += ch;
    }
    if (cur.trim()) out.push(cur.trim());
    return out;
  }

  function fillLabels(scope) {
    scope.querySelectorAll("[data-label]").forEach(function (el) {
      el.textContent = label(el.getAttribute("data-label"));
    });
    scope.querySelectorAll("[data-motif]").forEach(function (el) {
      el.innerHTML = MOTIFS[token("motif")] || MOTIFS.candle;
    });
  }

  // --- motifs: one small drawing per theme, coloured by the theme's tokens ----------------
  var MOTIFS = {
    candle:
      '<svg viewBox="0 0 64 64"><ellipse cx="32" cy="59" rx="17" ry="3.5" fill="var(--text)" opacity=".15"/>' +
      '<rect x="22" y="27" width="20" height="31" rx="4" fill="var(--motif-light)"/>' +
      '<path d="M22 33h20" stroke="var(--text)" stroke-opacity=".12" stroke-width="2"/>' +
      '<path class="m-flame" d="M32 4c6 8 8 12 8 16a8 8 0 0 1-16 0c0-4 2-8 8-16z" fill="var(--accent)"/>' +
      '<path class="m-flame" d="M32 12c3 5 4 7 4 9a4 4 0 0 1-8 0c0-2 1-4 4-9z" fill="#fff4b0"/>' +
      '<path d="M32 24v4" stroke="var(--text)" stroke-width="2"/></svg>',
    teacup:
      '<svg viewBox="0 0 64 64" fill="none" stroke-linecap="round">' +
      '<path class="m-steam" d="M24 6c-3 4 3 6 0 11M33 4c-3 4 3 6 0 11" stroke="var(--text-soft)" stroke-width="3"/>' +
      '<path d="M10 24h38v8a17 17 0 0 1-17 17h-4a17 17 0 0 1-17-17z" fill="var(--accent)"/>' +
      '<path d="M48 28h4a6 6 0 0 1 0 12h-6" stroke="var(--accent)" stroke-width="4"/>' +
      '<path d="M18 33c3 4 8 4 11 0" stroke="var(--motif-light)" stroke-width="3"/>' +
      '<ellipse cx="30" cy="54" rx="24" ry="5" fill="var(--accent-2)"/></svg>',
    lamp:
      '<svg viewBox="0 0 64 64"><path d="M2 60l14-30h32l14 30z" fill="var(--accent)" opacity=".18"/>' +
      '<path d="M20 6h24l8 22H12z" fill="var(--accent)"/><path d="M20 6h24l2 5H18z" fill="#fff" opacity=".25"/>' +
      '<rect x="30" y="28" width="4" height="26" fill="var(--text-soft)"/>' +
      '<rect x="20" y="54" width="24" height="6" rx="3" fill="var(--text-soft)"/></svg>',
    leaf:
      '<svg viewBox="0 0 64 64"><path d="M32 4l5 12 9-6-2 12 12-1-8 10 10 6-13 3 4 11-11-6-6 13-6-13-11 6 4-11-13-3 10-6-8-10 12 1-2-12 9 6z" fill="var(--accent)"/>' +
      '<path d="M32 18v44M32 34l-10-8M32 34l10-8M32 44l-12-2M32 44l12-2" stroke="var(--text)" stroke-opacity=".35" stroke-width="2" fill="none"/></svg>',
    cabin:
      '<svg viewBox="0 0 64 64"><rect x="42" y="10" width="7" height="16" fill="var(--text-soft)"/>' +
      '<path d="M6 32L32 10l26 22z" fill="var(--accent-2)"/><path d="M6 32L32 10l26 22-4 3L32 16 10 35z" fill="#fff" opacity=".9"/>' +
      '<rect x="11" y="31" width="42" height="27" fill="var(--accent)"/>' +
      '<path d="M11 38h42M11 45h42M11 52h42" stroke="var(--text)" stroke-opacity=".18" stroke-width="2"/>' +
      '<rect class="m-glow" x="18" y="38" width="11" height="10" rx="1.5" fill="#ffd27a"/>' +
      '<rect x="36" y="40" width="11" height="18" rx="1.5" fill="var(--text)" opacity=".55"/></svg>',
    mug:
      '<svg viewBox="0 0 64 64" fill="none" stroke-linecap="round">' +
      '<path class="m-steam" d="M24 4c-3 4 3 6 0 11M34 4c-3 4 3 6 0 11" stroke="var(--text-soft)" stroke-width="3"/>' +
      '<path d="M12 20h34v28a10 10 0 0 1-10 10H22a10 10 0 0 1-10-10z" fill="var(--accent)"/>' +
      '<path d="M46 26h4a7 7 0 0 1 0 14h-4" stroke="var(--accent)" stroke-width="5"/>' +
      '<ellipse cx="29" cy="21" rx="15" ry="3.5" fill="var(--motif-light)"/>' +
      '<path d="M29 38s-7-4-7-8a3.5 3.5 0 0 1 7-1 3.5 3.5 0 0 1 7 1c0 4-7 8-7 8z" fill="var(--motif-light)"/></svg>',
    moon:
      '<svg viewBox="0 0 64 64"><path d="M40 8a24 24 0 1 0 16 38A20 20 0 0 1 40 8z" fill="var(--motif-light)"/>' +
      '<circle cx="24" cy="38" r="3" fill="var(--text)" opacity=".12"/><circle cx="32" cy="48" r="2" fill="var(--text)" opacity=".12"/>' +
      '<path class="m-glow" d="M52 8l1.5 4 4 1.5-4 1.5-1.5 4-1.5-4-4-1.5 4-1.5z" fill="var(--accent)"/>' +
      '<path class="m-glow" d="M58 26l1 2.5 2.5 1-2.5 1-1 2.5-1-2.5-2.5-1 2.5-1z" fill="var(--accent)"/></svg>',
    jar:
      '<svg viewBox="0 0 64 64"><rect x="20" y="4" width="24" height="8" rx="2" fill="var(--text-soft)"/>' +
      '<path d="M18 12h28v4c6 4 8 8 8 16v20a8 8 0 0 1-8 8H18a8 8 0 0 1-8-8V32c0-8 2-12 8-16z" fill="var(--accent-2)" opacity=".35"/>' +
      '<path d="M18 12h28v4c6 4 8 8 8 16v20a8 8 0 0 1-8 8H18a8 8 0 0 1-8-8V32c0-8 2-12 8-16z" fill="none" stroke="var(--accent-2)" stroke-width="2.5"/>' +
      '<circle class="m-glow" cx="24" cy="36" r="3.5" fill="var(--accent)"/><circle class="m-glow" cx="38" cy="30" r="3" fill="var(--accent)"/>' +
      '<circle class="m-glow" cx="34" cy="48" r="3.5" fill="var(--accent)"/><circle class="m-glow" cx="44" cy="44" r="2.5" fill="var(--accent)"/></svg>',
    book:
      '<svg viewBox="0 0 64 64"><path d="M32 14C24 8 12 8 4 10v42c8-2 20-2 28 4 8-6 20-6 28-4V10c-8-2-20-2-28 4z" fill="var(--motif-light)" stroke="var(--text)" stroke-opacity=".45" stroke-width="2.5" stroke-linejoin="round"/>' +
      '<path d="M32 14v42" stroke="var(--text)" stroke-opacity=".25" stroke-width="2"/>' +
      '<path d="M10 20c6-1 12-1 17 2M10 28c6-1 12-1 17 2M10 36c6-1 12-1 17 2M37 22c5-3 11-3 17-2M37 30c5-3 11-3 17-2" stroke="var(--text)" stroke-opacity=".2" stroke-width="2" fill="none"/>' +
      '<path d="M44 8v18l4-4 4 4V8z" fill="var(--accent)"/></svg>',
    blossom:
      '<svg viewBox="0 0 64 64"><g fill="var(--accent)">' +
      '<ellipse cx="32" cy="16" rx="10" ry="13"/><ellipse cx="32" cy="16" rx="10" ry="13" transform="rotate(72 32 34)"/>' +
      '<ellipse cx="32" cy="16" rx="10" ry="13" transform="rotate(144 32 34)"/><ellipse cx="32" cy="16" rx="10" ry="13" transform="rotate(216 32 34)"/>' +
      '<ellipse cx="32" cy="16" rx="10" ry="13" transform="rotate(288 32 34)"/></g>' +
      '<circle cx="32" cy="34" r="7" fill="var(--motif-light)"/><circle cx="32" cy="34" r="3" fill="var(--accent-2)"/></svg>'
  };

  // --- particles --------------------------------------------------------------------------
  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var STATIC_TYPES = { stars: 1, fireflies: 1, dust: 1 };
  var SPEED = { embers: [7, 7], rain: [1.1, 1.2], snow: [9, 9], leaves: [10, 8], petals: [11, 8],
                steam: [6, 5], stars: [3, 4], fireflies: [8, 8], dust: [14, 10] };
  var SIZE = { embers: [5, 6], rain: [7, 4], snow: [5, 9], leaves: [22, 18], petals: [14, 10],
               steam: [90, 80], stars: [2, 4], fireflies: [5, 4], dust: [3, 4] };

  function ambient(box, count) {
    if (params.get("ambient") === "0") return;
    var type = token("ambient");
    if (!type || type === "none" || !SPEED[type]) return;
    if (reduceMotion && !STATIC_TYPES[type]) return;
    var colors = list("ambient-colors");
    var n = count || parseInt(token("ambient-count"), 10) || 14;
    box.innerHTML = "";
    box.classList.add("amb-" + type);
    box.style.setProperty("--fall", (box.clientHeight + 80) + "px");
    for (var i = 0; i < n; i++) {
      var p = document.createElement("i");
      var sp = SPEED[type], sz = SIZE[type];
      var dur = sp[0] + Math.random() * sp[1];
      p.style.left = (Math.random() * 100) + "%";
      if (STATIC_TYPES[type]) p.style.top = (Math.random() * 100) + "%";
      p.style.setProperty("--s", (sz[0] + Math.random() * sz[1]).toFixed(1) + "px");
      p.style.setProperty("--c", colors[i % colors.length] || "#fff");
      p.style.animationDuration = type === "fireflies" ? dur + "s, " + (2 + Math.random() * 3) + "s" : dur + "s";
      p.style.animationDelay = (-Math.random() * dur) + "s";
      if (type === "rain") p.style.opacity = (.35 + Math.random() * .6).toFixed(2);
      box.appendChild(p);
    }
  }

  // --- helpers ----------------------------------------------------------------------------
  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function hash(str) {
    var h = 0;
    for (var i = 0; i < str.length; i++) h = ((h << 5) - h + str.charCodeAt(i)) | 0;
    return Math.abs(h);
  }
  function tint(name) {
    var names = list("names");
    return names.length ? names[hash(name) % names.length] : "var(--accent)";
  }
  function duration(seconds) {
    var s = Math.max(0, Math.floor(seconds));
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    return h ? h + " h " + String(m).padStart(2, "0") + " min" : m + " min";
  }
  function mmss(seconds) {
    var s = Math.max(0, Math.round(seconds));
    return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
  }
  function clock() {
    var d = new Date();
    return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
  }

  // Drops the oldest children until the content fits - scrollHeight misses overflow towards the
  // top (justify-content:flex-end), so the heights are summed by hand.
  function fit(box, max) {
    while (box.children.length > (max || 14)) box.removeChild(box.firstElementChild);
    var style = getComputedStyle(box);
    var gap = parseFloat(style.rowGap) || 0;
    var room = box.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom);
    function used() {
      var total = 0;
      for (var k = 0; k < box.children.length; k++) total += box.children[k].offsetHeight + (k ? gap : 0);
      return total;
    }
    while (box.children.length > 1 && used() > room) box.removeChild(box.firstElementChild);
  }

  // Scales a fixed-size stage into whatever window or OBS source it sits in.
  function scale(stage, w, h) {
    function apply() {
      var s = Math.min(window.innerWidth / w, window.innerHeight / h);
      var x = (window.innerWidth - w * s) / 2, y = (window.innerHeight - h * s) / 2;
      stage.style.transform = "translate(" + x + "px," + y + "px) scale(" + s + ")";
    }
    window.addEventListener("resize", apply);
    apply();
  }

  // --- the bars' running two-colour hairline, as an SVG stroke along any path -------------
  function runningLine(d, w, h) {
    var weights = [0, .07, .25, .5, .75, .93, 1, .93, .75, .5, .25, .07, 0];
    var stops = weights.map(function (k, i) {
      return '<stop offset="' + (i / 12).toFixed(4) + '" style="stop-color:color-mix(in srgb, var(--bars-line-b) ' +
        Math.round(k * 100) + '%, var(--bars-line-a))"/>';
    }).join("");
    var anim = reduceMotion ? "" :
      '<animateTransform attributeName="gradientTransform" type="translate" from="0 0" to="240 0" dur="10s" repeatCount="indefinite"/>';
    var id = "rl" + Math.random().toString(36).slice(2, 8);
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("width", String(w));
    svg.setAttribute("height", String(h));
    svg.innerHTML = '<defs><linearGradient id="' + id + '" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="240" y2="0" spreadMethod="repeat">' +
      stops + anim + '</linearGradient></defs><path d="' + d + '" fill="none" stroke="url(#' + id + ')" stroke-width="3" transform="translate(0 -1.5)"/>';
    return svg;
  }

  // --- chat card --------------------------------------------------------------------------
  function mountChat(parent) {
    var card = document.createElement("div");
    card.className = "card chat" + (params.get("side") === "left" ? " left" : "");
    card.innerHTML = '<div class="ambient"></div>' +
      '<div class="chat-head"><span class="motif small" data-motif></span><span data-label="chat"></span></div>' +
      '<div class="log"></div>';
    parent.appendChild(card);
    fillLabels(card);
    var log = card.querySelector(".log");
    ambient(card.querySelector(".ambient"));
    var bubbles = list("bubbles");
    return {
      card: card,
      add: function (m, live) {
        var name = m.user_name || "";
        var row = document.createElement("div");
        row.className = "msg" + (live ? "" : " still") + ((hash(name) >> 3) % 2 ? " right" : "");
        if (m.id != null) row.dataset.chatId = m.id;
        row.style.setProperty("--tint", tint(name));
        if (bubbles.length) row.style.setProperty("--bubble", bubbles[hash(name) % bubbles.length]);
        row.innerHTML = '<span class="who">' + escapeHtml(name) + '</span><span class="text">' + escapeHtml(m.text || "") + "</span>";
        log.appendChild(row);
        fit(log);
      },
      clear: function (silent) {
        log.innerHTML = silent ? "" : '<div class="msg sys">alles frisch aufgeräumt</div>';
      }
    };
  }

  // --- connection -------------------------------------------------------------------------
  var state = {
    live: false, started_at: null, title: "", game: "", viewers: 0, last_follower: "", last_sub: "",
    last_raid: "", deaths: 0, ad_break_started_at: null, ad_break_seconds: 0, stream_recap: null
  };

  var DEMO_CHAT = [
    ["teekesselchen", "guten abend zusammen"],
    ["pixel_pia", "so gemütlich hier heute"],
    ["kernel_kalle", "endlich Feierabend, passt perfekt"],
    ["LurkerSupreme", "*kuschelt sich in die decke*"],
    ["glitch_witch", "hab mir extra kakao gemacht"],
    ["async_anna", "die musik ist so schön"],
    ["daemon_dana", "läuft brav im Hintergrund mit"],
    ["byte_sized", "noch eine runde?"],
    ["catgirl_dev", "uwu bin da"],
    ["vim_over_emacs", "regen draußen, stream drinnen, perfekt"]
  ];

  function connect(h) {
    h = h || {};
    var onState = h.onState || function () {};
    var onMessage = h.onMessage || function () {};
    var onClear = h.onClear || function () {};
    // uptime/clock tick on their own, the bot only sends started_at once
    setInterval(function () { onState(state, {}); }, 15000);

    if (params.get("demo") === "1") {
      Object.assign(state, {
        live: true, started_at: Date.now() / 1000 - 5420, title: "Gemütlicher Abend mit Tee",
        game: "Stardew Valley", viewers: 27, last_follower: "teekesselchen", last_sub: "kernel_kalle",
        deaths: 3
      }, h.demoState || {});
      onState(state, state);
      DEMO_CHAT.slice(0, 4).forEach(function (l) { onMessage({ user_name: l[0], text: l[1], platform: "twitch" }, false); });
      var i = 4;
      (function tick() {
        var l = DEMO_CHAT[i++ % DEMO_CHAT.length];
        onMessage({ user_name: l[0], text: l[1], platform: "twitch" }, true);
        setTimeout(tick, 2400 + Math.random() * 2600);
      })();
      setTimeout(function () { state.last_follower = "neu_hier_42"; onState(state, { last_follower: 1 }); }, 9000);
      return;
    }

    var token = params.get("token") || "";
    var host = params.get("host") || "127.0.0.1";
    var port = params.get("port") || "4457";
    var sources = (params.get("source") || "").split(",")
      .map(function (s) { return s.trim().toLowerCase(); }).filter(Boolean);
    if (!token) { console.warn("Kein ?token= in der URL - keine Verbindung."); if (h.onNoToken) h.onNoToken(); return; }
    function inScope(m) { return !sources.length || sources.indexOf((m.platform || "").toLowerCase()) !== -1; }

    (function open() {
      var socket = new WebSocket("ws://" + host + ":" + port + "/?token=" + encodeURIComponent(token));
      socket.onclose = function () { setTimeout(open, 3000); };
      socket.onerror = function () { socket.close(); };
      socket.onmessage = function (event) {
        var frame;
        try { frame = JSON.parse(event.data); } catch (e) { return; }
        if (frame.type === "state" || frame.type === "patch") {
          Object.assign(state, frame.data || {});
          onState(state, frame.data || {});
        } else if (frame.type === "history") {
          onClear(true);
          (frame.data || []).forEach(function (m) { if (inScope(m)) onMessage(m, false); });
        } else if (frame.type === "message") {
          if (inScope(frame.data)) onMessage(frame.data, true);
        } else if (frame.type === "clear") {
          onClear(false);
        } else if (frame.type === "remove") {
          // a ban/timeout: that user's lines leave every chat card on the page
          ((frame.data || {}).ids || []).forEach(function (id) {
            var rows = document.querySelectorAll('[data-chat-id="' + Number(id) + '"]');
            Array.prototype.forEach.call(rows, function (row) { row.parentNode.removeChild(row); });
          });
        }
      };
    })();
  }

  window.Cozy = {
    THEMES: THEMES,
    theme: themeId,
    params: params,
    state: state,
    reduceMotion: reduceMotion,
    ready: function (fn) { readyCallbacks.push(fn); flush(); },
    token: token,
    label: label,
    list: list,
    fillLabels: fillLabels,
    ambient: ambient,
    escapeHtml: escapeHtml,
    hash: hash,
    tint: tint,
    duration: duration,
    uptime: function () { return state.started_at ? duration(Date.now() / 1000 - state.started_at) : "–"; },
    mmss: mmss,
    clock: clock,
    fit: fit,
    scale: scale,
    mountChat: mountChat,
    runningLine: runningLine,
    connect: connect,
    off: function (part) {
      var off = (params.get("off") || "").split(","), only = (params.get("only") || "").split(",").filter(Boolean);
      return off.indexOf(part) !== -1 || (only.length && only.indexOf(part) === -1);
    }
  };
})();
