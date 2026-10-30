import { createHash, randomUUID } from 'node:crypto'
import { mkdir, writeFile } from 'node:fs/promises'
import { deflateSync } from 'node:zlib'
import { expect, test, type Locator, type Page } from '@playwright/test'
import { apiLogin, openFirstProject } from './helpers'

test.use({ deviceScaleFactor: 2 })

// Real PNG decoding + PanelPreview's original-byte identity owner. Only the API
// is substituted; pointer selection, layout, observers, focus and send are real.
//
// 下面第一屏挂的是「预览」那一格的外壳（`components/work/PanelPreviewHost.vue`），
// 不是面板本身：面板现在只吃 props，取数在 `composables/usePanelPreview.ts` 里做一次
// 再整包递下去，这里按住的假接口喂的正是那一层。只挂面板的话它拿不到那一包，什么都画不出来。
function png() {
  const crc = (bytes: Buffer) => {
    let value = 0xffffffff
    for (const byte of bytes) {
      value ^= byte
      for (let bit = 0; bit < 8; bit++) value = (value >>> 1) ^ (value & 1 ? 0xedb88320 : 0)
    }
    return (value ^ 0xffffffff) >>> 0
  }
  const chunk = (name: string, bytes: Buffer) => {
    const type = Buffer.from(name)
    const length = Buffer.alloc(4)
    length.writeUInt32BE(bytes.length)
    const checksum = Buffer.alloc(4)
    checksum.writeUInt32BE(crc(Buffer.concat([type, bytes])))
    return Buffer.concat([length, type, bytes, checksum])
  }
  const header = Buffer.alloc(13)
  header.writeUInt32BE(1200)
  header.writeUInt32BE(800, 4)
  header[8] = 8
  header[9] = 2
  const pixels = Buffer.alloc((1200 * 3 + 1) * 800)
  for (let y = 0; y < 800; y++) {
    for (let x = 0; x < 1200; x++) {
      const at = y * 3601 + 1 + x * 3
      pixels[at] = x < 400 ? 228 : 245
      pixels[at + 1] = y < 400 ? 223 : 239
      pixels[at + 2] = 215
    }
  }
  return Buffer.concat([Buffer.from('89504e470d0a1a0a', 'hex'), chunk('IHDR', header), chunk('IDAT', deflateSync(pixels)), chunk('IEND', Buffer.alloc(0))])
}
const bytes = png()
const version = createHash('sha256').update(bytes).digest('hex').slice(0, 16)
const fixture = `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><body>
<div id="fixture"></div><script type="module">
import {createApp,h,ref} from '/node_modules/.vite/deps/vue.js';
import {VApp} from '/node_modules/.vite/deps/vuetify_components.js';
import PanelPreviewHost from '/src/components/work/PanelPreviewHost.vue';
import vuetify from '/src/plugins/vuetify.ts';
import i18n,{setLocale} from '/src/i18n/index.ts';
import '/src/style.css';
setLocale('zh-CN');
const refreshTick=ref(0),width=ref(860),height=ref(660),mounted=ref(true);
window.notes=[]; window.metadata={path:'design.png',content:null,version:'${version}',bytes:${bytes.length},binary:true,too_large:false,source:'committed'};
window.fixture={resize:value=>width.value=value,height:value=>height.value=value,refresh:()=>refreshTick.value++,unmount:()=>mounted.value=false};
window.fetch=async url=>{
  if(String(url).includes('/attachments/')) {
    if(window.blockBytes) return new Promise(()=>{});
    return new Response(Uint8Array.from(atob('${bytes.toString('base64')}'),c=>c.charCodeAt(0)),{headers:{'Content-Type':'image/png'}});
  }
  return new Response(JSON.stringify({code:200,data:window.metadata}),{headers:{'Content-Type':'application/json'}});
};
createApp({setup(){return()=>h(VApp,{}, {default:()=>h('main',{style:'padding:24px;display:flex;gap:24px'},[
 h('aside',{id:'stage',style:{width:width.value+'px',height:height.value+'px',display:'flex',flexShrink:0}},[
 mounted.value?h(PanelPreviewHost,{topicId:'fixture-room',projectId:'fixture-project',active:true,path:'design.png',refreshTick:refreshTick.value,onLocate:note=>window.notes.push(note)}):null]),
 h('input',{id:'outside', 'aria-label':'聊天输入'})])})}}).use(vuetify).use(i18n).mount('#fixture');
</script></body></html>`

