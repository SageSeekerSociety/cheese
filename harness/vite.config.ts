import { fileURLToPath } from 'node:url'
import vue from '@vitejs/plugin-vue'
import vuetify from 'vite-plugin-vuetify'
import { defineConfig } from 'vite'
const src = fileURLToPath(new URL('../src', import.meta.url))
export default defineConfig({
  root: fileURLToPath(new URL('.', import.meta.url)),
  base: './',
  publicDir: fileURLToPath(new URL('../public', import.meta.url)),
  plugins: [vue(), vuetify({ autoImport: true })],
  define: { 'import.meta.env.VITE_API_GENERATION': '"0"' },
  resolve: {
    alias: [
      { find: '@/api/teams', replacement: fileURLToPath(new URL('./fakeTeams.ts', import.meta.url)) },
      { find: /^@\/plugins\/dialog$/, replacement: fileURLToPath(new URL('./fakeDialog.ts', import.meta.url)) },
      { find: 'virtual:pwa-register', replacement: fileURLToPath(new URL('./pwaStub.ts', import.meta.url)) },
      { find: /^@\/services\/account$/, replacement: fileURLToPath(new URL('./accountStub.ts', import.meta.url)) },
      { find: /^@\/errorReporter$/, replacement: fileURLToPath(new URL('./reporterStub.ts', import.meta.url)) },
      { find: '@', replacement: src },
    ],
    extensions: ['.js', '.json', '.mjs', '.ts', '.tsx', '.vue'],
  },
  build: { outDir: process.env.OUT || 'dist', emptyOutDir: true, assetsInlineLimit: 100000000, cssCodeSplit: false, copyPublicDir: false, rolldownOptions: { output: { codeSplitting: false } } },
})
