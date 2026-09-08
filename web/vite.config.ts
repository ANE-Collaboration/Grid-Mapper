import { defineConfig } from 'vite';

export default defineConfig(({ command }) => ({
  base: './',
  publicDir: command === 'build' ? '.generated-build' : '.generated',
  server: {
    port: 5173,
    host: true,
    headers: {
      'Accept-Ranges': 'bytes'
    }
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    sourcemap: true,
    rollupOptions: {
      output: {
        manualChunks: {
          maplibre: ['maplibre-gl']
        }
      }
    }
  }
}));
