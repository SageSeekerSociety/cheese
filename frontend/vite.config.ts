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
      // BACKEND_URL overrides the target (e.g. a throwaway demo backend).
      '/api': {
        target: process.env.BACKEND_URL ?? 'http://localhost:8099',
        changeOrigin: true,
        ws: true,
      },
      // The self-hosted device connector lives at the origin root (`/connector/*`,
      // incl. the 现场 viewer WS `/connector/session/{sid}/screen`), not under /api.
      '/connector': {
        target: process.env.BACKEND_URL ?? 'http://localhost:8099',
        changeOrigin: true,
        ws: true,
      },
      // Fusion merge (A5): the original product API lives at the ROOT, not under
      // /api. Proxy those paths to the merged backend so the shell's ProductView
      // can hit real /users/auth/login + /spaces + /teams.
      ...Object.fromEntries(
        ['/users', '/spaces', '/teams', '/tasks', '/questions'].map((p) => [
          p,
          { target: process.env.BACKEND_URL ?? 'http://localhost:8099', changeOrigin: true },
        ]),
      ),
    },
  },
})
