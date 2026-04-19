// WebSocket client with exponential-backoff reconnect.
//
// Emits every inbound event through `onEvent(evt)`. Connection-state
// changes come through `onConnectionChange(state)` where state is one
// of "connecting" | "open" | "closed" | "error". The token, if any, is
// appended as ?token= so the QR-scan flow works out of the box.

export function connectWs({ onEvent, onConnectionChange, token } = {}) {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const tokenSuffix = token ? `?token=${encodeURIComponent(token)}` : '';
  const url = `${proto}//${location.host}/ws${tokenSuffix}`;

  let ws = null;
  let attempt = 0;
  let closed = false;

  const notify = (state) => onConnectionChange && onConnectionChange(state);

  function open() {
    if (closed) return;
    notify('connecting');
    ws = new WebSocket(url);
    ws.onopen = () => {
      attempt = 0;
      notify('open');
    };
    ws.onmessage = (ev) => {
      if (!onEvent) return;
      try {
        onEvent(JSON.parse(ev.data));
      } catch (err) {
        console.warn('ws: malformed payload', err, ev.data);
      }
    };
    ws.onerror = () => notify('error');
    ws.onclose = () => {
      if (closed) return;
      notify('closed');
      const delay = Math.min(30000, 500 * 2 ** attempt);
      attempt += 1;
      setTimeout(open, delay);
    };
  }

  open();

  return {
    close() {
      closed = true;
      if (ws) ws.close();
    },
  };
}
