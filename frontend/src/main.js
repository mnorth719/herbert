// Frontend entry point: boots the PIXI app, wires the WS event stream
// to the character, transcript, boot, diagnostic, and latency modules.

import { Application } from 'pixi.js';
import { createCharacter } from './state.js';
import {
  clearTranscript,
  onTranscriptDelta,
  onTurnStarted,
} from './transcript.js';
import { runBootSequence } from './boot.js';
import { appendLogLine, setView } from './diagnostic.js';
import { onExchangeLatency, onLatencyMiss } from './latency.js';
import { connectWs } from './ws.js';

async function main() {
  const stage = document.getElementById('stage');
  const app = new Application();
  await app.init({
    antialias: false,
    roundPixels: true,
    background: 0x0a0e10,
    resizeTo: stage,
  });
  app.canvas.style.imageRendering = 'pixelated';
  stage.appendChild(app.canvas);

  const character = createCharacter();
  app.stage.addChild(character);
  const layout = () => {
    character.x = app.renderer.width / 2;
    character.y = app.renderer.height / 2;
  };
  layout();
  window.addEventListener('resize', layout);
  app.ticker.add((time) => character.tick(time.deltaTime));

  // Spacebar forwarding — when the browser tab has focus, pressing space
  // here publishes a ButtonEvent to the daemon via a future WS message
  // (Unit 8+: needs /ws bidi; for v1 we just fire a custom event).
  // Kept local for now; when the daemon accepts WS button events this
  // turns into a two-line extension.
  window.addEventListener('keydown', (e) => {
    if (e.code === 'Space' && !e.repeat) {
      // no-op placeholder — see Unit 8 decision table
    }
  });

  // Boot sequence first, then connect the live WS (fixes the flash
  // where boot text appears after the character has already rendered).
  await runBootSequence();

  const token = new URLSearchParams(location.search).get('token') || null;
  const connBadge = document.getElementById('conn');
  const stateBadge = document.getElementById('state');
  const errorBadge = document.getElementById('error-badge');

  connectWs({
    token,
    onConnectionChange(state) {
      connBadge.textContent = state === 'open' ? 'connected' : state;
    },
    onEvent(evt) {
      switch (evt.event_type) {
        case 'state_changed':
          character.setState(evt.to_state);
          stateBadge.textContent = evt.to_state;
          if (evt.to_state !== 'error') {
            errorBadge.classList.remove('visible');
          }
          break;
        case 'turn_started':
          onTurnStarted();
          break;
        case 'transcript_delta':
          onTranscriptDelta(evt);
          break;
        case 'exchange_latency':
          onExchangeLatency(evt);
          break;
        case 'latency_miss':
          onLatencyMiss(evt);
          break;
        case 'view_changed':
          setView(evt.view);
          break;
        case 'error_occurred':
          errorBadge.classList.add('visible');
          errorBadge.textContent = `ERR: ${evt.error_class}`;
          break;
        case 'log_line':
          appendLogLine(evt);
          break;
        default:
          // Forward-compat: unknown events are ignored rather than crashing
          break;
      }
    },
  });
}

main().catch((err) => {
  console.error('herbert frontend crashed', err);
});

// Expose a manual clear hook for dev-console use
window.herbert = { clearTranscript };