test.beforeAll(async () => {
  const scratch = new URL('../../frontend/.tmp/design-region-note/', import.meta.url)
  await mkdir(scratch, { recursive: true })
  await writeFile(new URL('index.html', scratch), fixture)
})

async function open(page: Page) {
  await page.setViewportSize({ width: 1280, height: 760 })
  await page.route('**/__design-region-note__', route => route.fulfill({ contentType: 'text/html', body: fixture }))
  await page.goto('/__design-region-note__', { waitUntil: 'commit' })
  await expect(page.getByRole('button', { name: '选择图片区域', exact: true })).toBeEnabled({ timeout: 60_000 })
}
async function select(page: Page, handFocusAway = false) {
  await page.getByRole('button', { name: '选择图片区域', exact: true }).click()
  if (handFocusAway) await page.evaluate(() => {
    document.addEventListener('pointerup', () => (document.querySelector('#outside') as HTMLInputElement).focus(), { once: true, capture: true })
  })
  const image = await page.locator('.design-image img').boundingBox()
  if (!image) throw new Error('No real image layout')
  await page.mouse.move(image.x + 80, image.y + 70)
  await page.mouse.down()
  await page.mouse.move(image.x + 180, image.y + 140, { steps: 8 })
  await page.mouse.up()
  await expect(page.getByPlaceholder('输入修改要求或批注内容…')).toBeVisible()
}

// Exercise both unchanged standalone callers, with decoded PNG bytes and a
// physical pointer gesture. Controlled null remains the main panel's boundary.
function standaloneFixture(kind: 'file' | 'version' | 'controlled') {
  return `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><body><div id="fixture"></div><script type="module">
import {createApp,h,ref} from '/node_modules/.vite/deps/vue.js';
import FileBytesPreview from '/src/components/common/FileBytesPreview.vue';
import ArtifactVersionPreview from '/src/components/ArtifactVersionPreview.vue';
import DesignImage from '/src/components/panels/preview/DesignImage.vue';
import i18n,{setLocale} from '/src/i18n/index.ts';
import '/src/style.css';
setLocale('zh-CN');
const source=ref('snapshot-a'),active=ref(null),enabled=ref(true);
const bytes=Uint8Array.from(atob('${bytes.toString('base64')}'),c=>c.charCodeAt(0));
const src=URL.createObjectURL(new Blob([bytes],{type:'image/png'}));
window.fetch=async()=>new Response(bytes,{headers:{'Content-Type':'image/png'}});
window.selections=[];window.fixture={replace:()=>source.value='snapshot-b',accept:()=>active.value=window.selections.at(-1).region,clear:()=>active.value=null,disable:()=>enabled.value=false};
createApp({setup(){return()=>h('main',{style:'padding:24px;width:860px;height:660px;display:flex'},[
 '${kind}'==='file'?h(FileBytesPreview,{filename:'design.png',source:source.value,read:async()=>bytes.buffer}):
 '${kind}'==='version'?h(ArtifactVersionPreview,{projectId:'project',artifactId:'artifact',bare:true,version:{kind:'file',filename:'design.png',number:1,card_id:source.value}}):
 h(DesignImage,{src,alt:'design.png',identity:source.value,selectionEnabled:enabled.value,activeRegion:active.value,onRegion:selection=>window.selections.push(selection)})
])}}).use(i18n).mount('#fixture');
</script></body></html>`
}
async function dragImage(page: Page, small = false) {
  await page.getByRole('button', { name: '选择图片区域', exact: true }).click()
  const image = await page.locator('.design-image img').boundingBox()
  if (!image) throw new Error('No decoded image layout')
  await page.mouse.move(image.x + (small ? 20 : 80), image.y + (small ? 8 : 70))
  await page.mouse.down()
  await page.mouse.move(image.x + (small ? 60 : 180), image.y + (small ? 24 : 140), { steps: 8 })
  await page.mouse.up()
}

