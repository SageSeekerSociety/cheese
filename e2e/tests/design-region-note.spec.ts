import { createHash } from 'node:crypto'
import { mkdir, writeFile } from 'node:fs/promises'
import { deflateSync } from 'node:zlib'
import { expect, test, type Page } from '@playwright/test'

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
  await expect(page.getByPlaceholder('说明要改什么')).toBeVisible()
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
for (const kind of ['file', 'version'] as const) {
  test(`${kind} standalone caller retains its natural-pixel selection and retires it with the resource`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 1280, height: 760 })
    await page.route('**/__standalone-region__', route => route.fulfill({ contentType: 'text/html', body: standaloneFixture(kind) }))
    await page.goto('/__standalone-region__', { waitUntil: 'commit' })
    await expect(page.getByRole('button', { name: '选择图片区域', exact: true })).toBeEnabled({ timeout: 60_000 })
    await dragImage(page)
    await expect(page.locator('.design-image__selection')).toHaveCount(1)
    const region = page.locator('.design-image > output')
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
  await expect(page.locator('.design-image > output')).toHaveCount(0)
  await page.evaluate(() => (window as unknown as { fixture: { accept: () => void } }).fixture.accept())
  await expect(page.locator('.design-image__selection')).toHaveCount(1)
  await page.evaluate(() => (window as unknown as { fixture: { clear: () => void } }).fixture.clear())
  await expect(page.locator('.design-image__selection')).toHaveCount(0)
  await dragImage(page)
  await expect(page.locator('.design-image__selection')).toHaveCount(0)
})
test('the initially focused compact entry cancels once with Escape and hands focus back', async ({ page }, testInfo) => {
  await open(page)
  await page.evaluate(() => {
    const fixture = (window as unknown as { fixture: { resize: (width: number) => void; height: (height: number) => void } }).fixture
    fixture.resize(240)
    fixture.height(150)
  })
  await dragImage(page, true)
  const entry = page.getByRole('button', { name: '说明要改什么', exact: true })
  await expect(entry).toBeFocused()
  await testInfo.attach('compact-initial-focus', { body: await page.screenshot(), contentType: 'image/png' })
  await entry.press('Escape')
  await expect(page.locator('[data-region-note]')).toHaveCount(0)
  await expect(page.locator('.design-image__selection')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '选择图片区域', exact: true })).toBeFocused()
  expect(await page.evaluate(() => (window as unknown as { notes: { message: string }[] }).notes)).toHaveLength(0)
})
test('a short wide real panel settles into one compact placement without measurement feedback', async ({ page }, testInfo) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await open(page)
  await page.evaluate(() => (window as unknown as { fixture: { height: (height: number) => void } }).fixture.height(150))
  await dragImage(page, true)
  await expect(page.locator('[data-region-note]')).toHaveCount(1)
  const samples = await page.evaluate(async () => {
    const samples = []
    for (let frame = 0; frame < 84; frame++) {
      await new Promise<void>(resolve => requestAnimationFrame(() => resolve()))
      const note = document.querySelector<HTMLElement>('[data-region-note]')!
      const box = note.getBoundingClientRect()
      const panel = document.querySelector('.panel-preview')!.getBoundingClientRect()
      if (frame >= 24) samples.push({
        placement: note.dataset.placement, compact: !!note.querySelector('.design-region-note__expand'),
        height: box.height, top: box.top, bottom: box.bottom, panelBottom: panel.bottom,
        focus: document.activeElement?.className || document.activeElement?.tagName,
      })
    }
    return samples
  })
  await testInfo.attach('steady-layout-samples', { body: JSON.stringify({ samples, errors }, null, 2), contentType: 'application/json' })
  await testInfo.attach('short-wide-panel', { body: await page.screenshot(), contentType: 'image/png' })
  expect(new Set(samples.map(sample => `${sample.placement}:${sample.compact}:${sample.top}:${sample.height}`)).size).toBe(1)
  expect(samples.every(sample => sample.compact && sample.bottom <= sample.panelBottom)).toBe(true)
  await expect(page.getByRole('button', { name: '说明要改什么', exact: true })).toBeFocused()
  expect(errors).toEqual([])
})
async function insidePane(page: Page) {
  await expect.poll(async () => {
    const { card, pane } = await geometry(page)
    return card.left >= pane.left + 7 && card.top >= pane.top + 7 && card.right <= pane.right - 7 && card.bottom <= pane.bottom - 7
  }).toBe(true)
}
async function geometry(page: Page) {
  return page.evaluate(() => {
    const rect = (element: Element) => {
      const box = element.getBoundingClientRect()
      return { left: box.left, top: box.top, right: box.right, bottom: box.bottom, width: box.width, height: box.height }
    }
    const input = document.querySelector('input[placeholder="说明要改什么"]')!
    return { card: rect(input.closest('[data-region-note],.locator')!), region: rect(document.querySelector('.design-image__selection')!), pane: rect(document.querySelector('.design-image__pane')!) }
  })
}
test('accepted image region keeps its note beside the visible region in the real panel', async ({ page }, testInfo) => {
  await open(page)
  await select(page)
  const input = page.getByPlaceholder('说明要改什么')
  await input.fill('把这块留白收紧')
  const measured = await geometry(page)
  await testInfo.attach('actual-layout', { body: JSON.stringify(measured, null, 2), contentType: 'application/json' })
  await testInfo.attach('wide-panel', { body: await page.screenshot(), contentType: 'image/png' })
  expect(measured.card.left - measured.region.right).toBeGreaterThanOrEqual(7)
  expect(measured.card.left - measured.region.right).toBeLessThanOrEqual(9)
  expect(measured.card.right).toBeLessThanOrEqual(measured.pane.right - 7)
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
  const input = page.getByPlaceholder('说明要改什么')
  await input.fill('保留中文草稿')
  await input.evaluate(element => {
    const field = element as HTMLInputElement
    field.setSelectionRange(2, 4)
    Object.assign(window, { originalInput: field, focusedAgain: 0 })
    field.addEventListener('focus', () => { (window as unknown as { focusedAgain: number }).focusedAgain++ })
  })
  const regionText = await page.locator('.design-image > output').textContent()
  // Physical toolbar activation may move focus; subsequent layout must not take it back.
  await page.getByRole('button', { name: '放大内容', exact: true }).click()
  await page.getByRole('button', { name: '放大内容', exact: true }).click()
  await page.getByRole('button', { name: '放大内容', exact: true }).click()
  await insidePane(page)
  await page.locator('.design-image__pane').evaluate(element => { element.scrollTop = element.scrollHeight; element.scrollLeft = element.scrollWidth })
  await expect(page.getByRole('status').filter({ hasText: '所选区域在当前视图外' })).toBeVisible()
  await insidePane(page)
  expect(await input.inputValue()).toBe('保留中文草稿')
  expect(await page.locator('.design-image > output').textContent()).toBe(regionText)
  await page.locator('.design-image__pane').evaluate(element => { element.scrollTop = 0; element.scrollLeft = 0 })
  await expect(page.getByRole('status').filter({ hasText: '所选区域在当前视图外' })).toHaveCount(0)
  for (const width of [240, 320, 860]) {
    await page.evaluate(value => (window as unknown as { fixture: { resize: (width: number) => void } }).fixture.resize(value), width)
    await insidePane(page)
    await expect(input).toHaveValue('保留中文草稿')
    await testInfo.attach(`width-${width}`, { body: await page.screenshot(), contentType: 'image/png' })
    expect(await page.evaluate(() => document.querySelector('input[placeholder="说明要改什么"]') === (window as unknown as { originalInput: Element }).originalInput)).toBe(true)
    expect(await input.evaluate(element => [(element as HTMLInputElement).selectionStart, (element as HTMLInputElement).selectionEnd])).toEqual([2, 4])
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

test('composition, explicit cancellation and external focus respect draft ownership', async ({ page }) => {
  await open(page)
  await select(page)
  const input = page.getByPlaceholder('说明要改什么')
  await expect(input).toBeFocused()
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
  await page.getByPlaceholder('说明要改什么').fill('旧图说明')
  await page.getByLabel('聊天输入', { exact: true }).focus()
  await page.evaluate(() => {
    const fixture = window as unknown as { metadata: { version: string }; blockBytes: boolean; fixture: { refresh: () => void } }
    fixture.metadata.version = 'bbbbbbbbbbbbbbbb'
    fixture.blockBytes = true
    fixture.fixture.refresh()
  })
  await expect(page.getByPlaceholder('说明要改什么')).toHaveCount(0)
  await expect(page.locator('.design-image__selection')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '选择图片区域', exact: true })).toBeDisabled()
  await expect(page.getByLabel('聊天输入', { exact: true })).toBeFocused()
  await page.evaluate(() => (window as unknown as { fixture: { unmount: () => void } }).fixture.unmount())
  await expect(page.getByLabel('聊天输入', { exact: true })).toBeFocused()
  expect(await page.evaluate(() => (window as unknown as { notes: { message: string }[] }).notes)).toHaveLength(0)
})

test('a compact image viewport keeps the draft entry and close button physically reachable', async ({ page }, testInfo) => {
  await open(page)
  await select(page)
  await page.getByPlaceholder('说明要改什么').fill('短视口中的草稿')
  for (const height of [80, 48, 32]) {
    await page.locator('.design-image__viewport').evaluate((element, height) => {
      const viewport = element as HTMLElement
      viewport.style.flex = 'none'
      viewport.style.height = height + 'px'
    }, height)
    await expect.poll(async () => page.getByRole('button', { name: '说明要改什么', exact: true }).count()).toBe(1)
    for (const name of ['说明要改什么', '取消']) {
      const control = page.getByRole('button', { name, exact: true })
      await expect.poll(async () => control.evaluate(element => {
        const box = element.getBoundingClientRect()
        const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2)
        return hit === element || !!(hit && element.contains(hit))
      })).toBe(true)
    }
    await testInfo.attach(`compact-${height}`, { body: await page.screenshot(), contentType: 'image/png' })
  }
  await page.getByRole('button', { name: '取消', exact: true }).click()
  await expect(page.getByPlaceholder('说明要改什么')).toHaveCount(0)
})

