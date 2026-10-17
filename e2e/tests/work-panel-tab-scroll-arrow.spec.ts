import { expect, test, type Page } from '@playwright/test'

// 页签条一屏放不下就横滚；滚到哪一端，那一端浮出一颗 44×44 的箭头。箭头里那颗 20px
// 的记号落在离栏边 12–32px 这一段，而它底下原来只有一层 40px 宽、最里面一头还是透明的
// 淡出——页签的图标和字就从箭头底下透上来，和箭头叠在一起。
//
// 记号那一块必须是实心的，而这件事只有真版面看得出来（happy-dom 没有布局）。这里挂真的
// PanelTabs 和产品样式，对同一块拍两次：一次原样，一次把整条页签栏 `visibility: hidden`
// （盒子一个都不动，只是不画了）。两次的 PNG 必须逐字节相同——记号底下实心的话，后面有
// 没有页签就画成同一个样子。把渐变改回旧的 40px、不留实心段，两次就不同。
// 一格页签最窄也有 45px（图标 16 + 间距 5 + 左右各 12），所以 14 格在两种宽度下都
// 一定滚得动——不带中文字体跑的机器上，字宽是 0，页签只剩这个下限。
const LABELS = [
  '对话', '改动', '文件', '现场', '演示', '文档', '预览',
  '资料', '任务', '反馈', '设置', '日志', '成员', '定时',
]

function fixture(width: number, phone: boolean): string {
  const tabs = LABELS.map((label, i) => ({ key: `k${i}`, label, icon: 'mdi-file-document-outline' }))
  // 页签条自己刷一层扎眼的底色：该被挡住的是图标和字，可这台机器（和任何没装中文字体
  // 的机器）上字宽是 0、图标字体也未必在，底下其实什么都不画——照原样跑，这个用例在旧
  // 样式下也会绿，等于量了个空。刷上底色之后，「记号底下不透东西」这句话在任何机器上
  // 都是同一件事。这层底色只是夹具，不进产品样式。
  return `<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<style>.tabbar { background: #ff00ff; }</style>
<body style="margin:0">
<div id="fixture"></div><script type="module">
import {createApp,h} from '/node_modules/.vite/deps/vue.js';
import {VApp} from '/node_modules/.vite/deps/vuetify_components.js';
import PanelTabs from '/src/components/panels/PanelTabs.vue';
import vuetify from '/src/plugins/vuetify.ts';
import i18n,{setLocale} from '/src/i18n/index.ts';
import '/src/style.css';
setLocale('zh-CN');
const tabs=${JSON.stringify(tabs)};
createApp({setup(){return()=>h(VApp,{},{default:()=>h('div',{id:'stage',style:{display:'flex',width:'${width}px',background:'var(--surface)'}},[
  h(PanelTabs,{tabs,active:'k0',phone:${phone},panelId:'panel',style:{width:'${width}px'}})])})}}).use(vuetify).use(i18n).mount('#fixture');
</script></body></html>`
}

/** 滚到某一端，并等那一端的箭头露出来、另一端的退回去。 */
async function showArrow(page: Page, side: 'left' | 'right') {
  await page.evaluate((to) => {
    const bar = document.querySelector<HTMLElement>('.tabbar')!
    // 滚到头，那一端的箭头就退回去：右边还有内容时才画右边那颗。
    bar.scrollLeft = to === 'right' ? 0 : bar.scrollWidth
  }, side)
  await expect(page.locator(`.tabbar-host__arrow--${side}`)).toBeVisible()
  await expect(page.locator(`.tabbar-host__arrow--${side === 'left' ? 'right' : 'left'}`)).toHaveCount(0)
}

/** 箭头记号那一块的截图。`hideTabs` 只把页签画不画关掉，盒子一个都不动。 */
async function markShot(page: Page, side: 'left' | 'right', hideTabs: boolean) {
  await page.evaluate((hide) => {
    document.querySelector<HTMLElement>('.tabbar')!.style.visibility = hide ? 'hidden' : ''
  }, hideTabs)
  return await page.locator(`.tabbar-host__arrow--${side} .v-icon`).screenshot()
}

for (const c of [
  { title: '窄面板（面板宽 500）', width: 500, phone: false },
  { title: '手机（宽 390、phone 那一档）', width: 390, phone: true },
]) {
  test(`${c.title}：两端箭头底下都看不到页签`, async ({ page }) => {
    await page.setViewportSize({ width: c.width, height: 600 })
    await page.route('**/__panel-tabs-arrow__', (route) =>
      route.fulfill({ contentType: 'text/html', body: fixture(c.width, c.phone) })
    )
    await page.goto('/__panel-tabs-arrow__', { waitUntil: 'commit' })
    await expect(page.getByRole('tablist')).toBeVisible({ timeout: 60_000 })

    for (const side of ['right', 'left'] as const) {
      await showArrow(page, side)
      const shown = await markShot(page, side, false)
      const hidden = await markShot(page, side, true)
      expect(shown.equals(hidden), `${c.title} ${side === 'right' ? '右' : '左'}端：箭头底下透出了页签`).toBe(true)
      await markShot(page, side, false)
    }
  })
}