// 图上那几颗评论点要真正接得住指针。区域选择层是 `inset: 0` 的一整张，和点同为
// `position: absolute` 的兄弟；点排在它前面时，后者既画在上面也先接到这一下，真鼠标
// 点上去就成了「开始框选」。单测把 `click` 直接打在 `<button>` 上，感受不到这一层，只有
// 真指针能钉住。`marks` 按原图像素给，这里落在图正中。
function marksFixture() {
  return `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><body><div id="fixture"></div><script type="module">
import {createApp,h,ref} from '/node_modules/.vite/deps/vue.js';
import DesignImage from '/src/components/panels/preview/DesignImage.vue';
import i18n,{setLocale} from '/src/i18n/index.ts';
import '/src/style.css';
setLocale('zh-CN');
const bytes=Uint8Array.from(atob('${bytes.toString('base64')}'),c=>c.charCodeAt(0));
const src=URL.createObjectURL(new Blob([bytes],{type:'image/png'}));
window.marks=[];window.selections=[];
const marks=[{id:'t1',region:{x:600,y:400,width:1,height:1}}];
createApp({setup(){return()=>h('main',{style:'padding:24px;width:860px;height:660px;display:flex'},[
 h(DesignImage,{src,alt:'design.png',identity:'marks-probe',selectionEnabled:true,marks,activeMark:null,
   onMark:id=>window.marks.push(id),onRegion:selection=>window.selections.push(selection)})])}}).use(i18n).mount('#fixture');
</script></body></html>`
}

// The status line that reads out the selected region in the image's own pixels.
const regionLine = (page: Page) => page.getByRole('status').filter({ hasText: '原图像素' })