test('a pointerup handoff before Vue layout wins over the initial composer focus', async ({ page }) => {
  await open(page)
  await select(page, true)
  await expect(page.getByLabel('聊天输入', { exact: true })).toBeFocused()
  await page.evaluate(() => (window as unknown as { fixture: { resize: (width: number) => void } }).fixture.resize(320))
  await insidePane(page)
  await expect(page.getByLabel('聊天输入', { exact: true })).toBeFocused()
})

test('a short real PanelPreview clips the note to its parent rather than the taller image pane', async ({ page }, testInfo) => {
  await open(page)
  await select(page)
  await page.getByPlaceholder('说明要改什么').fill('真实短面板草稿')
  await page.evaluate(() => (window as unknown as { fixture: { resize: (width: number) => void } }).fixture.resize(240))
  for (const height of [150, 220, 660]) {
    await page.evaluate(value => (window as unknown as { fixture: { height: (height: number) => void } }).fixture.height(value), height)
    await expect.poll(async () => page.evaluate(() => {
      const note = document.querySelector('[data-region-note]')!.getBoundingClientRect()
      const panel = document.querySelector('.panel-preview')!.getBoundingClientRect()
      return note.top >= panel.top && note.bottom <= panel.bottom && note.left >= panel.left && note.right <= panel.right
    })).toBe(true)
    for (const name of ['发送', '取消']) {
      await expect.poll(async () => page.getByRole('button', { name, exact: true }).evaluate(element => {
        const box = element.getBoundingClientRect()
        const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2)
        return hit === element || !!(hit && element.contains(hit))
      })).toBe(true)
    }
    await testInfo.attach(`parent-${height}`, { body: await page.screenshot(), contentType: 'image/png' })
  }
  await expect(page.getByPlaceholder('说明要改什么')).toHaveValue('真实短面板草稿')
})
