/**
 * 「一件事」演示的入口（one-thing-demo.html，构建配置 vite.one-thing-demo.config.mts）。
 *
 * 一页静态演示：不起路由、不登录、不连后端。画面用产品自己的组件、主题和设计令牌，
 * 数据是 `data.ts` 里的样例。几个产品组件里有会去取数的分支，演示后端在 fetch 那一层
 * 把 /api/... 一律答成 404，请求不出这一页。
 */
import '@/styles/content.scss'
import '@/styles/fonts.css'
import '@/style.css'
import './demo.css'

import { createApp } from 'vue'
import { createPinia } from 'pinia'

import DemoApp from './DemoApp.vue'

import i18n, { localeReady } from '@/i18n'
import vuetify from '@/plugins/vuetify'
import { applyResolvedTheme } from '@/theme'
import { installDemoBackend } from '@/views/demo/demoBackend'
import { demoRouter } from '@/views/demo/demoRouter'

installDemoBackend()

// ?theme=light|dark 选主题（截图和嵌入时用）；HTML 里那段启动脚本已经按它打好了
// data-theme，这里告诉 Vuetify。
const asked = new URLSearchParams(location.search).get('theme')
if (asked === 'dark' || asked === 'light') {
  applyResolvedTheme(asked)
  vuetify.theme.change(asked)
}

// 演示的文字是中文，界面上产品组件的字也得是中文，不跟浏览器语言走。
void localeReady.then(() => {
  i18n.global.locale.value = 'zh-CN'
  createApp(DemoApp).use(vuetify).use(i18n).use(createPinia()).use(demoRouter()).mount('#app')
})