for (const kind of ['file', 'version'] as const) {
  test(`${kind} standalone caller retains its natural-pixel selection and retires it with the resource`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 1280, height: 760 })
    await page.route('**/__standalone-region__', route => route.fulfill({ contentType: 'text/html', body: standaloneFixture(kind) }))
    await page.goto('/__standalone-region__', { waitUntil: 'commit' })
    await expect(page.getByRole('button', { name: '选择图片区域', exact: true })).toBeEnabled({ timeout: 60_000 })
    await dragImage(page)
    await expect(page.locator('.design-image__selection')).toHaveCount(1)
    const region = regionLine(page)
    const original = await region.textContent()
    expect(original).toMatch(/原图像素：x=\d+，y=\d+，宽=\d+，高=\d+/)
    await page.getByRole('button', { name: '放大内容', exact: true }).click()
    await expect(page.locator('.design-image__selection')).toHaveCount(1)
    expect(await region.textContent()).toBe(original)
    await testInfo.attach(`${kind}-retained`, { body: await page.screenshot(), contentType: 'image/png' })
    await page.evaluate(() => (window as unknown as { fixture: { replace: () => void } }).fixture.replace())
    await expect(page.locator('.design-image__selection')).toHaveCount(0)
    await expect(region).toHaveCount(0)
    await expect(page.getByRole('button', { name: '选择图片区域', exact: true })).toBeEnabled()
    await dragImage(page)
    await expect(page.locator('.design-image__selection')).toHaveCount(1)
  })
}
test('explicit controlled null never displays an unaccepted region and clears accepted selection', async ({ page }) => {
  await page.route('**/__controlled-region__', route => route.fulfill({ contentType: 'text/html', body: standaloneFixture('controlled') }))
  await page.goto('/__controlled-region__', { waitUntil: 'commit' })
  await expect(page.getByRole('button', { name: '选择图片区域', exact: true })).toBeEnabled({ timeout: 60_000 })
  await dragImage(page)
  expect(await page.evaluate(() => (window as unknown as { selections: unknown[] }).selections.length)).toBe(1)
  await expect(page.locator('.design-image__selection')).toHaveCount(0)
  await expect(regionLine(page)).toHaveCount(0)
  await page.evaluate(() => (window as unknown as { fixture: { accept: () => void } }).fixture.accept())
  await expect(page.locator('.design-image__selection')).toHaveCount(1)
  await page.evaluate(() => (window as unknown as { fixture: { clear: () => void } }).fixture.clear())
  await expect(page.locator('.design-image__selection')).toHaveCount(0)
  await dragImage(page)
  await expect(page.locator('.design-image__selection')).toHaveCount(0)
})
test('a short-panel note keeps the opener focused and returns focus after editing and Escape', async ({ page }, testInfo) => {
  await open(page)
  await page.evaluate(() => {
    const fixture = (window as unknown as { fixture: { resize: (width: number) => void; height: (height: number) => void } }).fixture
    fixture.resize(240)
    fixture.height(150)
  })
  await dragImage(page, true)
  const input = page.getByRole('textbox', { name: '输入修改要求或批注内容…', exact: true })
  await expect(input).toBeVisible()
  await expect(page.getByRole('button', { name: '选择图片区域', exact: true })).toBeFocused()
  await reachable(input)
  await input.fill('取消这条草稿')
  await testInfo.attach('short-panel-editing', { body: await page.screenshot(), contentType: 'image/png' })
  await input.press('Escape')
  await expect(input).toHaveCount(0)
  await expect(page.locator('.design-image__selection')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '选择图片区域', exact: true })).toBeFocused()
  expect(await page.evaluate(() => (window as unknown as { notes: { message: string }[] }).notes)).toHaveLength(0)
})
test('a short wide real panel keeps its note placement stable and its editor reachable', async ({ page }, testInfo) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await open(page)
  await page.evaluate(() => (window as unknown as { fixture: { height: (height: number) => void } }).fixture.height(150))
  await dragImage(page, true)
  const input = page.getByRole('textbox', { name: '输入修改要求或批注内容…', exact: true })
  await expect(input).toBeVisible()
  const samples = await page.evaluate(async () => {
    const samples = []
    for (let frame = 0; frame < 84; frame++) {
      await new Promise<void>(resolve => requestAnimationFrame(() => resolve()))
      const note = document.querySelector<HTMLElement>('[data-locator-ui]')!
      const box = note.getBoundingClientRect()
      const panel = document.querySelector('.panel-preview')!.getBoundingClientRect()
      if (frame >= 24) samples.push({
        height: box.height, top: box.top, left: box.left, right: box.right, panelLeft: panel.left, panelRight: panel.right,
        focus: document.activeElement?.className || document.activeElement?.tagName,
      })
    }
    return samples
  })
  await testInfo.attach('steady-layout-samples', { body: JSON.stringify({ samples, errors }, null, 2), contentType: 'application/json' })
  await testInfo.attach('short-wide-panel', { body: await page.screenshot(), contentType: 'image/png' })
  for (const dimension of ['top', 'left', 'height'] as const) {
    const values = samples.map(sample => sample[dimension])
    expect(Math.max(...values) - Math.min(...values)).toBeLessThan(0.5)
  }
  expect(samples.every(sample => sample.left >= sample.panelLeft && sample.right <= sample.panelRight)).toBe(true)
  await expect(page.getByRole('button', { name: '选择图片区域', exact: true })).toBeFocused()
  await reachable(input)
  await input.fill('短面板也能发送')
  await page.getByRole('button', { name: '发送', exact: true }).click()
  expect(await page.evaluate(() => (window as unknown as { notes: { message: string }[] }).notes.map(note => note.message))).toEqual([expect.stringContaining('短面板也能发送')])
  expect(errors).toEqual([])
})
async function insidePane(page: Page) {
  await expect.poll(async () => {
    const { card, pane } = await geometry(page)
    return card.left >= pane.left + 11 && card.top >= pane.top + 11 && card.right <= pane.right - 11 && card.bottom <= pane.bottom - 11
  }).toBe(true)
}
async function geometry(page: Page) {
  return page.evaluate(() => {
    const rect = (element: Element) => {
      const box = element.getBoundingClientRect()
      return { left: box.left, top: box.top, right: box.right, bottom: box.bottom, width: box.width, height: box.height }
    }
    const input = document.querySelector('textarea[placeholder="输入修改要求或批注内容…"]')!
    return { card: rect(input.closest('[data-locator-ui]')!), region: rect(document.querySelector('.design-image__selection')!), pane: rect(document.querySelector('.panel-preview')!) }
  })
}
async function reachable(control: Locator) {
  await control.scrollIntoViewIfNeeded()
  await expect.poll(async () => control.evaluate(element => {
    const box = element.getBoundingClientRect()
    const panel = element.closest('.panel-preview')!.getBoundingClientRect()
    const x = box.left + box.width / 2
    const y = box.top + box.height / 2
    const hit = document.elementFromPoint(x, y)
    return x >= panel.left && x <= panel.right && y >= panel.top && y <= panel.bottom && (hit === element || !!(hit && element.contains(hit)))
  })).toBe(true)
}
test('accepted image region keeps its note beside the visible region in the real panel', async ({ page }, testInfo) => {
  await open(page)
  await select(page)
  const input = page.getByPlaceholder('输入修改要求或批注内容…')
  await input.fill('把这块留白收紧')
  await expect.poll(async () => {
    const { card, region } = await geometry(page)
    return card.top - region.bottom
  }).toBeGreaterThanOrEqual(7)
  await expect.poll(async () => {
    const { card, region } = await geometry(page)
    return card.top - region.bottom
  }).toBeLessThanOrEqual(9)
  const measured = await geometry(page)
  await testInfo.attach('actual-layout', { body: JSON.stringify(measured, null, 2), contentType: 'application/json' })
  await testInfo.attach('wide-panel', { body: await page.screenshot(), contentType: 'image/png' })
  expect(measured.card.top - measured.region.bottom).toBeGreaterThanOrEqual(7)
  expect(measured.card.top - measured.region.bottom).toBeLessThanOrEqual(9)
  await insidePane(page)
  await input.press('Enter')
  await expect(input).toHaveCount(0)
  const located = await page.evaluate(() =>
    (window as unknown as { notes: { message: string }[] }).notes.map(note => note.message)
  )
  expect(located).toEqual([expect.stringContaining(`version=${version}`)])
  await expect(page.locator('.design-image__selection')).toHaveCount(0)
})

