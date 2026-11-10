// obs-tabs.js - shared by bars.html and terminal/chat.html (loaded as a plain script, no build).
//
// Tabs are shapes that grow out of an edge (the top bar, the bottom bar, the chat card) as a
// calm ground for something that sits on that edge - the avatar, the chat. Each page draws its
// own shape; this file holds what they share:
//
//   ObsTabs.tween(draw, ms)  eased 0..1 animation; draw(t) gets every frame
//   ObsTabs.follow(opts)     ties tabs to OBS sources over obs-websocket (local):
//       port, password       obs-websocket
//       scenes: ["PC"]       the program scenes this page acts in. A tab is out only while
//                            program is one of them and its source is enabled there.
//       tabs: { Avatar: tween, Chat: tween }   source name -> tab
//       keys: { "wafobi-avatar": "Avatar" }     obs-browser event -> source it switches
//       onScene(name)        optional, called with every program scene (also the first)
//   returns { program, item(source) -> {id, on}, send(type, data) } for pages that move
//   things themselves. A tween with .slide = true: its source travels with the tab (the page
//   moves it in its draw), so the key switches it on first and retracts the tab before off.
//
// The keys: the Stream Deck (Bitfocus Companion, OBS module, "Custom Command") sends
//     CallVendorRequest {"vendorName":"obs-browser","requestType":"emit_event",
//                        "requestData":{"event_name":"wafobi-avatar","event_data":{"mode":"toggle"}}}
// and obs-browser hands that to every browser source as a window event (mode toggle|show|hide).
// Only the page whose scenes contain the program scene acts, so exactly one switches. Showing:
// the tab grows first, the source is enabled once it is out. Hiding: the source goes off
// first, the tab follows through the enable event.
(function () {
  "use strict";
  const ease = (x) => (x < .5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2);

  // set(on): out to .level (default 1, a tab that only needs part of its depth sets less)
  // or back in. setLevel(l): a new depth; if the tab is out it moves there.
  function tween(draw, ms) {
    let t = 0, anim = null, on = false;
    draw(0);
    const api = {
      level: 1,
      get value() { return t; },
      set(state, instant) {
        on = !!state;
        const from = t, to = on ? api.level : 0;
        cancelAnimationFrame(anim);
        if (instant || from === to) { t = to; draw(t); return; }
        const start = performance.now();
        const step = (now) => {
          const k = Math.min(1, (now - start) / ms);
          t = from + (to - from) * ease(k);
          draw(t);
          if (k < 1) anim = requestAnimationFrame(step);
        };
        anim = requestAnimationFrame(step);
      },
      setLevel(level) {
        if (Math.abs(level - api.level) < 1e-3) return;
        api.level = level;
        if (on) api.set(true);
      },
      ms,
    };
    return api;
  }

  async function sha256b64(text) {
    const hash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
    return btoa(String.fromCharCode(...new Uint8Array(hash)));
  }

  function follow({ port = "4455", password = "", scenes = [], tabs = {}, keys = {}, onScene }) {
    let program = null, nextId = 1, request = null, busy = false;
    const items = {};                     // source -> { id, on } in the program scene
    const pending = new Map();
    const mine = () => !scenes.length || scenes.includes(program);
    const show = (instant) => {
      for (const [source, tab] of Object.entries(tabs)) {
        tab.set(mine() && !!(items[source] && items[source].on), instant);
      }
    };

    for (const [eventName, source] of Object.entries(keys)) {
      window.addEventListener(eventName, async (event) => {
        if (!request || busy || !scenes.length || !mine()) return;
        const item = items[source];
        if (!item) return;
        busy = true;
        try {
          const mode = (event.detail && event.detail.mode) || "toggle";
          const on = mode === "show" ? true : mode === "hide" ? false : !item.on;
          const tab = tabs[source];
          if (tab && tab.slide) {
            // the source travels with the tab (the page moves it): on first, then out;
            // in first, then off
            if (!on && tab.value > 0) {
              tab.set(false);
              await new Promise(resolve => setTimeout(resolve, tab.ms));
            }
          } else if (on && tab && tab.value < tab.level) {
            tab.set(true);
            await new Promise(resolve => setTimeout(resolve, tab.ms));
          }
          await request("SetSceneItemEnabled", { sceneName: program, sceneItemId: item.id, sceneItemEnabled: on });
        } finally { busy = false; }
      });
    }

    function open() {
      const ws = new WebSocket(`ws://127.0.0.1:${port}`);
      request = (requestType, requestData = {}) => new Promise((resolve) => {
        const requestId = String(nextId++);
        pending.set(requestId, resolve);
        ws.send(JSON.stringify({ op: 6, d: { requestType, requestId, requestData } }));
      });
      // Program scene and the state of every watched source in it. A scene change sets the
      // tabs without animation: the cut already is the change.
      async function refresh(instant) {
        const current = await request("GetCurrentProgramScene");
        const data = current.responseData || {};
        program = data.currentProgramSceneName || data.sceneName;
        if (onScene) onScene(program);
        const watched = new Set([...Object.keys(tabs), ...Object.values(keys)]);
        for (const source of watched) {
          delete items[source];
          const found = await request("GetSceneItemId", { sceneName: program, sourceName: source });
          if (!found.requestStatus.result) continue;
          const id = found.responseData.sceneItemId;
          const state = await request("GetSceneItemEnabled", { sceneName: program, sceneItemId: id });
          items[source] = { id, on: !!(state.responseData && state.responseData.sceneItemEnabled) };
        }
        show(instant);
      }
      ws.onmessage = async (message) => {
        const { op, d } = JSON.parse(message.data);
        if (op === 0) {                                   // Hello -> Identify (Scenes + SceneItems events)
          const identify = { rpcVersion: 1, eventSubscriptions: 4 | 128 };
          if (d.authentication) {
            identify.authentication = await sha256b64(
              (await sha256b64(password + d.authentication.salt)) + d.authentication.challenge);
          }
          ws.send(JSON.stringify({ op: 1, d: identify }));
        } else if (op === 2) {                            // Identified
          refresh(true);
        } else if (op === 7) {                            // RequestResponse
          const resolve = pending.get(d.requestId);
          if (resolve) { pending.delete(d.requestId); resolve(d); }
        } else if (op === 5) {                            // Event
          if (d.eventType === "CurrentProgramSceneChanged") refresh(true);
          else if (d.eventType === "SceneItemEnableStateChanged" && d.eventData.sceneName === program) {
            for (const item of Object.values(items)) {
              if (item.id === d.eventData.sceneItemId) item.on = d.eventData.sceneItemEnabled;
            }
            show(false);
          }
        }
      };
      ws.onclose = () => { pending.clear(); request = null; setTimeout(open, 3000); };
      ws.onerror = () => ws.close();
    }
    open();
    return {
      get program() { return program; },
      item: (source) => items[source] || null,
      send: (requestType, requestData) => request && request(requestType, requestData),
    };
  }

  window.ObsTabs = { tween, follow };
})();
