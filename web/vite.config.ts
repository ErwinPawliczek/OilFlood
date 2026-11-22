import { defineConfig } from 'vite';
import { resolve } from 'node:path';

const API = process.env.VITE_API || 'http://127.0.0.1:5200';
const PORT = Number(process.env.VITE_PORT || 5273);

export default defineConfig({
  server: {
    port: PORT,
    strictPort: true,
    host: true,
    proxy: { '/api': API },
    // docs/METHODOLOGY.md is inlined into /methodology at build time
    fs: { allow: [resolve(__dirname, '..')] },
  },
  preview: { port: PORT, proxy: { '/api': API } },
  build: {
    outDir: 'dist',
    chunkSizeWarningLimit: 3000,
    // two pages, one build: the globe (/) and the methodology explainer (/methodology/)
    rollupOptions: { input: { main: resolve(__dirname, 'index.html'), methodology: resolve(__dirname, 'methodology/index.html') } },
  },
});
