import { expect, test, type Page } from '@playwright/test'

// 话题页、任务页、私聊页里那一排「对话 | 工作面板」不许把外面那一层变成横向可滚的。
//
// 收起的工作面板是一张浮层，整块被 translateX(100%) 推到这一排的右边界外面。这一排以前
// 是可滚的，于是收起的面板里任何一次 scrollIntoView（面板自己的滚动、聚焦、锚点）都会把
// 外面最近的那个裁剪容器（话题页那一层 `overflow: hidden`）横向滚过去，而且停在那儿：
// 对话被推出左边、右边空一片、任务页头只剩最后一截。用户 2026-11-04 报的就是它。
//
// 只有真浏览器量得出来：happy-dom 不排版，scrollWidth 恒等于 clientWidth，这一条在单测里
// 永远绿。这里挂真的 DmViewView（和频道页共用 `topic-panes.css` 和同一套浮层）和产品样式，
// 外面再套一层和话题页一样的裁剪容器。

const fixture = `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><body>
<div id="fixture"></div><script type="module">
import {createApp,h} from '/node_modules/.vite/deps/vue.js';
import pinia from '/src/stores/index.ts';
import {VApp} from '/node_modules/.vite/deps/vuetify_components.js';
import DmViewView from '/src/views/workspace/DmViewView.vue';
import vuetify from '/src/plugins/vuetify.ts';
import i18n from '/src/i18n/index.ts';
import '/src/styles/common.scss';
import '/src/style.css';
// 外面这一层和话题页的 .project-shell 一样：overflow: hidden，页面在里面滚。
createApp({render:()=>h(VApp,{class:'h-100'},()=>h('div',{
  id:'frame', class:'project-shell h-100',
  style:'overflow:hidden;width:900px;height:700px;display:flex',
},[
  h(DmViewView,{
    error:null, ready:true, desktop:true, docked:false,
    panelOpen:false, panelFloat:true, focusMode:false, chatNews:false,
    focusLabel:'专注', focusIcon:'mdi-arrow-expand',
    chatStyle:{flex:'0 0 50%'},
  },{chat:()=>h('div',{style:'flex:1 1 auto;min-width:0'},'对话'),panel:()=>h('div',{style:'height:400px;overflow:auto'},'面板')}),
]))}).use(vuetify).use(i18n).use(pinia).mount('#fixture');
</script></body></html>`

/** 这一排外面最近的那个裁剪容器能不能横向滚：能滚就返回它多出来的宽度。 */
function scrolledBy(page: Page) {
  return page.evaluate(() => {
    const panes = document.querySelector('.panes') as HTMLElement | null
    if (!panes) throw new Error('没有找到那一排')
    let el: HTMLElement | null = panes.parentElement
    while (el) {
      const overflow = getComputedStyle(el).overflowX
      if (overflow !== 'visible') {
        el.scrollLeft = 200
        const moved = el.scrollLeft
        el.scrollLeft = 0
        return moved
      }
      el = el.parentElement
    }
    throw new Error('外面没有裁剪容器')
  })
}

test('收起的工作面板不会把话题页横向滚出去', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 })
  await page.addInitScript(() => localStorage.clear())
  await page.route('**/__panes__', (route) => route.fulfill({ contentType: 'text/html', body: fixture }))
  await page.goto('/__panes__', { waitUntil: 'commit' })
  await page.waitForSelector('.panes', { timeout: 60_000 })
  // 收起时它是 hidden 的（`visibility: hidden`），等到它挂上就行。
  await page.waitForSelector('.panel-host--sheet', { state: 'attached', timeout: 60_000 })

  // 收起时那张浮层被推到右边外面，但外面那一层不该因此长出一段横向可滚的区域。
  expect(await scrolledBy(page), '收起的面板浮层把话题页撑出了一段横向可滚区域').toBe(0)

  // 面板里任何一次 scrollIntoView 也不该把话题页带偏——用户看到的那一下就是它。
  const after = await page.evaluate(() => {
    const sheet = document.querySelector('.panel-host--sheet') as HTMLElement
    sheet.querySelector('*')?.scrollIntoView({ block: 'start' })
    let el: HTMLElement | null = sheet.parentElement
    while (el) {
      if (getComputedStyle(el).overflowX !== 'visible') return el.scrollLeft
      el = el.parentElement
    }
    return 0
  })
  expect(after, '收起的面板里一次 scrollIntoView 就把话题页横向带偏了').toBe(0)
})