test('zoom, scroll and local resize retain the same input, draft and original region', async ({ page }, testInfo) => {
  await open(page)
  await select(page)
  const input = page.getByPlaceholder('输入修改要求或批注内容…')
  await input.fill('保留中文草稿')
  await input.evaluate(element => {
    const field = element as HTMLTextAreaElement
    field.setSelectionRange(2, 4)
    Object.assign(window, { originalInput: field, focusedAgain: 0 })
    field.addEventListener('focus', () => { (window as unknown as { focusedAgain: number }).focusedAgain++ })
  })
  const regionText = await regionLine(page).textContent()
  // Physical toolbar activation may move focus; subsequent layout must not take it back.
  await page.getByRole('button', { name: '放大内容', exact: true }).click()
  await page.getByRole('button', { name: '放大内容', exact: true }).click()
  await page.getByRole('button', { name: '放大内容', exact: true }).click()
  await insidePane(page)
  await page.locator('.design-image__pane').evaluate(element => { element.scrollTop = element.scrollHeight; element.scrollLeft = element.scrollWidth })
  await expect.poll(() => regionIntersectsView(page)).toBe(false)
  await insidePane(page)
  expect(await input.inputValue()).toBe('保留中文草稿')
  expect(await regionLine(page).textContent()).toBe(regionText)
  await page.locator('.design-image__pane').evaluate(element => { element.scrollTop = 0; element.scrollLeft = 0 })
  await expect.poll(() => regionIntersectsView(page)).toBe(true)
  for (const width of [240, 320, 860]) {
    await page.evaluate(value => (window as unknown as { fixture: { resize: (width: number) => void } }).fixture.resize(value), width)
    await insidePane(page)
    await expect(input).toHaveValue('保留中文草稿')
    await testInfo.attach(`width-${width}`, { body: await page.screenshot(), contentType: 'image/png' })
    expect(await input.evaluate(element => element === (window as unknown as { originalInput: Element }).originalInput)).toBe(true)
    expect(await input.evaluate(element => [(element as HTMLTextAreaElement).selectionStart, (element as HTMLTextAreaElement).selectionEnd])).toEqual([2, 4])
  }
  expect(await page.evaluate(() => (window as unknown as { focusedAgain: number }).focusedAgain)).toBe(0)
  await testInfo.attach('resized-layout', { body: JSON.stringify(await geometry(page)), contentType: 'application/json' })
  await input.press('Enter')
  const notes = await page.evaluate(() =>
    (window as unknown as { notes: { message: string }[] }).notes.map(note => note.message)
  )
  expect(notes).toHaveLength(1)
  expect(notes[0]).toContain('x=')
  expect(notes[0]).toContain('保留中文草稿')
})
async function regionIntersectsView(page: Page) {
  return page.evaluate(() => {
    const region = document.querySelector('.design-image__selection')!.getBoundingClientRect()
    const pane = document.querySelector('.design-image__pane')!.getBoundingClientRect()
    return region.right > pane.left && region.left < pane.right && region.bottom > pane.top && region.top < pane.bottom
  })
}

