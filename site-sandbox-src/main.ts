import './fakeServer'

import '@/styles/fonts.css'
import '../src/style.css'

import { createApp } from 'vue'
import { createPinia } from 'pinia'

import { setLocale } from '@/i18n'
import vuetify from '@/plugins/vuetify'

import Harness from './Harness.vue'

setLocale('zh-CN')
createApp(Harness).use(createPinia()).use(vuetify).mount('#app')
