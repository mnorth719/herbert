// Transcript panel: appends TranscriptDelta events, clears on TurnStarted.
//
// The daemon publishes the user transcript as a single event (after STT)
// and streaming assistant text as multiple deltas. We coalesce both roles
// into one entry per turn so the layout stays tidy under long replies.

const container = () => document.getElementById('transcript');

let currentTurnEl = null;
let currentAssistantEl = null;

export function clearTranscript() {
  container().innerHTML = '';
  currentTurnEl = null;
  currentAssistantEl = null;
}

export function onTurnStarted() {
  currentTurnEl = document.createElement('div');
  currentTurnEl.className = 'turn';
  container().appendChild(currentTurnEl);
  currentAssistantEl = null;
}

export function onTranscriptDelta({ role, text }) {
  if (!currentTurnEl) onTurnStarted();
  if (role === 'user') {
    const el = document.createElement('div');
    el.className = 'user';
    const label = document.createElement('div');
    label.className = 'role';
    label.textContent = 'you';
    el.appendChild(label);
    const body = document.createElement('div');
    body.textContent = text;
    el.appendChild(body);
    currentTurnEl.appendChild(el);
    currentAssistantEl = null;
  } else if (role === 'assistant') {
    if (!currentAssistantEl) {
      currentAssistantEl = document.createElement('div');
      currentAssistantEl.className = 'assistant';
      const label = document.createElement('div');
      label.className = 'role';
      label.textContent = 'herbert';
      currentAssistantEl.appendChild(label);
      const body = document.createElement('div');
      body.className = 'assistant-body';
      currentAssistantEl.appendChild(body);
      currentTurnEl.appendChild(currentAssistantEl);
    }
    const body = currentAssistantEl.querySelector('.assistant-body');
    body.textContent = (body.textContent || '') + text;
  }
  container().scrollTop = container().scrollHeight;
}
