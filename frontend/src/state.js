// Character view: draws the pixel-art placeholder for each pipeline state.
//
// v1 uses `PIXI.Graphics` rectangles (4 filled colours per plan) rather
// than a sprite-sheet, so the plumbing is testable long before real art
// lands. Swapping in a `PIXI.AnimatedSprite` later is a 20-line change
// — the `setState` hook stays put.

import { Container, Graphics } from 'pixi.js';

const COLORS = {
  idle: 0x4a5560,
  listening: 0x3ac267,
  thinking: 0xf2a03d,
  speaking: 0x3ab3ff,
  error: 0xf04060,
};

const BOX_SIZE = 96;

export function createCharacter() {
  const container = new Container();

  const box = new Graphics();
  box.rect(-BOX_SIZE / 2, -BOX_SIZE / 2, BOX_SIZE, BOX_SIZE).fill(COLORS.idle);
  container.addChild(box);

  // Simple breathing-style scale animation to suggest aliveness.
  // Paused during `idle` / `error`; active during the three working states.
  let phase = 0;
  let active = false;
  container.tick = (delta) => {
    if (!active) {
      container.scale.set(1);
      return;
    }
    phase += delta * 0.08;
    const s = 1 + 0.08 * Math.sin(phase);
    container.scale.set(s);
  };

  container.setState = (state) => {
    const color = COLORS[state] ?? COLORS.idle;
    box.clear().rect(-BOX_SIZE / 2, -BOX_SIZE / 2, BOX_SIZE, BOX_SIZE).fill(color);
    active = state === 'listening' || state === 'thinking' || state === 'speaking';
    if (!active) phase = 0;
  };

  return container;
}
