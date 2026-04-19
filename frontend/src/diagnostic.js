// Diagnostic view: log-tail overlay. Unit 12 wires the voice trigger
// + SSE log stream; for now we just listen for LogLine events and
// surface the last N lines when the user toggles the view (future).

const MAX_LINES = 200;
const lineBuffer = [];

export function appendLogLine({ level, line }) {
  lineBuffer.push({ level, line });
  if (lineBuffer.length > MAX_LINES) lineBuffer.shift();
  if (document.getElementById('diagnostic').classList.contains('visible')) {
    render();
  }
}

export function setView(mode) {
  const el = document.getElementById('diagnostic');
  if (mode === 'diagnostic') {
    el.classList.add('visible');
    render();
  } else {
    el.classList.remove('visible');
  }
}

function render() {
  const el = document.getElementById('diagnostic');
  el.innerHTML = '';
  for (const { level, line } of lineBuffer) {
    const row = document.createElement('div');
    row.className = `log-line ${level}`;
    row.textContent = line;
    el.appendChild(row);
  }
  el.scrollTop = el.scrollHeight;
}
