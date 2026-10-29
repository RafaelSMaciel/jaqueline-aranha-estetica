import { defineConfig } from 'vite'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [tailwindcss()],
  base: '/static/dist/',
  build: {
    manifest: 'manifest.json',
    outDir: 'aranha_estetica/static/dist',
    emptyOutDir: true,
    // Uma entrada por tela: o nome sai com hash no manifest (cache do navegador e do service
    // worker nunca serve JS velho depois de um deploy; o static/js/ antigo nao tinha versao).
    rollupOptions: {
      input: {
        app: 'aranha_estetica/static/src/js/app.js',
        wizard: 'aranha_estetica/static/src/js/wizard.js',
        // abas, menu "⋯", modais e Ctrl+K das telas da equipe (painel e portal da profissional)
        painel: 'aranha_estetica/static/src/js/painel.js',
        'painel-busca': 'aranha_estetica/static/src/js/painel-busca.js',
        // select de status e lote de Aprovar/Recusar das listas do painel (agenda e visao geral)
        'painel-status': 'aranha_estetica/static/src/js/painel-status.js',
        'painel-calendario': 'aranha_estetica/static/src/js/painel-calendario.js',
        webpush: 'aranha_estetica/static/src/js/webpush.js',
      },
    },
  },
  server: { origin: 'http://localhost:5173' },
})
