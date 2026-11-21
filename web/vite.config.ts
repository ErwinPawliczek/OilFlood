import { defineConfig } from 'vite';

export default defineConfig({
  server: {
    port: 5273,
    strictPort: true,
    host: true,
    proxy: { '/api': 'http://127.0.0.1:5200' },
  },
  preview: { port: 5273, proxy: { '/api': 'http://127.0.0.1:5200' } },
  build: { outDir: 'dist', chunkSizeWarningLimit: 3000 },
});
