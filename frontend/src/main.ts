import { createApp } from 'vue'
import App from './App.vue'
import { router } from './router'
import { installExperimentalGuard } from './exp'
import vuetify from './plugins/vuetify'
import './style.css'

// 功能旗: ?exp=true 进入内测态并在应用内导航间保持粘性 (exp.ts)。
installExperimentalGuard(router)

createApp(App).use(router).use(vuetify).mount('#app')
