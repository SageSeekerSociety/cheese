// Probe-only vite config: same app, but proxied to an EPHEMERAL backend so UI
// changes can be verified end-to-end without touching the running dogfood
// stack (8099/5173). Used by scripts/probe_reactions.py.
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'
import vuetify, { transformAssetUrls } from 'vite-plugin-vuetify'

const target = process.env.PROBE_API_TARGET || 'http://127.0.0.1:8098'

export default defineConfig({
  plugins: [vue({ template: { transformAssetUrls } }), vuetify({ autoImport: true })],
  server: {
    port: 5175,
    strictPort: true,
    proxy: {
      '/api': { target, changeOrigin: true, ws: true },
    },
  },
})
