import { expect, test, type Page } from '@playwright/test'

// A file chip in a channel opens the project's current version of that file in a
// read-only tab of the right panel. Its code view has to span the panel, at the
// width the panel opened with and after the divider makes the panel wider.
//
// Only a real layout shows this: the tab used to shrink to the width of its path
// bar, Monaco measured that and kept it, and the panel showed an empty strip to
// the right. Mounts the real WorkPanel and product styles; only the API is
// substituted, so nothing reaches a backend.
const LINE = 'task_submission_schema_seq = Sequence("task_submission_schema_seq")  # long enough to need the full width'
const fixture = `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><body>
<div id="fixture"></div><script type="module">
import {createApp,h,ref} from '/node_modules/.vite/deps/vue.js';
import {VApp} from '/node_modules/.vite/deps/vuetify_components.js';
import WorkPanel from '/src/components/WorkPanel.vue';
import vuetify from '/src/plugins/vuetify.ts';
import i18n,{setLocale} from '/src/i18n/index.ts';
import '/src/style.css';
setLocale('zh-CN');
const width=ref(560), panel=ref(null);
window.fixture={resize:value=>width.value=value, open:path=>panel.value.openFile(path)};
const json=(data,status=200)=>new Response(JSON.stringify(status===200?{code:200,data}:{code:status,message:'not found'}),{status,headers:{'Content-Type':'application/json'}});
window.fetch=async url=>{
  const u=String(url);
  // Not one of the channel's own files, so the chip resolves to the project's current version.
  if(u.includes('/preview/file')) return json(null,404);
  if(u.includes('/file?')) return json({path:'app/models.py',content:${JSON.stringify(LINE + '\n')}.repeat(40),version:'v1',bytes:4000,binary:false,too_large:false});
  if(u.includes('/preview')) return json(null);
  if(u.includes('summary')) return json({changed_files:[],has_run:false});
  return json({data:[],total:0});
};
const topic={id:'chan-1',project_id:'p1',parent_id:null,title:'频道',kind:'topic',status:'active',created_by:'me',created_at:'2026-10-01T09:00:00Z'};
createApp({setup(){return()=>h(VApp,{},{default:()=>h('main',{style:'padding:24px;display:flex'},[
  h('aside',{id:'stage',style:{width:width.value+'px',height:'600px',display:'flex',flexDirection:'column',flexShrink:0}},[
    h(WorkPanel,{ref:panel,topic,projectId:'p1',activityTick:0})])])})}}).use(vuetify).use(i18n).mount('#fixture');
</script></body></html>`

/** How far the code view's right edge stops short of the panel's, in px. */
function gap(page: Page) {
  return page.evaluate(() => {
    const panel = document.querySelector('[role="tabpanel"]')!.getBoundingClientRect()
    const code = document.querySelector('[data-testid="project-file"] .monaco-editor')!.getBoundingClientRect()
    return Math.round(panel.right - code.right)
  })
}

test('a project file tab fills the panel, also after the panel widens', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.route('**/__work-panel-file-tab__', (route) => route.fulfill({ contentType: 'text/html', body: fixture }))
  await page.goto('/__work-panel-file-tab__', { waitUntil: 'commit' })
  await expect(page.getByRole('tablist')).toBeVisible({ timeout: 60_000 })
  await page.evaluate(() => (window as unknown as { fixture: { open: (p: string) => Promise<void> } }).fixture.open('app/models.py'))
  await expect(page.locator('[data-testid="project-file"] .monaco-editor')).toBeVisible({ timeout: 60_000 })

  await expect.poll(() => gap(page)).toBe(0)

  // Dragging the divider leftwards is the panel getting wider.
  await page.evaluate(() => (window as unknown as { fixture: { resize: (w: number) => void } }).fixture.resize(900))
  await expect.poll(() => page.evaluate(() => document.querySelector('[role="tabpanel"]')!.getBoundingClientRect().width)).toBeGreaterThan(800)
  await expect.poll(() => gap(page)).toBe(0)
})
