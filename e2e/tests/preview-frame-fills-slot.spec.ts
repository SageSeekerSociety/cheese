import { expect, test, type Page } from '@playwright/test'

// The web/app preview in the right panel shows a page at the full height of the space it
// has, in every viewport tier (电脑 / 平板 / 手机), at any panel width. The 240px floor
// (`--preview-min`) only applies when the column itself is shorter than that.
//
// Only a real layout shows this: the frames used to stop at 240px in every tier with the
// rest of the column blank under a long page. Mounts the real PanelPreviewHost with the
// product styles, in a row laid out the way WorkPanel's tab body lays out its panes; only
// the API and the previewed page are substituted, so nothing reaches a backend.
const PAGE = `<!doctype html><meta charset="utf-8"><body style="margin:0">${'<p style="height:80px;margin:0">段落</p>'.repeat(40)}</body>`

const fixture = `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><body style="margin:0">
<div id="fixture"></div><script type="module">
import {createApp,h,ref} from '/node_modules/.vite/deps/vue.js';
import PanelPreviewHost from '/src/components/work/PanelPreviewHost.vue';
import vuetify from '/src/plugins/vuetify.ts';
import i18n,{setLocale} from '/src/i18n/index.ts';
import '/src/style.css';
setLocale('zh-CN');
const width=ref(470), height=ref(820);
window.fixture={size:(w,hgt)=>{width.value=w;height.value=hgt}};
const page=${JSON.stringify(PAGE)};
const json=data=>new Response(JSON.stringify({code:200,message:'ok',data}),{headers:{'Content-Type':'application/json'}});
window.fetch=async url=>{
  const u=String(url);
  if(u.includes('/preview-session')) return json({url:'http://preview-frame.test/session',grant:'fixture-grant'});
  if(u.includes('/preview/file')) return json({path:'site/index.html',content:page,version:'v1',bytes:page.length,binary:false,too_large:false});
  return json(null);
};
createApp({setup(){return()=>h('div',{id:'stage',style:{display:'flex',width:width.value+'px',height:height.value+'px'}},[
  h(PanelPreviewHost,{style:'flex:1 1 auto;min-width:0',topicId:'fixture-topic',projectId:'p1',active:true,refreshTick:0,path:'site/index.html',agentName:'芝士'})])}})
  .use(vuetify).use(i18n).mount('#fixture');
</script></body></html>`

/** The slot's height, and the visible (after scaling) height of the page frame in it. */
function heights(page: Page) {
  return page.evaluate(() => {
    const slot = document.querySelector('.preview-slot')!.getBoundingClientRect()
    const frame = document.querySelector('.preview-frames iframe')!.getBoundingClientRect()
    return { slot: Math.round(slot.height), frame: Math.round(frame.height) }
  })
}

function size(page: Page, width: number, height: number) {
  return page.evaluate(
    ([w, h]) => (window as unknown as { fixture: { size: (w: number, h: number) => void } }).fixture.size(w, h),
    [width, height]
  )
}

test('the preview frame fills the panel height in every tier, down to a 240px floor', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.route('**/__preview-frame-fixture__', (route) => route.fulfill({ contentType: 'text/html', body: fixture }))
  // The previewed page lives on its own origin, as content-domain pages do.
  await page.route('http://preview-frame.test/**', (route) => route.fulfill({ contentType: 'text/html', body: PAGE }))
  await page.goto('/__preview-frame-fixture__', { waitUntil: 'commit' })
  await expect(page.locator('.preview-frames iframe')).toBeAttached({ timeout: 60_000 })

  // 470 is the usual right panel (平板 scales down to fit, 手机 fits at 1:1); at 900 both fit.
  for (const width of [470, 900]) {
    await size(page, width, 820)
    for (const tier of ['desktop', 'tablet', 'mobile']) {
      await page.getByTestId(`viewport-${tier}`).click()
      await expect(async () => {
        const { slot, frame } = await heights(page)
        expect(slot, `the slot at ${width}px is most of the 820px column`).toBeGreaterThan(700)
        expect(frame, `${tier} at ${width}px fills the slot`).toBe(slot)
      }).toPass({ timeout: 10_000 })
    }
  }

  // A column too short for the floor: the frame keeps 240px and the panel scrolls.
  await size(page, 470, 200)
  for (const tier of ['desktop', 'tablet', 'mobile']) {
    await page.getByTestId(`viewport-${tier}`).click()
    await expect.poll(() => heights(page), { message: `${tier} in a short column` }).toEqual({ slot: 240, frame: 240 })
  }
})
