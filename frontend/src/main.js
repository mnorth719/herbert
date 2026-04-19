// Frontend entry point: boots the PIXI app, wires the WS event stream
// to the character, transcript, boot, diagnostic, and latency modules.

import { Application } from 'pixi.js';
import { createHerbert } from './herbert-character.js';
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

  const character = createHerbert();
  app.stage.addChild(character);

  // Fit the character to the stage while keeping chunky pixel scaling.
  // We designed at a logical 96-pixel scale; pick the largest integer
  // multiplier that still fits comfortably inside the stage rectangle.
  // Fit Herbert to the stage. Silhouette spans local y = -42..+32
  // (antenna + body = 74 total). Scale fills ~60% of the shorter
  // dimension; the y-anchor puts the silhouette's visual midpoint
  // (local y = -5) on the stage center so antenna and body have
  // equal breathing room above and below.
  const SILHOUETTE_CENTER = -5;
  const SILHOUETTE_HEIGHT = 74;

  const layout = () => {
    const { width, height } = app.renderer;
    const maxScaleByHeight = Math.floor((height * 0.6) / SILHOUETTE_HEIGHT);
    const maxScaleByWidth = Math.floor((width * 0.35) / 72);
    const scale = Math.max(2, Math.min(maxScaleByHeight, maxScaleByWidth, 8));
    character.scale.set(scale);
    character.x = width / 2;
    character.y = height / 2 - SILHOUETTE_CENTER * scale;
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

  // Lifecycle layer: overrides the pipeline state whenever Herbert isn't
  // actually reachable-and-ready. Priority order is:
  //   1. WS closed/connecting  → 'disconnected'
  //   2. Daemon not ready yet  → 'warming' (loading models)
  //   3. Otherwise             → last pipelineState ('idle'/'listening'/...)
  let lifecycleState = 'disconnected';
  let pipelineState = 'idle';
  let readyPollCtrl = null;

  const applyState = () => {
    const effective =
      lifecycleState === 'disconnected' ? 'disconnected'
      : lifecycleState === 'warming'    ? 'warming'
      : pipelineState;
    character.setState(effective);
    stateBadge.textContent = effective;
  };
  applyState();

  // Poll /healthz until daemon reports ready=true, then flip to pipeline
  // mode. Stops polling cleanly if WS drops or the page navigates away.
  async function waitForReady() {
    if (readyPollCtrl) readyPollCtrl.abort();
    const ctrl = new AbortController();
    readyPollCtrl = ctrl;
    const authSuffix = token ? `?token=${encodeURIComponent(token)}` : '';
    while (!ctrl.signal.aborted) {
      try {
        const resp = await fetch(`/healthz${authSuffix}`, { signal: ctrl.signal });
        if (resp.ok) {
          const data = await resp.json();
          if (data.ready) {
            lifecycleState = 'ready';
            applyState();
            return;
          }
        }
      } catch (err) {
        if (ctrl.signal.aborted) return;
        // Network flake — ignore and retry
      }
      await new Promise((r) => setTimeout(r, 500));
    }
  }

  connectWs({
    token,
    onConnectionChange(state) {
      connBadge.textContent = state === 'open' ? 'connected' : state;
      if (state === 'open') {
        lifecycleState = 'warming';
        applyState();
        waitForReady();
      } else {
        // 'closed', 'connecting', 'error' — no signal, show disconnected
        lifecycleState = 'disconnected';
        if (readyPollCtrl) readyPollCtrl.abort();
        applyState();
      }
    },
    onEvent(evt) {
      switch (evt.event_type) {
        case 'state_changed':
          pipelineState = evt.to_state;
          if (evt.to_state !== 'error') {
            errorBadge.classList.remove('visible');
          }
          applyState();
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
