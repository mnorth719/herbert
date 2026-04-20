// Diagnostic view: boot snapshot (top) + prompt subview + live log tail.
//
// The boot snapshot fetches /api/boot_snapshot each time the view opens.
// The prompt subview fetches /api/prompt/snapshot and shows the assembled
// system prompt + live-session messages, with a manual refresh. Both
// fetches carry the bearer token in --expose mode. Log lines arrive via WS.

const MAX_LINES = 200;
const STALE_THRESHOLD_MS = 10_000;
const lineBuffer = [];

let authToken = null;
let promptFetchedAt = null;

/** Must be called once before setView or appendLogLine are used so the
 *  snapshot fetch carries the bearer token in --expose mode. */
export function configureDiagnostic({ token }) {
  authToken = token || null;
}

export function appendLogLine({ level, line }) {
  lineBuffer.push({ level, line });
  if (lineBuffer.length > MAX_LINES) lineBuffer.shift();
  if (document.getElementById('diagnostic').classList.contains('visible')) {
    renderLogs();
  }
}

export async function setView(mode) {
  const el = document.getElementById('diagnostic');
  if (mode === 'diagnostic') {
    el.classList.add('visible');
    renderShell();
    await Promise.all([
      fetchAndRenderSnapshot(),
      fetchAndRenderPromptSnapshot(),
    ]);
    renderLogs();
  } else {
    el.classList.remove('visible');
  }
}

function renderShell() {
  const el = document.getElementById('diagnostic');
  el.innerHTML = `
    <section id="diag-snapshot" class="diag-section">
      <h3>Boot snapshot</h3>
      <div id="diag-snapshot-body">loading…</div>
    </section>
    <section id="diag-prompt" class="diag-section">
      <div class="prompt-header">
        <h3>Prompt</h3>
        <div class="prompt-header-right">
          <span id="diag-prompt-stamp"></span>
          <button id="diag-prompt-refresh" type="button">↻ refresh</button>
        </div>
      </div>
      <div id="diag-prompt-body">loading…</div>
    </section>
    <section id="diag-logs" class="diag-section">
      <h3>Live log</h3>
      <div id="diag-log-body"></div>
    </section>
  `;
  document
    .getElementById('diag-prompt-refresh')
    .addEventListener('click', fetchAndRenderPromptSnapshot);
}

async function fetchAndRenderSnapshot() {
  const body = document.getElementById('diag-snapshot-body');
  if (!body) return;
  try {
    const suffix = authToken ? `?token=${encodeURIComponent(authToken)}` : '';
    const resp = await fetch(`/api/boot_snapshot${suffix}`);
    if (!resp.ok) {
      body.textContent = `snapshot unavailable: HTTP ${resp.status}`;
      return;
    }
    const data = await resp.json();
    body.innerHTML = formatSnapshot(data);
  } catch (err) {
    body.textContent = `snapshot error: ${err}`;
  }
}

async function fetchAndRenderPromptSnapshot() {
  const body = document.getElementById('diag-prompt-body');
  if (!body) return;
  body.textContent = 'loading…';
  try {
    const suffix = authToken ? `?token=${encodeURIComponent(authToken)}` : '';
    const resp = await fetch(`/api/prompt/snapshot${suffix}`);
    if (!resp.ok) {
      body.innerHTML = `<div class="prompt-error">prompt unavailable: HTTP ${resp.status}</div>`;
      return;
    }
    const data = await resp.json();
    body.innerHTML = formatPromptSnapshot(data);
    promptFetchedAt = Date.now();
    updatePromptStamp();
    // Schedule a single stale-check to dim the timestamp after threshold.
    setTimeout(updatePromptStamp, STALE_THRESHOLD_MS + 100);
  } catch (err) {
    body.innerHTML = `<div class="prompt-error">prompt error: ${esc(String(err))}</div>`;
  }
}

function updatePromptStamp() {
  const stamp = document.getElementById('diag-prompt-stamp');
  if (!stamp || promptFetchedAt == null) return;
  const date = new Date(promptFetchedAt);
  const hh = String(date.getHours()).padStart(2, '0');
  const mm = String(date.getMinutes()).padStart(2, '0');
  const ss = String(date.getSeconds()).padStart(2, '0');
  stamp.textContent = `as of ${hh}:${mm}:${ss}`;
  const isStale = Date.now() - promptFetchedAt > STALE_THRESHOLD_MS;
  stamp.classList.toggle('stale', isStale);
}

