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

import { createApp, createSSRApp, watch } from 'vue'

import { ensureStackBehind } from './router/pageStack'
import App from './App.vue'
import { installErrorReporter } from './errorReporter'
import { registerPwa } from './pwa'
import router from './router'

import { resolveRefs } from '@/api/addresses'
import { docAppShotUrl, getDocApp, getDocAppVersion } from '@/api/docApps'
import i18n, { loadPlatformSentences, localeReady } from '@/i18n'
import { armAfterFirstPaint } from '@/lib/afterFirstPaint'
import { firstPageReady, stylesheetsLoaded } from '@/lib/bootReady'
import { markTitleBarOverlay } from '@/lib/desktopApp'
import { behaveAsDesktopApp } from '@/lib/desktopNative'
import { answerDocAppsWith } from '@/lib/docApps'
import { watchInstallPrompt } from '@/lib/pwaInstall'
import { installRefChipKeys } from '@/lib/refChip'
import { answerRefsWith } from '@/lib/refDirectory'
// Plugins
import { registerPlugins } from '@/plugins'
import { installAppRuntime } from '@/plugins/runtime'
import vuetify from '@/plugins/vuetify'
import { persistProjectList } from '@/query/persist'
import AccountService from '@/services/account'
import { watchForStaleBuild } from '@/services/staleBuild'

AccountService.init()

// 上次的项目清单先放回缓存，左边栏冷打开时当场就有。
persistProjectList()

// In the desktop app the frame is not selectable text and has no browser menu.
behaveAsDesktopApp()

// macOS 桌面版把红黄绿三颗窗口按钮画在页面上：给 <html> 挂个类，样式表据此让位
// （style.css 的 --window-buttons-inset）。别的平台不挂，值和位置都不动。
markTitleBarOverlay()

// 公开页的 HTML 是构建时预渲染的（scripts/prerender.mjs）。启动脚本认定来的正是它渲染
// 给的那位访客（index.html 的 data-boot="page"）时，应用在这份 HTML 上接管（hydrate），
// 页面一帧也不重画；别的情况它被骨架盖着，照常从头挂载，挂载时清掉。
const hydrating = document.documentElement.dataset.boot === 'page'
const app = hydrating ? createSSRApp(App) : createApp(App)

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
// 和画哪一页无关、整段会话里一直在跑的那几件（主题跟随、页面标题、登录后的实时连接、全局按键）。
installAppRuntime(app, router)

// 挂载等三件事：
// - 当前语言那份语言包。它和入口分开取（见 i18n/index.ts），没到就挂载会先画出一片裸键。
//   取不到（离线、构建对不上）也照常挂载，i18n 那边退回入口自带的 zh-CN，界面是齐的。
// - 第一页：会话恢复完、第一次导航走完。以前挂载在它前面，先画出一副空外壳（没登录的人
//   在首页上先看到工作台的左栏和「登录」），续签那一下还要弹「正在恢复登录状态」。现在
//   这段时间里一直是按落点画的启动骨架（index.html），挂上去的就是那一页。
// - 样式表。首屏不再等它们（见 lib/bootReady.ts），挂载要等。
// 哪一样都不会让挂载失败，都有自己的期限：挂载不能卡在网络上。
void Promise.all([
  localeReady,
  // 第一页定下来之后、挂载之前，把页面栈垫进浏览器历史（见 router/pageStack.ts）：
  // 手机冷开落在任务页时，身后那条来路要在系统返回键按下去之前就位，而中间那几屏
  // 正被启动骨架盖着，谁也看不见。
  firstPageReady(AccountService.sessionRestored, router.isReady()).then(() => ensureStackBehind(router)),
  stylesheetsLoaded(),
]).then(() => {
  app.mount('#app')

  // The cold-start skeleton lives in index.html and only covers the gap before
  // this line. Mounting has just drawn the page it stood for, so drop it now, in
  // the same frame, along with the boot mode that kept #app hidden under it.
  delete document.documentElement.dataset.boot
  document.getElementById('sx-boot-skeleton')?.remove()

  // 后端说的报错和系统消息不挡第一页，页面画出来再取（见 i18n/index.ts）。
  void loadPlatformSentences()
})

registerPwa(router)
// 安装机会（beforeinstallprompt）来得比任何页面都早——早到用户还没来得及打开
// 下载页。所以在启动时就把它接住，下载页上的「添加到主屏幕」才有得用。
watchInstallPrompt()
