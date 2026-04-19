// Fake boot sequence — retro-style line-by-line text reveal.
//
// Gated by a per-boot marker on the server so it doesn't replay on
// every browser refresh. For now we just run once per page load; the
// daemon-side gate lands with the BootSequenceStart event in Unit 11.

const DEFAULT_LINES = [
  'HERBERT v0.1',
  '',
  '> memory ok',
  '> audio ok',
  '> anthropic reachable',
  '> ready.',
  '',
  'hello.',
];

export async function runBootSequence({ lines = DEFAULT_LINES, linePauseMs = 180 } = {}) {
  const el = document.getElementById('boot');
  if (!el) return;
  el.classList.remove('hidden');
  el.textContent = '';
  for (const line of lines) {
    el.textContent += line + '\n';
    await new Promise((r) => setTimeout(r, linePauseMs));
  }
  await new Promise((r) => setTimeout(r, 600));
  el.classList.add('hidden');
}
