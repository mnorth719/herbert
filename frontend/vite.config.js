// Vite config — ships the built frontend into src/herbert/web/static/
// so FastAPI's static mount picks it up automatically.

import { defineConfig } from 'vite';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  root: __dirname,
  base: './',
  build: {
    outDir: resolve(__dirname, '../src/herbert/web/static'),
    emptyOutDir: true,
    sourcemap: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/ws': {
        target: 'ws://127.0.0.1:8080',
        ws: true,
        changeOrigin: true,
      },
      '/healthz': 'http://127.0.0.1:8080',
      '/api': 'http://127.0.0.1:8080',
    },
  },
});
