import { defineConfig } from 'vite'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [tailwindcss()],
  base: '/static/dist/',
  build: {
    manifest: 'manifest.json',
    outDir: 'aranha_estetica/static/dist',
    emptyOutDir: true,
    rollupOptions: {
      input: 'aranha_estetica/static/src/js/app.js',
    },
  },
  server: { origin: 'http://localhost:5173' },
})