test('composition, explicit cancellation and external focus respect draft ownership', async ({ page }) => {
  await open(page)
  await select(page)
  const input = page.getByPlaceholder('输入修改要求或批注内容…')
  await expect(page.getByRole('button', { name: '选择图片区域', exact: true })).toBeFocused()
  await input.fill('正在输入中文')
  await input.evaluate(element => element.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', keyCode: 229, isComposing: true, bubbles: true })))
  expect(await page.evaluate(() => (window as unknown as { notes: { message: string }[] }).notes)).toHaveLength(0)
  await input.press('Escape')
  await expect(page.getByRole('button', { name: '选择图片区域', exact: true })).toBeFocused()
  await expect(page.locator('.design-image__selection')).toHaveCount(0)
  await select(page)
  await page.getByLabel('聊天输入', { exact: true }).focus()
  await page.getByRole('button', { name: '取消', exact: true }).evaluate(element => (element as HTMLButtonElement).click())
  await expect(page.getByLabel('聊天输入', { exact: true })).toBeFocused()
  expect(await page.evaluate(() => (window as unknown as { notes: { message: string }[] }).notes)).toHaveLength(0)
})

test('replacement metadata retires the draft while new bytes are pending, without restoring old focus', async ({ page }) => {
  await open(page)
  await select(page)
  await page.getByPlaceholder('输入修改要求或批注内容…').fill('旧图说明')
  await page.getByLabel('聊天输入', { exact: true }).focus()
  await page.evaluate(() => {
    const fixture = window as unknown as { metadata: { version: string }; blockBytes: boolean; fixture: { refresh: () => void } }
    fixture.metadata.version = 'bbbbbbbbbbbbbbbb'
    fixture.blockBytes = true
    fixture.fixture.refresh()
  })
  await expect(page.getByPlaceholder('输入修改要求或批注内容…')).toHaveCount(0)
  await expect(page.locator('.design-image__selection')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '选择图片区域', exact: true })).toBeDisabled()
  await expect(page.getByLabel('聊天输入', { exact: true })).toBeFocused()
  await page.evaluate(() => (window as unknown as { fixture: { unmount: () => void } }).fixture.unmount())
  await expect(page.getByLabel('聊天输入', { exact: true })).toBeFocused()
  expect(await page.evaluate(() => (window as unknown as { notes: { message: string }[] }).notes)).toHaveLength(0)
})

test('a compact image viewport keeps the note editor and close button physically reachable', async ({ page }, testInfo) => {
  await open(page)
  await select(page)
  await page.getByPlaceholder('输入修改要求或批注内容…').fill('短视口中的草稿')
  for (const height of [80, 48, 32]) {
    await page.locator('.design-image__viewport').evaluate((element, height) => {
      const viewport = element as HTMLElement
      viewport.style.flex = 'none'
      viewport.style.height = height + 'px'
    }, height)
    await reachable(page.getByRole('textbox', { name: '输入修改要求或批注内容…', exact: true }))
    await reachable(page.getByRole('button', { name: '取消', exact: true }))
    await testInfo.attach(`compact-${height}`, { body: await page.screenshot(), contentType: 'image/png' })
  }
  await page.getByRole('button', { name: '取消', exact: true }).click()
  await expect(page.getByPlaceholder('输入修改要求或批注内容…')).toHaveCount(0)
})

test('a pointerup focus handoff remains with the chat input through layout and resize', async ({ page }) => {
  await open(page)
  await select(page, true)
  await expect(page.getByLabel('聊天输入', { exact: true })).toBeFocused()
  await page.evaluate(() => (window as unknown as { fixture: { resize: (width: number) => void } }).fixture.resize(320))
  await insidePane(page)
  await expect(page.getByLabel('聊天输入', { exact: true })).toBeFocused()
})

test('a short real PanelPreview lets the reader reach every note control by scrolling', async ({ page }, testInfo) => {
  await open(page)
  await select(page)
  await page.getByPlaceholder('输入修改要求或批注内容…').fill('真实短面板草稿')
  await page.evaluate(() => (window as unknown as { fixture: { resize: (width: number) => void } }).fixture.resize(240))
  for (const height of [150, 220, 660]) {
    await page.evaluate(value => (window as unknown as { fixture: { height: (height: number) => void } }).fixture.height(value), height)
    await reachable(page.getByRole('textbox', { name: '输入修改要求或批注内容…', exact: true }))
    await reachable(page.getByRole('button', { name: '发送', exact: true }))
    await reachable(page.getByRole('button', { name: '取消', exact: true }))
    await testInfo.attach(`parent-${height}`, { body: await page.screenshot(), contentType: 'image/png' })
  }
  await expect(page.getByPlaceholder('输入修改要求或批注内容…')).toHaveValue('真实短面板草稿')
})

