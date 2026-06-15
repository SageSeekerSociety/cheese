import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vuetify, { transformAssetUrls } from 'vite-plugin-vuetify'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    vue({ template: { transformAssetUrls } }),
    // Vuetify auto-import + treeshaking (vite-plugin-vuetify).
    vuetify({ autoImport: true }),
  ],
  server: {
    proxy: {
      // Proxy both HTTP REST and WebSocket (`/api/topics/{id}/chat`)
      // to the backend dev server. `ws: true` enables WS upgrade proxying.
      '/api': {
        target: 'http://localhost:8099',
        changeOrigin: true,
        ws: true,
      },
    },
  },
})
