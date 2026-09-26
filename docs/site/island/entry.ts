// The docs home's room demo, built from the workbench's own components so it
// looks like the product because it is the product's rows. The parent page
// drives it: window.setStep(n) and window.setTheme('light' | 'dark').
import '@mdi/font/css/materialdesignicons.css'
import 'vuetify/styles'
import '@/style.css'

import { createApp, ref } from 'vue'
import { createVuetify } from 'vuetify'
import { aliases, mdi } from 'vuetify/iconsets/mdi'

import DocsRoom from './DocsRoom.vue'

const step = ref(0)
const vuetify = createVuetify({ icons: { defaultSet: 'mdi', aliases, sets: { mdi } }, theme: { defaultTheme: 'light' } })
const app = createApp(DocsRoom, { step })
app.use(vuetify).mount('#room')

declare global {
  interface Window { setStep: (n: number) => void; setTheme: (t: 'light' | 'dark') => void }
}
window.setStep = (n) => { step.value = n }
window.setTheme = (t) => {
  document.documentElement.dataset.theme = t
  vuetify.theme.global.name.value = t
}
const q = new URLSearchParams(location.search)
window.setTheme(q.get('theme') === 'dark' ? 'dark' : 'light')
window.setStep(Number(q.get('step') || 0))
