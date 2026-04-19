// Diagnostic view: boot snapshot (top) + live log tail (below).
//
// The snapshot fetches from /api/boot_snapshot each time the view opens
// so persona hot-reloads and any future memory content are always
// reflected. Log lines arrive via WS and buffer in memory.

const MAX_LINES = 200;
const lineBuffer = [];

let authToken = null;

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
    await fetchAndRenderSnapshot();
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
    <section id="diag-logs" class="diag-section">
      <h3>Live log</h3>
      <div id="diag-log-body"></div>
    </section>
  `;
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
