import { expect, test, type Page } from '@playwright/test'

// 设置浮层（`SettingsOverlay`）的关闭箭头：macOS 桌面 app 把红黄绿三个窗口按钮画在
// 页面左上角之上（`window.__CHEESE_APP__.titleBar === 'overlay'`，见
// `frontend/src/lib/desktopApp.ts`）。箭头原来的左缘是灰栏的内容内距 24px，在那个壳里
// 正压在红色那颗窗口按钮上，看着像第四个窗口按钮。和 app 壳顶栏那颗返回同一条规则：
// 壳顶栏让出 72px（`AppBar.vue` 的 `.app-system-bar--window-buttons`），这一层也让开，
// 左缘从 24 挪到 72。
//
// 这件事只有真版面看得出来（happy-dom 没有布局），所以挂真的 SettingsOverlay、跑真
// 样式。普通浏览器（没有那个标记）位置必须照旧是 24 —— 让开的只是 macOS 那个壳。
const groups = [
  {
    key: 'account',
    title: '账号',
    items: [
      { key: 'profile', label: '个人资料', to: '/profile' },
      { key: 'security', label: '密码与安全', to: '/security' },
    ],
  },
]

/** 挂真组件的夹具。`overlay` 决定这一页跑不跑在 macOS 壳里（app 在页面脚本之前设好标记，
 *  组件 setup 时读一次）。 */
function fixture(overlay: boolean): string {
  return `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><body>
<div id="fixture"></div><script type="module">
import {createApp,h,ref} from '/node_modules/.vite/deps/vue.js';
import {VApp} from '/node_modules/.vite/deps/vuetify_components.js';
import SettingsOverlay from '/src/components/common/SettingsOverlay.vue';
import vuetify from '/src/plugins/vuetify.ts';
import i18n,{setLocale} from '/src/i18n/index.ts';
import '/src/style.css';
import {markTitleBarOverlay} from '/src/lib/desktopApp.ts';
setLocale('zh-CN');
${overlay ? "window.__CHEESE_APP__={titleBar:'overlay'};" : ''}
markTitleBarOverlay();
window.fixture={closed:0};
// 一屏装不下的内容：主滚动区真的滚得动，「滚了按钮还钉着」这条才有意义。
const body='<p style=\"height:2400px\">这一页的内容</p>';
createApp({setup(){
  const closed=ref(false);
  return()=>h(VApp,{},{default:()=>h('div',[h(SettingsOverlay,{
    label:'个人设置',groups:${JSON.stringify(groups)},active:'security',indexTo:'/',
    closeLabel:'关闭设置',closeTitle:'关闭设置（Esc）',backLabel:'返回设置',
    onClose:()=>{window.fixture.closed+=1;closed.value=true;}
  },{default:()=>h('div',{innerHTML:body})})])})
}}).use(vuetify).use(i18n).mount('#fixture');
</script></body></html>`
}

async function open(page: Page, overlay: boolean) {
  await page.route('**/__settings-overlay__', (route) =>
    route.fulfill({ contentType: 'text/html', body: fixture(overlay) })
  )
  await page.goto('/__settings-overlay__', { waitUntil: 'commit' })
  await expect(page.locator('.so__close')).toBeVisible({ timeout: 60_000 })
  await expect(page.locator('.so__side')).toBeVisible()
}

/**
 * 关闭按钮的左缘，以及它底下几像素处是否有别的东西盖着（窗口按钮在真壳里就画在那里，
 * 夹具里没有真窗口按钮，所以只量盒子的左缘、不量命中）。
 */
function closeLeft(page: Page) {
  return page.locator('.so__close').evaluate((el) => Math.round(el.getBoundingClientRect().left))
}

/** 截图前画一条只属于证据的竖线：真壳里窗口按钮占的就是 x=0..72 这条带子。这条线
 *  不进产品样式（`pointer-events:none`，绝对定位，不改任何被量的盒子）。 */
async function markReservedZone(page: Page) {
  await page.evaluate(() => {
    const line = document.createElement('div')
    line.id = 'window-buttons-zone'
    line.style.cssText =
      'position:fixed;left:0;top:0;width:72px;height:100%;pointer-events:none;z-index:9999;' +
      'border-right:2px dashed #e53935;background:rgba(229,57,53,.06)'
    line.title = 'macOS window buttons reserve x<72'
    document.body.appendChild(line)
  })
}

test('macOS 壳里关闭箭头让开窗口按钮那条保留区', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await open(page, true)
  await markReservedZone(page)
  await page.screenshot({ path: 'test-results/settings-overlay-macos-arrow.png' })
  // 72 就是 app 壳顶栏让开的宽度（`AppBar.vue` `.app-system-bar--window-buttons` 的
  // padding-inline-start）。箭头整颗落在保留区之外：左缘 >= 72。
  expect(await closeLeft(page), 'macOS 壳里箭头伸进了窗口按钮那条保留区').toBeGreaterThanOrEqual(72)
})

test('普通浏览器里关闭箭头位置不变：还是灰栏的内距 24', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await open(page, false)
  // 没有那个标记就不该让开：位置和改动前一模一样。
  expect(await closeLeft(page), '普通浏览器里箭头的位置被这次改动挪了').toBeLessThanOrEqual(25)
})

test('关闭箭头点得动，也钉在视口里不随内容滚走', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 480 })
  await open(page, true)

  // 真的滚一屏：不滚的话「滚了还在」等于没做。
  const scrolled = await page.locator('.so__main').evaluate((el) => {
    el.scrollTop = el.clientHeight
    return { top: el.scrollTop, canScroll: el.scrollHeight > el.clientHeight + 1 }
  })
  expect(scrolled.canScroll, '这一页不滚，这一条等于没做').toBeTruthy()
  expect(scrolled.top, '没滚下去').toBeGreaterThan(0)

  const box = await page.locator('.so__close').boundingBox()
  expect(box, '滚动后量不到关闭按钮').not.toBeNull()
  expect(box!.x, '滚动后箭头伸进了窗口按钮那条保留区').toBeGreaterThanOrEqual(72)
  expect(box!.y, '滚动后箭头滚出了视口').toBeGreaterThanOrEqual(0)
  expect(box!.y + box!.height, '滚动后箭头滚出了视口').toBeLessThanOrEqual(480)

  // 点它：这一层收到关闭这件事（夹具把 closed 记在一处可读的地方）。
  await page.getByRole('button', { name: '关闭设置' }).click()
  expect(await page.evaluate(() => (window as unknown as { fixture: { closed: number } }).fixture.closed)).toBe(1)
})
