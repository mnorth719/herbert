// Procedural pixel-art character for Herbert.
//
// No sprite sheet — the whole face is drawn with PIXI Graphics primitives
// so future Matt can tune any pixel of it without re-exporting art. Five
// states (idle / listening / thinking / speaking / error) each get a
// distinct combination of eye shape, halo colour, halo behaviour, and
// mouth animation.

import { Container, Graphics } from 'pixi.js';

// Pixel grid: everything is drawn on a 96x96 conceptual canvas. The outer
// PIXI app scales this up with nearest-neighbour filtering so each
// "pixel" is a chunky block — retro CRT feel without needing an actual
// low-res backbuffer.
const GRID = 96;
const PIX = 1; // one grid unit == one logical pixel (we scale via container)

const COLORS = {
  bg: 0x0a0e10,
  body: 0x262d35,
  bodyOutline: 0x4a5560,
  eyeBase: 0x0a0e10,
  idle:      { halo: 0x4a5560, eye: 0x9aa8b4 },
  listening: { halo: 0x3ac267, eye: 0xbbffcf },
  thinking:  { halo: 0xf2a03d, eye: 0xffd9a0 },
  speaking:  { halo: 0x3ab3ff, eye: 0xcfe9ff },
  error:     { halo: 0xf04060, eye: 0xffb0bc },
};

export function createHerbert() {
  // `root` is the layout-positioned container. `sway` is an INNER
  // container that the tick loop animates (tiny y-offset for a living
  // feel). Splitting them keeps the outer layout (set by main.js) from
  // getting clobbered every frame when the sway writes to .y.
  const root = new Container();
  const sway = new Container();
  root.addChild(sway);

  // Render order, back-to-front:
  //   halo  (state-coloured aura, pulses)
  //   body  (rounded head)
  //   mouth (seven bars; active during speaking)
  //   eyes  (left + right; morph per state)
  //   scans (horizontal scanlines overlay for CRT vibe)
  const halo = new Graphics();
  const body = new Graphics();
  const mouth = new Graphics();
  const leftEye = new Graphics();
  const rightEye = new Graphics();
  const scans = new Graphics();
  sway.addChild(halo, body, mouth, leftEye, rightEye, scans);

  // Center the drawing around (0,0) so container.x/y positions the midpoint
  const cx = 0;
  const cy = 0;

  // --- Static body + scanlines (drawn once) -------------------------

  drawBody(body, cx, cy);
  drawScanlines(scans, cx, cy);

  // --- Per-state animation bag -------------------------------------

  let state = 'idle';
  let t = 0; // accumulated delta ticks

  // Mouth amplitude per-bar. Lerps toward target each frame so the bars
  // feel alive instead of jumping. Seven bars = modest, readable mouth.
  const MOUTH_BARS = 7;
  const mouthLevels = new Array(MOUTH_BARS).fill(0);
  const mouthTargets = new Array(MOUTH_BARS).fill(0);
  let mouthTargetClock = 0;

  // Thinking eye scan phase
  let scanPhase = 0;

  // Error flicker
  let errorFlicker = 0;

  root.setState = (next) => {
    state = next;
    // Reset transient animations on entry so we don't inherit stale phase
    scanPhase = 0;
    errorFlicker = 0;
    if (state !== 'speaking') {
      for (let i = 0; i < MOUTH_BARS; i++) mouthLevels[i] = mouthTargets[i] = 0;
    }
  };

  root.tick = (delta) => {
    t += delta;

    // Halo: pulse amplitude + colour are state-driven
    drawHalo(halo, cx, cy, state, t);

    // Eyes: redraw per state
    drawEyes(leftEye, rightEye, cx, cy, state, t);

    // Mouth: animate only during speaking; otherwise a flat neutral line
    mouthTargetClock += delta;
    if (state === 'speaking' && mouthTargetClock > 4) {
      for (let i = 0; i < MOUTH_BARS; i++) {
        // Centre bars get a higher ceiling — mouths open most in the middle
        const centerBias = 1 - Math.abs(i - (MOUTH_BARS - 1) / 2) / MOUTH_BARS;
        mouthTargets[i] = Math.random() * 0.7 * centerBias + 0.15;
      }
      mouthTargetClock = 0;
    } else if (state !== 'speaking') {
      for (let i = 0; i < MOUTH_BARS; i++) mouthTargets[i] = 0;
    }
    // Lerp toward target
    for (let i = 0; i < MOUTH_BARS; i++) {
      mouthLevels[i] += (mouthTargets[i] - mouthLevels[i]) * 0.35;
    }
    drawMouth(mouth, cx, cy, mouthLevels, state);

    // Error flicker: stutter the whole container's alpha a touch
    if (state === 'error') {
      errorFlicker += delta * 0.4;
      root.alpha = 0.7 + 0.3 * Math.abs(Math.sin(errorFlicker * 3.3));
    } else {
      root.alpha = 1;
    }

    // Subtle sway so idle never feels frozen. Applied to the inner
    // container so the outer `root` position (set by main.js layout)
    // is never overwritten.
    sway.y = Math.sin(t * 0.03) * 1.5;
  };

  root.setState(state);
  return root;
}

// --- Draw helpers ---------------------------------------------------------

function drawBody(g, cx, cy) {
  g.clear();
  // Outer outline (pixel-style thick border)
  g.roundRect(cx - 36, cy - 28, 72, 60, 6).fill(COLORS.bodyOutline);
  // Inner face
  g.roundRect(cx - 33, cy - 25, 66, 54, 5).fill(COLORS.body);
  // Antenna
  g.rect(cx - 1, cy - 40, 2, 10).fill(COLORS.bodyOutline);
  g.rect(cx - 3, cy - 42, 6, 3).fill(COLORS.bodyOutline);
  // Chin / speaker slot recess (matte)
  g.roundRect(cx - 24, cy + 10, 48, 14, 3).fill(0x151a20);
}

