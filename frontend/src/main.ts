/**
 * main.ts
 *
 * Bootstraps Vuetify and other plugins then mounts the App`
 */

import 'editorjs-latex/dist/editorjs-latex.bundle.css'
import 'katex/dist/katex.min.css'
import '@/styles/content.scss'
import '@/styles/fonts.css'
import '@/styles/md-content.css'

import 'dayjs/locale/zh-cn'
import 'wc-waterfall'

import dayjs from 'dayjs'
import duration from 'dayjs/plugin/duration'
import relativeTime from 'dayjs/plugin/relativeTime'
import timezone from 'dayjs/plugin/timezone'
import utc from 'dayjs/plugin/utc'
dayjs.locale('zh-cn')
dayjs.extend(utc)
dayjs.extend(timezone)
dayjs.extend(duration)
dayjs.extend(relativeTime)

// Components
// Composables
// Fusion merge (C): our topic/agent views (grafted into the cheese shell) use
// cheesex's design tokens (--ink/--accent) and cheesex's identity (handle).
// Load our stylesheet, and bridge the logged-in product account -> a cheesex
// identity so our views have a handle (= username) when embedded here.
import './style.css'

import { createApp } from 'vue'
import i18next from 'i18next'
import { z } from 'zod'
import { zodI18nMap } from 'zod-i18n-map'
// Import your language translation files
import translation from 'zod-i18n-map/locales/zh-CN/zod.json'

import App from './App.vue'
import { installErrorReporter } from './errorReporter'
import { registerPwa } from './pwa'

// Plugins
import { registerPlugins } from '@/plugins'
import AccountService from '@/services/account'

try {
  const raw = localStorage.getItem('user')
  const existingMe = localStorage.getItem('cheesex.me')
  // Re-derive whenever there's no mirror yet, or a mirror written before `id`
  // was added to it (2026-08-10) — those stale entries never self-heal
  // otherwise, since this bridge only runs once per fresh 'user' write.
  const needsId = existingMe ? !JSON.parse(existingMe)?.id : false
  if (raw && (!existingMe || needsId)) {
    const u = JSON.parse(raw)
    localStorage.setItem(
      'cheesex.me',
      JSON.stringify({ id: String(u.id), handle: u.username, name: u.nickname || u.username, token: '' })
    )
  }
} catch {
  // non-fatal
}

AccountService.init()

const app = createApp(App)

// 现场即事实记录: browser-side errors report into the open topic's 现场 so
// agents (who can't read a user's console) can debug them. See errorReporter.ts.
installErrorReporter(app)

registerPlugins(app)
app.mount('#app')

// Register the service worker (offline shell + auto-update). No-op where the
// browser has no SW support or the build produced none (dev).
registerPwa()

// Initialize i18next
i18next.init({
  lng: 'zh-CN',
  resources: {
    'zh-CN': {
      zod: translation,
    },
  },
})
z.setErrorMap(zodI18nMap)
