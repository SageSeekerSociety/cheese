/**
 * main.ts
 *
 * Bootstraps Vuetify and other plugins then mounts the App`
 */

import 'editorjs-latex/dist/editorjs-latex.bundle.css'
import 'katex/dist/katex.min.css'
import '@/styles/content.scss'
import '@/styles/fonts.css'

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

import { createApp, watch } from 'vue'
import i18next from 'i18next'
import { z } from 'zod'
import { zodI18nMap } from 'zod-i18n-map'
import englishTranslation from 'zod-i18n-map/locales/en/zod.json'
// Import your language translation files
import translation from 'zod-i18n-map/locales/zh-CN/zod.json'

import App from './App.vue'
import { installErrorReporter } from './errorReporter'
import { registerPwa } from './pwa'

import i18n from '@/i18n'
// Plugins
import { registerPlugins } from '@/plugins'
import vuetify from '@/plugins/vuetify'
import AccountService from '@/services/account'
import { clearStaleBuildGuard, watchForStaleBuild } from '@/services/staleBuild'

AccountService.init()

const app = createApp(App)

// A tab open across a deploy asks for chunks the new build does not
// serve; recover it before the failure reaches the user as a dead click.
watchForStaleBuild()

// 现场即事实记录: browser-side errors report into the open topic's 现场 so
// agents (who can't read a user's console) can debug them. See errorReporter.ts.
installErrorReporter(app)

i18next.init({
  lng: i18n.global.locale.value,
  resources: {
    'zh-CN': {
      zod: translation,
    },
    en: { zod: englishTranslation },
  },
})
z.setErrorMap(zodI18nMap)

watch(
  i18n.global.locale,
  (locale) => {
    void i18next.changeLanguage(locale)
    dayjs.locale(locale === 'en' ? 'en' : 'zh-cn')
    vuetify.locale.current.value = locale === 'en' ? 'en' : 'zhHans'
  },
  { immediate: true }
)

registerPlugins(app)
app.mount('#app')

// Mounting is the proof that a reload recovered the tab, so the one-shot
// guard reopens for the next release.
clearStaleBuildGuard()
registerPwa()