function drawScanlines(g, cx, cy) {
  g.clear();
  const left = cx - 36;
  const width = 72;
  const top = cy - 28;
  const bottom = cy + 32;
  for (let y = top; y < bottom; y += 3) {
    g.rect(left, y, width, 1).fill({ color: 0x000000, alpha: 0.18 });
  }
}

function drawHalo(g, cx, cy, state, t) {
  const color = COLORS[state]?.halo ?? COLORS.idle.halo;
  // Pulse speed + amplitude differ by state so the character feels alive
  let pulse = 0;
  let base = 0.22;
  switch (state) {
    case 'idle':
      pulse = Math.sin(t * 0.04) * 0.05;
      base = 0.15;
      break;
    case 'listening':
      pulse = Math.sin(t * 0.18) * 0.25;
      base = 0.35;
      break;
    case 'thinking':
      pulse = Math.sin(t * 0.24) * 0.18 + Math.sin(t * 0.13) * 0.08;
      base = 0.3;
      break;
    case 'speaking':
      pulse = Math.sin(t * 0.3) * 0.2 + Math.sin(t * 0.17) * 0.1;
      base = 0.4;
      break;
    case 'error':
      pulse = Math.sin(t * 0.55) * 0.25;
      base = 0.35;
      break;
  }
  const alpha = Math.max(0.05, Math.min(0.75, base + pulse));
  g.clear();
  // Three concentric rounded rects, each dimmer and larger — cheap glow
  for (let i = 0; i < 3; i++) {
    const inset = -4 - i * 6;
    const aFactor = 1 - i * 0.3;
    g.roundRect(cx - 36 + inset, cy - 28 + inset, 72 - inset * 2, 60 - inset * 2, 8 + i * 2)
      .fill({ color, alpha: alpha * aFactor });
  }
}

function drawEyes(left, right, cx, cy, state, t) {
  const eyeColor = COLORS[state]?.eye ?? COLORS.idle.eye;
  const leftX = cx - 16;
  const rightX = cx + 16;
  const baseY = cy - 7;

  left.clear();
  right.clear();

  switch (state) {
    case 'idle': {
      // Small square dots, occasional blink
      const blink = Math.sin(t * 0.015) > 0.985 ? 1 : 0; // rare blink
      const h = blink ? 1 : 6;
      const yOff = blink ? 2 : 0;
      left.rect(leftX - 3, baseY + yOff, 6, h).fill(eyeColor);
      right.rect(rightX - 3, baseY + yOff, 6, h).fill(eyeColor);
      break;
    }
    case 'listening': {
      // Wide circular-ish eyes (8x8 rounded)
      left.roundRect(leftX - 4, baseY - 1, 8, 8, 2).fill(eyeColor);
      right.roundRect(rightX - 4, baseY - 1, 8, 8, 2).fill(eyeColor);
      // Shine highlight
      left.rect(leftX + 1, baseY, 2, 2).fill(0xffffff);
      right.rect(rightX + 1, baseY, 2, 2).fill(0xffffff);
      break;
    }
    case 'thinking': {
      // Eyes scan left-right; track each other
      const scanX = Math.sin(t * 0.12) * 3;
      left.roundRect(leftX - 3 + scanX, baseY, 6, 6, 2).fill(eyeColor);
      right.roundRect(rightX - 3 + scanX, baseY, 6, 6, 2).fill(eyeColor);
      break;
    }
    case 'speaking': {
      // Half-closed, focused eyes (thin horizontal rects with rounded ends)
      left.roundRect(leftX - 4, baseY + 2, 8, 3, 1).fill(eyeColor);
      right.roundRect(rightX - 4, baseY + 2, 8, 3, 1).fill(eyeColor);
      break;
    }
    case 'error': {
      // Little X marks — each drawn as two short diagonals approximated
      // by 3 stacked rectangles (pixel-art style)
      drawX(left, leftX, baseY, eyeColor);
      drawX(right, rightX, baseY, eyeColor);
      break;
    }
  }
}

function drawX(g, cx, cy, color) {
  // A 6x6 X built out of 3-pixel squares offset
  const size = 2;
  const off = [-2, 0, 2];
  for (const d of off) {
    g.rect(cx + d - size / 2, cy + d - size / 2, size, size).fill(color);
    g.rect(cx + d - size / 2, cy - d - size / 2, size, size).fill(color);
  }
}

function drawMouth(g, cx, cy, levels, state) {
  g.clear();
  const color = state === 'error' ? COLORS.error.eye : COLORS[state]?.eye ?? COLORS.idle.eye;
  const barW = 4;
  const gap = 2;
  const total = levels.length * barW + (levels.length - 1) * gap;
  const startX = cx - total / 2;
  const baseY = cy + 17;
  if (state === 'speaking') {
    for (let i = 0; i < levels.length; i++) {
      const h = Math.max(1, Math.round(levels[i] * 10));
      const x = startX + i * (barW + gap);
      g.rect(x, baseY + 5 - h, barW, h).fill(color);
    }
  } else {
    // Flat mouth line (just a thin bar) when not speaking
    const lineColor = state === 'error' ? COLORS.error.halo : COLORS.bodyOutline;
    g.rect(cx - 10, baseY + 4, 20, 1).fill(lineColor);
  }
}
