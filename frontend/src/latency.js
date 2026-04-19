// Corner latency badge — updates on LatencyMiss events (R6a).

const el = () => document.getElementById('latency');

let clearTimer = null;

export function onLatencyMiss({ stage, actual_ms, ceiling_ms }) {
  const node = el();
  node.classList.add('miss');
  node.textContent = `MISS ${stage} ${actual_ms}ms (ceiling ${ceiling_ms})`;
  if (clearTimer) clearTimeout(clearTimer);
  clearTimer = setTimeout(() => {
    node.classList.remove('miss');
    node.textContent = '';
  }, 6000);
}

export function onExchangeLatency({ total_ms }) {
  const node = el();
  if (!node.classList.contains('miss')) {
    node.textContent = `${total_ms}ms`;
  }
}
