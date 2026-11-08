import '@/styles/fonts.css'
import '../src/style.css'

import { createApp } from 'vue'

import HarnessApp from './HarnessApp.vue'

import i18n, { localeReady } from '@/i18n'
import vuetify from '@/plugins/vuetify'

await localeReady
createApp(HarnessApp).use(vuetify).use(i18n).mount('#app')