test('a short project window lets the reader edit, choose a recipient, cancel and send a region note', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 800 })
  await apiLogin(page)
  await page.route('**/preview/file?path=design.png', route => route.fulfill({ json: {
    code: 200, data: { path: 'design.png', content: null, version, bytes: bytes.length, binary: true, too_large: false, source: 'committed' },
  } }))
  await page.route('**/attachments/raw?path=design.png&**', route => route.fulfill({ contentType: 'image/png', body: bytes }))
  const topics = await openFirstProject(page)
  await topics.first().click()
  await page.waitForURL(/\/channels\//)
  const url = new URL(page.url())
  url.searchParams.set('tab', 'file:design.png')
  await page.goto(url.toString())
  await expect(page.getByRole('button', { name: '选择图片区域', exact: true })).toBeEnabled({ timeout: 30_000 })
  await page.setViewportSize({ width: 1440, height: 280 })
  await page.locator('.panel-preview').evaluate(element => element.scrollTop = element.scrollHeight)
  await dragImage(page, true)
  const card = page.locator('.locator')
  const input = card.getByRole('textbox')
  await reachable(input)
  await input.fill('短窗口取消检查')
  await reachable(card.getByRole('button', { name: '取消', exact: true }))
  await input.press('Escape')
  await expect(card).toHaveCount(0)
  await expect(page.getByRole('button', { name: '选择图片区域', exact: true })).toBeFocused()
  await expect(page.getByRole('feed')).not.toContainText('短窗口取消检查')

  await dragImage(page, true)
  const note = `短窗口批注发送 ${randomUUID()}`
  await reachable(input)
  await input.fill(note)
  const recipient = card.getByRole('button', { name: '批注投递目标', exact: true })
  for (const height of [240, 340, 280]) {
    await page.setViewportSize({ width: 1440, height })
    await reachable(input)
    await reachable(card.getByRole('button', { name: '发送', exact: true }))
    await reachable(card.getByRole('button', { name: '取消', exact: true }))
    await reachable(recipient)
    await expect(input).toHaveValue(note)
  }
  await reachable(recipient)
  await recipient.click()
  await page.getByRole('option', { name: /发给任务芝士/ }).click()
  const send = card.getByRole('button', { name: '发送', exact: true })
  await reachable(send)
  const boxes = await send.evaluate(element => ({
    send: element.getBoundingClientRect().toJSON(),
    card: element.closest('.locator')!.getBoundingClientRect().toJSON(),
    panel: element.closest('.panel-preview')!.getBoundingClientRect().toJSON(),
    viewportHeight: innerHeight,
  }))
  expect(boxes.send.top).toBeGreaterThanOrEqual(boxes.panel.top)
  expect(boxes.send.bottom).toBeLessThanOrEqual(Math.min(boxes.panel.bottom, boxes.viewportHeight))
  await testInfo.attach('short-project-window-geometry', { body: JSON.stringify(boxes), contentType: 'application/json' })
  await page.screenshot({ path: testInfo.outputPath('short-project-window.png') })
  await send.click()
  await expect(card).toHaveCount(0)
  await expect(page.getByRole('feed')).toContainText(note)
})

test('a real pointer on a comment mark opens the thread instead of starting a region', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 })
  await page.route('**/__marks-probe__', route => route.fulfill({ contentType: 'text/html', body: marksFixture() }))
  await page.goto('/__marks-probe__', { waitUntil: 'commit' })
  await expect(page.locator('.design-image img')).toBeVisible({ timeout: 60_000 })
  await page.waitForFunction(() => {
    const image = document.querySelector('.design-image img') as HTMLImageElement | null
    return !!image && image.complete && image.naturalWidth > 0
  })
  const dot = page.locator('.design-image__mark').first()
  await expect(dot).toHaveCount(1)
  const box = await dot.boundingBox()
  if (!box) throw new Error('No real layout for the mark dot')
  const hit = await page.evaluate(
    ({ x, y }) => document.elementFromPoint(x, y)?.className ?? '',
    { x: box.x + box.width / 2, y: box.y + box.height / 2 }
  )
  expect(hit).toContain('design-image__mark')
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2)
  await expect
    .poll(async () => page.evaluate(() => (window as unknown as { marks: string[] }).marks))
    .toEqual(['t1'])
  expect(await page.evaluate(() => (window as unknown as { selections: unknown[] }).selections.length)).toBe(0)
})