function formatPromptSnapshot(data) {
  const personaSection = `
    <details class="prompt-collapsible">
      <summary>persona — ${data.persona.tokens} tokens</summary>
      <pre>${esc(data.persona.text)}</pre>
    </details>
  `;

  const toolsSection = data.tools_addendum
    ? `
    <details class="prompt-collapsible">
      <summary>tools — ${data.tools_addendum.tokens} tokens</summary>
      <pre>${esc(data.tools_addendum.text)}</pre>
    </details>
  `
    : '';

  let factsBody;
  if (!data.memory_enabled) {
    factsBody = `<em class="prompt-empty">memory is disabled</em>`;
  } else if (data.facts.items.length === 0) {
    factsBody = `<em class="prompt-empty">no facts learned yet</em>`;
  } else {
    factsBody = `<ul class="prompt-bullet-list">${data.facts.items
      .map((f) => `<li>${esc(f)}</li>`)
      .join('')}</ul>`;
  }
  const factsSection = `
    <div class="prompt-subsection">
      <div class="prompt-subheader">facts — ${data.facts.tokens} tokens</div>
      ${factsBody}
    </div>
  `;

  let summariesBody;
  if (!data.memory_enabled) {
    summariesBody = `<em class="prompt-empty">memory is disabled</em>`;
  } else if (data.summaries.items.length === 0) {
    summariesBody = `<em class="prompt-empty">no closed sessions yet</em>`;
  } else {
    summariesBody = `<dl class="prompt-summary-list">${data.summaries.items
      .map(
        (s) =>
          `<dt>${esc(s.date)}</dt><dd>${esc(s.summary)}</dd>`
      )
      .join('')}</dl>`;
  }
  const summariesSection = `
    <div class="prompt-subsection">
      <div class="prompt-subheader">recent sessions — ${data.summaries.tokens} tokens</div>
      ${summariesBody}
    </div>
  `;

  const liveBody =
    data.live_messages.length === 0
      ? `<em class="prompt-empty">no messages in this session yet</em>`
      : `<ol class="prompt-live-messages">${data.live_messages
          .map(
            (m) =>
              `<li class="live-msg role-${esc(m.role)}"><span class="live-msg-role">${esc(
                m.role
              )}</span><span class="live-msg-content">${esc(m.content)}</span></li>`
          )
          .join('')}</ol>`;
  const liveSection = `
    <div class="prompt-subsection">
      <div class="prompt-subheader">live messages — ${data.live_messages_tokens} tokens</div>
      ${liveBody}
    </div>
  `;

  const total = `<div class="prompt-total">total ≈ ${data.total_tokens} tokens</div>`;

  return (
    personaSection + toolsSection + factsSection + summariesSection + liveSection + total
  );
}

function formatSnapshot(data) {
  const cfg = data.config;
  const prompt = data.system_prompt;
  const sttSize = cfg.stt.model_size_mb != null
    ? `${cfg.stt.model_size_mb.toFixed(1)} MB`
    : '(missing)';
  const ttsSize = cfg.tts.voice_size_mb != null
    ? `${cfg.tts.voice_size_mb.toFixed(1)} MB`
    : cfg.tts.voice_path ? '(missing)' : '—';

  return `
    <table class="diag-kv">
      <tr><td>version</td><td>${esc(data.version)}</td></tr>
      <tr><td>platform / mode</td><td>${esc(data.platform)} / ${esc(data.mode)}</td></tr>
      <tr><td>ready</td><td>${data.ready ? 'yes' : 'no'}</td></tr>
      <tr><td>llm</td><td>${esc(cfg.llm.model)} max_tokens=${cfg.llm.max_tokens}</td></tr>
      <tr><td>stt</td><td>${esc(cfg.stt.provider)} ${esc(cfg.stt.model)} — ${sttSize}</td></tr>
      <tr><td>tts</td><td>${esc(cfg.tts.provider)} voice=${esc(String(cfg.tts.voice_id))} — ${ttsSize}</td></tr>
      <tr><td>tools</td><td>${data.tools.join(', ') || '(none)'}</td></tr>
      <tr><td>mcp servers</td><td>${data.mcp_servers_count}</td></tr>
      <tr><td>persona_path</td><td>${esc(cfg.persona_path)}</td></tr>
      <tr><td>log transcripts</td><td>${cfg.logging.log_transcripts}</td></tr>
    </table>
    <details class="diag-prompt">
      <summary>System prompt (${prompt.char_count} chars, ~${prompt.token_estimate} tokens)</summary>
      <pre>${esc(prompt.text)}</pre>
    </details>
  `;
}

function esc(s) {
  return String(s ?? '').replace(/[&<>]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
}

function renderLogs() {
  const el = document.getElementById('diag-log-body');
  if (!el) return;
  el.innerHTML = '';
  for (const { level, line } of lineBuffer) {
    const row = document.createElement('div');
    row.className = `log-line ${level}`;
    row.textContent = line;
    el.appendChild(row);
  }
  el.scrollTop = el.scrollHeight;
}
