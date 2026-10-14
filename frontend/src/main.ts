/**
 * main.ts
 *
 * Bootstraps Vuetify and other plugins then mounts the App`
 */

import 'editorjs-latex/dist/editorjs-latex.bundle.css'
import '@/styles/content.scss'
import '@/styles/fonts.css'
import '@/styles/docBlocks.css'

import 'dayjs/locale/zh-cn'

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

import App from './App.vue'
import { installErrorReporter } from './errorReporter'
import { registerPwa } from './pwa'
import router from './router'

import { resolveRefs } from '@/api/addresses'
import { docAppShotUrl, getDocApp, getDocAppVersion } from '@/api/docApps'
import i18n, { localeReady } from '@/i18n'
import { armAfterFirstPaint } from '@/lib/afterFirstPaint'
import { behaveAsDesktopApp } from '@/lib/desktopNative'
import { answerDocAppsWith } from '@/lib/docApps'
import { watchInstallPrompt } from '@/lib/pwaInstall'
import { installRefChipKeys } from '@/lib/refChip'
import { answerRefsWith } from '@/lib/refDirectory'
// Plugins
import { registerPlugins } from '@/plugins'
import vuetify from '@/plugins/vuetify'
import { persistProjectList } from '@/query/persist'
import AccountService from '@/services/account'
import { watchForStaleBuild } from '@/services/staleBuild'

AccountService.init()

// 上次的项目清单先放回缓存，左边栏冷打开时当场就有。
persistProjectList()

// In the desktop app the frame is not selectable text and has no browser menu.
behaveAsDesktopApp()

const app = createApp(App)

// A tab open across a deploy asks for chunks the new build does not
// serve; recover it before the failure reaches the user as a dead click.
watchForStaleBuild()

// 现场即事实记录: browser-side errors report into the open topic's 现场 so
// agents (who can't read a user's console) can debug them. See errorReporter.ts.
installErrorReporter(app)

// 消息和文档里的 @人、#话题、文件 chip：Enter / 空格按下去和点一下一样（lib/refChip.ts）。
installRefChipKeys()
answerRefsWith(resolveRefs)
// 文档里的小程序：代码、版本和截图从哪里取（lib/docApps.ts）。
answerDocAppsWith({ app: getDocApp, version: getDocAppVersion, shot: docAppShotUrl })

watch(
  i18n.global.locale,
  (locale) => {
    dayjs.locale(locale === 'en' ? 'en' : 'zh-cn')
    vuetify.locale.current.value = locale === 'en' ? 'en' : 'zhHans'
  },
  { immediate: true }
)

// 首帧之后才发的那一批（角标、装饰类）跟着这台 router 排。装插件会把 router 装上并
// 开始第一次跳转，所以在这一行之前装（见 lib/afterFirstPaint.ts）。
armAfterFirstPaint(router)

registerPlugins(app)

// 界面上的每个字都来自当前语言那份语言包，它和入口分开取（见 i18n/index.ts），所以
// 挂载等它——没到就挂载会先画出一片裸键。取不到（离线、构建对不上）也照常挂载，
// i18n 那边退回入口自带的 zh-CN，界面是齐的；挂载不能卡在网络上。挂载之后这个
// promise 早已落定，别处不用再等。
void localeReady.then(() => {
  app.mount('#app')

  // The cold-start skeleton shell lives in index.html and is only there to cover
  // the gap before this line. Mounting has just painted the real shell, so drop
  // it now — a fresh node with fresh token values arriving in the same frame
  // means no flash even on a fast connection. See index.html for the markup.
  document.getElementById('sx-boot-skeleton')?.remove()
})

registerPwa(router)
// 安装机会（beforeinstallprompt）来得比任何页面都早——早到用户还没来得及打开
// 下载页。所以在启动时就把它接住，下载页上的「添加到主屏幕」才有得用。
watchInstallPrompt()
