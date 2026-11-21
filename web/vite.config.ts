import { defineConfig } from 'vite';

const API = process.env.VITE_API || 'http://127.0.0.1:5200';

export default defineConfig({
  server: {
    port: 5273,
    strictPort: true,
    host: true,
    proxy: { '/api': API },
  },
  preview: { port: 5273, proxy: { '/api': API } },
  build: { outDir: 'dist', chunkSizeWarningLimit: 3000 },
});
