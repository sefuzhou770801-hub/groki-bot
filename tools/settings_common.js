// SPDX-FileCopyrightText: 2026 Kenta IDA <fuga@fugafuga.org>
// SPDX-License-Identifier: BSL-1.0
//
// Transport-agnostic helpers shared by the two settings pages:
//   - tools/settings.html (BLE)      — loads this as a sibling <script src>
//     (works over file:// for local dev; pages.yml copies it next to the
//     deployed settings.html)
//   - web/settings_wifi.html (HTTP)  — gets this inlined at firmware build
//     time by tools/avatar_dsl/inject.mjs via the SETTINGS_COMMON block
//     comment placeholder (the page must stay a single embeddable file)
//
// Keep this file dependency-free and side-effect-free (pages opt in via
// init()/setupTabs()); both pages assume only `window.StackchanSettings`.
(function () {
  'use strict';

  // --- UI strings ---------------------------------------------------------
  // English when the host page's <html lang> starts with "en" (the BLE page
  // sets it from the browser language / its EN-中文 switch); Chinese
  // otherwise, which keeps the Wi-Fi page (lang="ja") showing the same
  // messages as before. Read at call time so a language switch applies to
  // the next message.
  const STRINGS = {
    en: {
      ready: 'Ready: press Ctrl/Cmd+Enter or click "Compile and send" to apply',
      noCompiler: 'The DSL compiler failed to load, so nothing can be compiled',
      compileErr: 'Compile error: {msg}',
      compiled: 'Done: {bytes} bytes, compiled in {ms} ms',
      sentSaved: 'Sent {label}and saved {n} bytes to NVS{note}',
      logSentBytes: 'Avatar DSL sent ({n} B)',
      sentRaw: 'Sent {label}(response: {raw})',
      logSent: 'Avatar DSL sent',
      sendFailed: 'Sending {label}failed: {err}',
      logSendFailed: 'Avatar DSL send failed: {err}',
      sendError: 'Send error: {msg}',
      sending: 'Sending…',
      sendingProgress: 'Sending… {sent}/{total} B',
      textOnly: '{msg}: only the text was updated',
      resetConfirm: 'This deletes the face override in NVS and returns to the Grok face built into the firmware. Continue?',
      resetDone: 'Face override deleted; the robot is back to the built-in Grok face{raw}',
      logReset: 'Avatar DSL reset',
      resetFail: 'Reset failed: {msg}',
      logResetFail: 'Avatar DSL reset failed: {msg}',
      loaded: 'Loaded: click "Compile and send" to sync it to the robot',
      fileSending: 'Sending {name} ({n} B)…',
      fileProgress: 'Sending {name}… {sent}/{total} B',
      rangeMode: 'Range setting mode: {state}',
      logRangeOn: 'Range setting mode: ON (servo torque off)',
      logRangeOff: 'Range setting mode: OFF',
      rangeFail: 'Failed to switch range setting mode: {msg}',
      noPosition: 'Servo position not read yet',
      authClearConfirm: 'This removes the password (no authentication). It takes effect after Save and restart. Continue?',
      authClearHint: '(clear scheduled: an empty value is sent on save)',
    },
    zh: {
      ready: '已就绪：Ctrl/Cmd+Enter 或点「编译并发送」即可生效',
      noCompiler: '未能加载 DSL 编译器，无法编译',
      compileErr: '编译错误：{msg}',
      compiled: '完成，{bytes} 字节，编译 {ms} ms',
      sentSaved: '{label}发送完成，已向 NVS 保存 {n} 字节{note}',
      logSentBytes: 'Avatar DSL 发送成功（{n} B）',
      sentRaw: '{label}发送完成，响应：{raw}',
      logSent: 'Avatar DSL 发送成功',
      sendFailed: '{label}发送失败：{err}',
      logSendFailed: 'Avatar DSL 发送失败：{err}',
      sendError: '发送错误：{msg}',
      sending: '发送中…',
      sendingProgress: '发送中… {sent}/{total} B',
      textOnly: '{msg}：仅更新了文本',
      resetConfirm: '将删除 NVS 中的面部覆盖并回到固件内置的 Grok 脸。确定吗？',
      resetDone: '已删除面部覆盖，真机回到内置 Grok 脸{raw}',
      logReset: 'Avatar DSL 重置',
      resetFail: '重置失败：{msg}',
      logResetFail: 'Avatar DSL 重置失败：{msg}',
      loaded: '已读取：请点「编译并发送」同步到真机',
      fileSending: '{name}（{n} B）发送中…',
      fileProgress: '{name} 发送中… {sent}/{total} B',
      rangeMode: '范围设置模式: {state}',
      logRangeOn: '范围设置模式: ON（舵机卸力）',
      logRangeOff: '范围设置模式: OFF',
      rangeFail: '切换范围设置模式失败：{msg}',
      noPosition: '尚未获取舵机位置',
      authClearConfirm: '将恢复为无认证（空）。保存并重启后生效。确定吗？',
      authClearHint: '（已预约清除：保存时发送空值）',
    },
  };
  function tr(key, params) {
    const l = String(document.documentElement.lang || '').toLowerCase();
    const dict = l.startsWith('en') ? STRINGS.en : STRINGS.zh;
    let s = dict[key] != null ? dict[key] : STRINGS.en[key];
    if (s == null) return key;
    if (params) s = s.replace(/\{(\w+)\}/g, (m, k) => (k in params ? String(params[k]) : m));
    return s;
  }
  // Helpers write their own text into these elements; drop any page-level
  // i18n key so a later language switch doesn't overwrite it.
  function setOwnText(el, text) {
    if (!el) return;
    el.removeAttribute('data-i18n');
    el.removeAttribute('data-i18n-args');
    el.textContent = text;
  }

  // --- Board facts -----------------------------------------------------
  // Keyed by the BoardKind byte the firmware reports (BLE BoardKind chr /
  // /api/status "board"). Mirrors board::profile_for() — update BOTH when a
  // board is added. `slug` is the per-board firmware ZIP key in
  // versions.json (release_ota uses the same strings device-side).
  const BOARDS = {
    0: { label: 'CoreS3 + M5 base',                 slug: 'cores3',    servo: true,  battery: true  },
    1: { label: 'CoreS3 + Takao base',              slug: 'cores3',    servo: true,  battery: false },
    2: { label: 'AtomS3R + Atomic ECHO BASE',       slug: 'atoms3r',   servo: false, battery: false },
    3: { label: 'AtomS3 + Atomic ECHO BASE (slim)', slug: 'atoms3',    servo: false, battery: false },
    4: { label: 'M5 StopWatch (C152)',              slug: 'stopwatch', servo: false, battery: true  },
  };

  function boardLabel(kind) {
    const b = BOARDS[kind];
    return b ? b.label : `kind ${kind}`;
  }

  // Firmware-ZIP key for a board kind. Unknown / null (pre-BoardKind
  // firmware) falls back to cores3 — the only board that existed then.
  function boardSlug(kind) {
    const b = BOARDS[kind];
    return b ? b.slug : 'cores3';
  }

  // --- Log panel ---------------------------------------------------------
  // Appends one timestamped line to #log. textContent (not innerHTML) so
  // device-supplied strings can't inject markup. The per-page default css
  // class is set via init() (BLE page historically rendered classless calls
  // as "info" blue; the Wi-Fi page as plain).
  let logDefaultClass = '';
  function log(msg, cls) {
    const el = document.getElementById('log');
    if (!el) return;
    const now = new Date();
    const ts = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`;
    const div = document.createElement('div');
    div.className = 'entry';
    const tsSpan = document.createElement('span');
    tsSpan.className = 'ts';
    tsSpan.textContent = `[${ts}]`;
    const body = document.createElement('span');
    body.className = cls === undefined ? logDefaultClass : cls;
    body.textContent = msg;
    div.appendChild(tsSpan);
    div.appendChild(body);
    el.appendChild(div);
    el.scrollTop = el.scrollHeight;
  }

  // --- Tab bar -----------------------------------------------------------
  // Wires .tabs button[data-tab] to .tab-panel[data-tab] and persists the
  // active tab under `storageKey` (per page, so the BLE and Wi-Fi pages
  // remember their tabs independently).
  function setupTabs(storageKey) {
    const tabs = document.querySelectorAll('.tabs button[data-tab]');
    const panels = document.querySelectorAll('.tab-panel[data-tab]');
    function activate(name) {
      let found = false;
      tabs.forEach(b => {
        const on = b.dataset.tab === name;
        b.classList.toggle('active', on);
        if (on) found = true;
      });
      if (!found) return;
      panels.forEach(p => p.classList.toggle('active', p.dataset.tab === name));
      try { localStorage.setItem(storageKey, name); } catch {}
    }
    tabs.forEach(b => b.addEventListener('click', () => activate(b.dataset.tab)));
    let saved = null;
    try { saved = localStorage.getItem(storageKey); } catch {}
    if (saved) activate(saved);
  }

  function init(opts) {
    opts = opts || {};
    if (opts.logDefaultClass !== undefined) logDefaultClass = opts.logDefaultClass;
    if (opts.tabStorageKey) setupTabs(opts.tabStorageKey);
  }

  // --- Avatar DSL editor ---------------------------------------------------
  // Shared editor logic for #avatar-dsl-section (compile in browser via the
  // injected window.AvatarDsl bundle, presets, downloads, file load). The
  // transport object carries the only genuinely page-specific parts:
  //   isConnected()        -> bool (the Wi-Fi page is always "connected")
  //   send(u8, onProgress) -> Promise. Resolve {ok:true, saved:<bytes
  //                           persisted|null>, note:<msg suffix|''>, raw} or
  //                           {ok:false, error, raw}; reject on transport
  //                           failure. onProgress(sent, total) is optional
  //                           for chunked transports.
  //   reset()              -> Promise<{raw}>; reject on failure.
  //   notConnectedMsg      -> shown when isConnected() is false
  //   compilerMissingMsg   -> shown when window.AvatarDsl is absent
  // Returns {refreshButtons} so pages with a connect/disconnect lifecycle
  // (BLE) can re-gate the buttons, or null when the section isn't in the DOM.
  function setupDslEditor(transport) {
    const $id = (id) => document.getElementById(id);
    const srcEl = $id('dsl-src');
    const msg = $id('dsl-msg');
    const sendBtn = $id('btn-dsl-send');
    const resetBtn = $id('btn-dsl-reset');
    const presetEl = $id('dsl-preset');
    if (!srcEl || !msg) return null;

    const setMsg = (txt, kind = '') => { setOwnText(msg, txt); msg.className = kind; };

    const compilerOk = !!(window.AvatarDsl && typeof window.AvatarDsl.compile === 'function');
    if (!compilerOk) setMsg(transport.compilerMissingMsg, 'err');

    // Preset selector. Populated only when injection succeeded; otherwise the
    // dropdown stays empty (Array.isArray guard against the raw placeholder
    // string on an un-injected local file:// copy).
    const presets = Array.isArray(window.AVATAR_DSL_PRESETS) ? window.AVATAR_DSL_PRESETS : [];
    const presetMap = {};
    presets.forEach((p, idx) => {
      presetMap[p.name] = p.source;
      const opt = document.createElement('option');
      opt.value = p.name;
      opt.textContent = (idx === 0 ? '↻ ' : '✎ ') + p.name;
      presetEl.appendChild(opt);
    });
    srcEl.value = (presets[0] && presets[0].source) ||
                  (typeof window.AVATAR_DSL_DEFAULT_SOURCE === 'string' &&
                   !window.AVATAR_DSL_DEFAULT_SOURCE.includes('{{AVATAR_DSL_DEFAULT_SOURCE}}')
                     ? window.AVATAR_DSL_DEFAULT_SOURCE : '');
    if (compilerOk) {
      setMsg(tr('ready'));
    }

    let lastBuf = null; // last successfully-compiled bytecode (for download)

    function compileNow() {
      if (!compilerOk) {
        setMsg(tr('noCompiler'), 'err');
        return null;
      }
      const t0 = performance.now();
      let buf;
      try { buf = window.AvatarDsl.compile(srcEl.value); }
      catch (e) {
        setMsg(tr('compileErr', { msg: e.message }), 'err');
        return null;
      }
      const compileMs = performance.now() - t0;
      setMsg(tr('compiled', { bytes: buf.byteLength, ms: compileMs.toFixed(1) }), 'ok');
      lastBuf = buf;
      return buf;
    }

    // Shared send tail for compile-and-send and .avbc direct upload. `label`
    // prefixes the result messages ('' for the compile path); onProgress
    // receives (sent, total) from chunked transports.
    async function sendBytes(u8, label, onProgress) {
      try {
        const r = await transport.send(u8, onProgress);
        if (r.ok && r.saved != null) {
          setMsg(tr('sentSaved', { label, n: r.saved, note: r.note || '' }), 'ok');
          log(tr('logSentBytes', { n: r.saved }), 'ok');
          return true;
        }
        if (r.ok) {
          setMsg(tr('sentRaw', { label, raw: r.raw }), 'ok');
          log(tr('logSent'), 'ok');
          return true;
        }
        setMsg(tr('sendFailed', { label, err: r.error || 'unknown' }), 'err');
        log(tr('logSendFailed', { err: r.error }), 'err');
      } catch (e) {
        setMsg(tr('sendError', { msg: e.message }), 'err');
        log(tr('logSendFailed', { err: e.message }), 'err');
      }
      return false;
    }

    async function compileAndSend() {
      if (!transport.isConnected()) { setMsg(transport.notConnectedMsg, 'err'); return; }
      const buf = compileNow();
      if (!buf) return;
      sendBtn.disabled = true;
      const oldText = sendBtn.textContent;
      sendBtn.textContent = tr('sending');
      try {
        await sendBytes(new Uint8Array(buf), '', (sent, total) => {
          sendBtn.textContent = tr('sendingProgress', { sent, total });
        });
      } finally {
        sendBtn.textContent = oldText;
        sendBtn.disabled = !transport.isConnected();
      }
    }

    sendBtn.addEventListener('click', compileAndSend);
    // Ctrl/Cmd+Enter inside the textarea triggers compile+send.
    srcEl.addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        e.preventDefault();
        compileAndSend();
      }
    });

    // Preset selector: load source and send the compiled bytecode. Reset is
    // a separate control: it clears the NVS override and returns to the
    // built-in Grok face compiled into the firmware.
    presetEl.addEventListener('change', async () => {
      const name = presetEl.value;
      const src = presetMap[name];
      if (src == null) return;
      srcEl.value = src;
      lastBuf = null;
      if (!transport.isConnected()) {
        setMsg(tr('textOnly', { msg: transport.notConnectedMsg }), 'err');
        return;
      }
      await compileAndSend();
    });

    resetBtn.addEventListener('click', async () => {
      if (!transport.isConnected()) { setMsg(transport.notConnectedMsg, 'err'); return; }
      if (!confirm(tr('resetConfirm'))) return;
      const oldText = resetBtn.textContent;
      resetBtn.disabled = true;
      resetBtn.textContent = tr('sending');
      try {
        const r = await transport.reset();
        setMsg(tr('resetDone', { raw: r.raw ? ` (${r.raw})` : '' }), 'ok');
        log(tr('logReset'), 'ok');
      } catch (e) {
        setMsg(tr('resetFail', { msg: e.message }), 'err');
        log(tr('logResetFail', { msg: e.message }), 'err');
      } finally {
        resetBtn.textContent = oldText;
        resetBtn.disabled = !transport.isConnected();
      }
    });

    $id('btn-dsl-download').addEventListener('click', () => {
      let buf = lastBuf;
      if (!buf) buf = compileNow();
      if (!buf) return;
      const blob = new Blob([buf], { type: 'application/octet-stream' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'face.avbc';
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    });

    $id('btn-dsl-download-src').addEventListener('click', () => {
      const blob = new Blob([srcEl.value], { type: 'text/plain;charset=utf-8' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'face.avdsl';
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    });

    $id('dsl-load').addEventListener('change', (e) => {
      const f = e.target.files[0];
      if (!f) return;
      const r = new FileReader();
      r.onload = () => {
        srcEl.value = r.result;
        setMsg(tr('loaded'));
      };
      r.readAsText(f);
      e.target.value = '';
    });

    // Direct .avbc upload — for users who already have a compiled bytecode. Skips
    // the in-browser compiler entirely. Only present on pages whose HTML has
    // the file input (currently the BLE page).
    const avbcInput = $id('avbc-file');
    if (avbcInput) {
      avbcInput.addEventListener('change', async (e) => {
        const f = e.target.files[0];
        e.target.value = '';
        if (!f) return;
        if (!transport.isConnected()) { setMsg(transport.notConnectedMsg, 'err'); return; }
        const buf = new Uint8Array(await f.arrayBuffer());
        setMsg(tr('fileSending', { name: f.name, n: buf.length }));
        const ok = await sendBytes(buf, `${f.name} `, (sent, total) => {
          setMsg(tr('fileProgress', { name: f.name, sent, total }));
        });
        if (ok) {
          lastBuf = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
        }
      });
    }

    // Gate send / reset / preset on the transport's connection state. The
    // download / file-load buttons stay enabled (useful pre-connect too).
    function refreshButtons() {
      const connected = transport.isConnected();
      if (sendBtn) sendBtn.disabled = !(connected && compilerOk);
      if (resetBtn) resetBtn.disabled = !connected;
      if (presetEl) presetEl.disabled = !connected;
    }
    refreshButtons();

    return { refreshButtons };
  }

  // --- Servo limits form + range-setting calibration -----------------------
  // Shared logic for the #servo-section form (sv-yaw-*/sv-pitch-* inputs) and
  // the hand-driven range calibration: toggle range mode → device drops
  // torque → live positions poll into #sv-live-pos → capture buttons stash
  // raw/degree values into the form. The transport object carries the
  // page-specific parts:
  //   canToggle()      -> bool; gates the range-mode button (BLE: chr + key)
  //   canCapture()     -> bool; extra gate on the capture buttons
  //   setRangeMode(on) -> Promise; reject on failure
  //   readPositions()  -> Promise<{yaw, pitch}> raw steps, -1 = unknown;
  //                       reject on transient failure (display keeps the
  //                       last reading)
  //   pollMs           -> live-position poll period while range mode is on
  // Returns null when the servo section isn't in the DOM.
  const SERVO_DEFAULTS = { yaw_zero: 460, yaw_min: -40, yaw_max: 40,
                           pitch_zero: 620, pitch_min: -10, pitch_max: 25 };
  function setupServoCalibration(transport) {
    const $id = (id) => document.getElementById(id);
    if (!$id('sv-range-mode') || !$id('sv-yaw-zero')) return null;

    let rangeOn = false;
    let liveYaw = -1, livePitch = -1;
    let pollTimer = null;

    function readForm() {
      const num = (id, def) => {
        const v = parseInt($id(id).value, 10);
        return Number.isFinite(v) ? v : def;
      };
      return {
        yaw_zero: num('sv-yaw-zero', SERVO_DEFAULTS.yaw_zero),
        yaw_min:  num('sv-yaw-min',  SERVO_DEFAULTS.yaw_min),
        yaw_max:  num('sv-yaw-max',  SERVO_DEFAULTS.yaw_max),
        pitch_zero: num('sv-pitch-zero', SERVO_DEFAULTS.pitch_zero),
        pitch_min:  num('sv-pitch-min',  SERVO_DEFAULTS.pitch_min),
        pitch_max:  num('sv-pitch-max',  SERVO_DEFAULTS.pitch_max),
      };
    }
    function formJson() { return JSON.stringify(readForm()); }
    function seedForm(json) {
      let o = { ...SERVO_DEFAULTS };
      if (json) { try { Object.assign(o, JSON.parse(json)); } catch {} }
      $id('sv-yaw-zero').value   = o.yaw_zero;
      $id('sv-yaw-min').value    = o.yaw_min;
      $id('sv-yaw-max').value    = o.yaw_max;
      $id('sv-pitch-zero').value = o.pitch_zero;
      $id('sv-pitch-min').value  = o.pitch_min;
      $id('sv-pitch-max').value  = o.pitch_max;
    }

    function rawDeltaToDeg(raw, zero) {
      return Math.round((raw - zero) * 5 / 16);
    }

    function refreshUi() {
      const ok = rangeOn && transport.canCapture();
      for (const id of ['sv-cap-yaw-zero','sv-cap-yaw-min','sv-cap-yaw-max',
                        'sv-cap-pitch-zero','sv-cap-pitch-min','sv-cap-pitch-max']) {
        const el = $id(id);
        if (el) el.disabled = !ok;
      }
      const btn = $id('sv-range-mode');
      setOwnText(btn, tr('rangeMode', { state: rangeOn ? 'ON' : 'OFF' }));
      btn.disabled = !transport.canToggle();
    }

    function clearLiveDisplay() {
      $id('sv-live-pos').textContent = 'Yaw: -- (--°)   Pitch: -- (--°)';
    }

    async function refreshPositions() {
      try {
        const p = await transport.readPositions();
        liveYaw = p.yaw;
        livePitch = p.pitch;
        const zy = parseInt($id('sv-yaw-zero').value, 10) || SERVO_DEFAULTS.yaw_zero;
        const zp = parseInt($id('sv-pitch-zero').value, 10) || SERVO_DEFAULTS.pitch_zero;
        const fmt = (raw, z) => raw >= 0
          ? `${raw} (${rawDeltaToDeg(raw, z) >= 0 ? '+' : ''}${rawDeltaToDeg(raw, z)}°)`
          : '--';
        $id('sv-live-pos').textContent = `Yaw: ${fmt(liveYaw, zy)}   Pitch: ${fmt(livePitch, zp)}`;
      } catch (e) {
        // Transient — leave the last reading visible.
      }
    }

    function restartPoll() {
      clearInterval(pollTimer); pollTimer = null;
      if (rangeOn) {
        pollTimer = setInterval(refreshPositions, transport.pollMs);
        refreshPositions();
      }
    }

    async function setRangeMode(on) {
      if (!transport.canToggle()) return;
      try {
        await transport.setRangeMode(on);
        rangeOn = on;
        refreshUi();
        restartPoll();
        if (on) {
          log(tr('logRangeOn'), 'ok');
        } else {
          log(tr('logRangeOff'));
          liveYaw = livePitch = -1;
          clearLiveDisplay();
        }
      } catch (e) {
        log(tr('rangeFail', { msg: e.message }), 'err');
      }
    }

    // Range mode toggled from elsewhere (another tab / an earlier session) —
    // adopt the device-reported state without re-writing it.
    function syncRangeOn(on) {
      if (on === rangeOn) return;
      rangeOn = on;
      refreshUi();
      restartPoll();
    }

    // Transport lost (BLE disconnect): stop polling and drop back to the
    // idle display. The device auto-OFFs range mode on its side.
    function reset() {
      rangeOn = false;
      liveYaw = livePitch = -1;
      clearInterval(pollTimer); pollTimer = null;
      clearLiveDisplay();
      refreshUi();
    }

    function capture(axis, which) {
      const live = axis === 'yaw' ? liveYaw : livePitch;
      if (live < 0) { log(tr('noPosition'), 'err'); return; }
      const zeroId = `sv-${axis}-zero`;
      if (which === 'zero') {
        $id(zeroId).value = live;
      } else {
        const z = parseInt($id(zeroId).value, 10) ||
                  SERVO_DEFAULTS[axis === 'yaw' ? 'yaw_zero' : 'pitch_zero'];
        $id(`sv-${axis}-${which}`).value = rawDeltaToDeg(live, z);
      }
      // Re-flow the live °-display (the zero may have just moved).
      refreshPositions();
    }

    $id('sv-range-mode').addEventListener('click', () => setRangeMode(!rangeOn));
    for (const axis of ['yaw', 'pitch']) {
      for (const which of ['zero', 'min', 'max']) {
        const el = $id(`sv-cap-${axis}-${which}`);
        if (el) el.addEventListener('click', () => capture(axis, which));
      }
    }

    return { readForm, formJson, seedForm, refreshUi, refreshPositions,
             setRangeMode, syncRangeOn, reset, isOn: () => rangeOn };
  }

  // --- Secret-field buttons (MCP token / auth password) --------------------
  // Identical on both pages: random-generate / show-toggle / clear for
  // #mcp-token, and the confirm-guarded clear for #auth-password. The clear
  // paths set el.dataset.cleared so each page's Apply logic sends an explicit
  // empty value rather than treating an empty field as "leave as-is".
  const MCP_TOKEN_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  function generateMcpToken(len = 32) {
    const out = new Uint8Array(len);
    crypto.getRandomValues(out);
    let s = '';
    for (let i = 0; i < len; ++i) {
      s += MCP_TOKEN_ALPHABET[out[i] % MCP_TOKEN_ALPHABET.length];
    }
    return s;
  }
  function setupSecretFields() {
    const $id = (id) => document.getElementById(id);
    const tokenEl = $id('mcp-token');
    if (tokenEl) {
      $id('btn-mcp-token-gen').addEventListener('click', () => {
        tokenEl.value = generateMcpToken();
        tokenEl.type = 'text';
        delete tokenEl.dataset.cleared;
      });
      $id('btn-mcp-token-show').addEventListener('click', () => {
        tokenEl.type = (tokenEl.type === 'password') ? 'text' : 'password';
      });
      $id('btn-mcp-token-clear').addEventListener('click', () => {
        tokenEl.value = '';
        tokenEl.type = 'password';
        tokenEl.dataset.cleared = '1';
      });
    }
    const authEl = $id('auth-password');
    const authClear = $id('btn-auth-password-clear');
    if (authEl && authClear) {
      authClear.addEventListener('click', () => {
        if (!confirm(tr('authClearConfirm'))) {
          return;
        }
        authEl.value = '';
        authEl.dataset.cleared = '1';
        const hintEl = $id('auth-password-state');
        setOwnText(hintEl, tr('authClearHint'));
      });
    }
  }

  // --- LT timekeeper config form -------------------------------------------
  // Shared form <-> compact-JSON mapping for the #lt-section inputs
  // (lt-total / lt-warn / lt-soon / lt-repeat numbers + the four
  // text/reading announcement pairs). See main/lt_timer.hpp for the schema.

  // Build the compact JSON from whichever fields are filled. Empty form →
  // null (= nothing to send; firmware keeps current values).
  function buildLtConfigJson() {
    const $id = (id) => document.getElementById(id);
    const num = (id) => { const v = $id(id).value.trim(); return v === '' ? null : Number(v); };
    const str = (id) => { const v = $id(id).value.trim(); return v === '' ? null : v; };
    const o = {};
    const total = num('lt-total');   if (total  != null && total  > 0) o.total_s  = total;
    const warn  = num('lt-warn');    if (warn   != null && warn   > 0) o.warn_s   = warn;
    const soon  = num('lt-soon');    if (soon   != null && soon   >= 0) o.soon_s   = soon;
    const rep   = num('lt-repeat');  if (rep    != null && rep    >= 0) o.repeat_s = rep;
    for (const key of ['warn', 'soon', 'just', 'over']) {
      const t = str(`lt-${key}-text`), r = str(`lt-${key}-reading`);
      if (t || r) { o[key] = {}; if (t) o[key].text = t; if (r) o[key].reading = r; }
    }
    return Object.keys(o).length ? JSON.stringify(o) : null;
  }

  // Populate the form from the device-reported JSON. Missing / unparsable
  // JSON leaves the form untouched (placeholders show the firmware defaults).
  function seedLtConfigForm(json) {
    if (!json) return;
    let o;
    try { o = JSON.parse(json); } catch { return; }
    const $id = (id) => document.getElementById(id);
    if (o.total_s  != null) $id('lt-total').value  = o.total_s;
    if (o.warn_s   != null) $id('lt-warn').value   = o.warn_s;
    if (o.soon_s   != null) $id('lt-soon').value   = o.soon_s;
    if (o.repeat_s != null) $id('lt-repeat').value = o.repeat_s;
    for (const key of ['warn', 'soon', 'just', 'over']) {
      if (!o[key]) continue;
      if (o[key].text) $id(`lt-${key}-text`).value = o[key].text;
      if (o[key].reading) $id(`lt-${key}-reading`).value = o[key].reading;
    }
  }

  // --- jtts phrase list mapping ---------------------------------------------
  // Textarea line format: `display | reading` (reading optional). JSON entry: a
  // plain string when display == reading (compact — the JttsConfig budget is
  // ~768 bytes), otherwise {text, reading}. The device parser accepts both.
  // MUST be used by both pages: a page that renders entries with a bare
  // join('\n') turns object entries into "[object Object]" and then persists
  // the corruption on the next save (bit the Wi-Fi page on HW).
  function jttsPhrasesFromText(text) {
    return text
      .split('\n')
      .map((line) => {
        const bar = line.indexOf('|');
        const t = (bar < 0 ? line : line.slice(0, bar)).trim();
        const r = (bar < 0 ? line : line.slice(bar + 1)).trim();
        return { text: t, reading: r };
      })
      .filter((p) => p.text.length > 0 || p.reading.length > 0)
      .map((p) => {
        const t = p.text || p.reading;
        const r = p.reading || p.text;
        return t === r ? t : { text: t, reading: r };
      });
  }
  function jttsPhrasesToText(arr) {
    if (!Array.isArray(arr)) return '';
    return arr.map((p) => {
      if (typeof p === 'string') return p;
      const t = p.text ?? p.reading ?? '';
      const r = p.reading ?? p.text ?? '';
      return t === r ? t : `${t} | ${r}`;
    }).join('\n');
  }

  window.StackchanSettings = { BOARDS, boardLabel, boardSlug, log, setupTabs, init,
                               setupDslEditor, SERVO_DEFAULTS, setupServoCalibration,
                               setupSecretFields, buildLtConfigJson, seedLtConfigForm,
                               jttsPhrasesFromText, jttsPhrasesToText };
})();
