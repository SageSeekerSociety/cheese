import { expect, test, type Page } from '@playwright/test'

// 拖侧栏右缘时，分界线得跟着指针走：不跟手（拖到了别处、松手后还自己滑一段）就是
// 这一条要拦的回归。
//
// 只有真浏览器量得出来：宽度带过渡时每一次指针移动都重新起一段过渡，栏永远缓在
// 半路；happy-dom 没有布局，宽度是多少都看不出来。所以这里挂真的 SecondaryNavigation
// 和产品样式，只有接口是假的，不碰后端 —— 这一栏本来也不取数。

const fixture = `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><body>
<div id="fixture"></div><script type="module">
import {createApp,h} from '/node_modules/.vite/deps/vue.js';
import pinia from '/src/stores/index.ts';
import {VApp,VMain} from '/node_modules/.vite/deps/vuetify_components.js';
import SecondaryNavigation from '/src/components/common/Navigation/SecondaryNavigation.vue';
import vuetify from '/src/plugins/vuetify.ts';
import i18n from '/src/i18n/index.ts';
import '/src/styles/common.scss';
import '/src/style.css';
createApp({render:()=>h(VApp,{class:'h-100'},()=>[
  h(SecondaryNavigation,{},{default:()=>h('div',{style:'padding:16px'},'侧栏')}),
  h(VMain,{class:'bg-background h-100'},()=>h('div',{class:'app-pane h-100'},'内容区')),
])}).use(vuetify).use(i18n).use(pinia).mount('#fixture');
</script></body></html>`

/** 抽屉右缘现在在哪儿。 */
function edge(page: Page) {
  return page.evaluate(() => document.getElementById('secondary-sidebar')!.getBoundingClientRect().right)
}

test('拖着侧栏右缘走的时候，分界线跟着指针走', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.addInitScript(() => localStorage.clear())
  await page.route('**/__sidebar-resizer__', (route) => route.fulfill({ contentType: 'text/html', body: fixture }))
  await page.goto('/__sidebar-resizer__', { waitUntil: 'commit' })
  await page.waitForSelector('.sidebar-resizer', { timeout: 60_000 })

  // 指针从右缘出发，一步 24px 往右拖，每走一步量一次：分界线此刻应该已经跟到了。
  // 一步之间差 24px 就是「不跟手」——宽度带 0.2 秒过渡时，一步只走到三分之一上下。
  const start = await edge(page)
  const y = 400
  await page.mouse.move(start, y)
  await page.mouse.down()
  let worst = 0
  for (let step = 1; step <= 5; step++) {
    const x = start + step * 24
    await page.mouse.move(x, y)
    worst = Math.max(worst, Math.abs(x - (await edge(page))))
  }
  expect(worst, '拖着走的时候分界线落在指针后面').toBeLessThanOrEqual(2)

  // 松手就停在指针停下的地方，不再自己滑一段。
  await page.mouse.up()
  await expect.poll(() => edge(page), { timeout: 2_000 }).toBeCloseTo(start + 120, -1)
})
