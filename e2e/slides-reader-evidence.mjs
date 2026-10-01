// Actual Vue reader + real PDF.js, synthetic PDF bytes. No backend or conversion service.
import { chromium } from '@playwright/test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'

const frontend = path.resolve(import.meta.dirname, '../frontend')
process.chdir(frontend)
const require = createRequire(path.join(frontend, 'package.json'))
const { createServer } = await import(require.resolve('vite'))
const scratch = path.join(frontend, '.tmp/slides-reader-evidence')
const output = path.resolve(import.meta.dirname, 'test-results/slides-reader')
await fs.mkdir(scratch, { recursive: true })
await fs.mkdir(output, { recursive: true })

function fixturePdf() {
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R 5 0 R 7 0 R] /Count 3 >>',
  ]
  for (let n = 1; n <= 3; n++) {
    const stream = `0.96 0.94 0.90 rg 0 0 960 540 re f\n0.20 0.22 0.24 rg BT /F1 40 Tf 64 420 Td (Slide ${n}: actual PDF fixture) Tj 0 -80 Td /F1 22 Tf (Navigation, fit and page context) Tj ET\nBT /F1 24 Tf 0 1 -1 0 840 80 Tm (Rotated text) Tj ET\nBT /F1 28 Tf 160 Tz 1 0 0 1 64 160 Tm (Stretched text) Tj ET`
    objects.push(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 960 540] /Resources << /Font << /F1 9 0 R >> >> /Contents ${objects.length + 2} 0 R >>`)
    objects.push(`<< /Length ${Buffer.byteLength(stream)} >>\nstream\n${stream}\nendstream`)
  }
  objects.push('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>')
  let pdf = '%PDF-1.4\n'
  const offsets = [0]
  for (let i = 0; i < objects.length; i++) {
    offsets.push(Buffer.byteLength(pdf))
    pdf += `${i + 1} 0 obj\n${objects[i]}\nendobj\n`
  }
  const xref = Buffer.byteLength(pdf)
  pdf += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`
  pdf += offsets.slice(1).map((offset) => `${String(offset).padStart(10, '0')} 00000 n \n`).join('')
  pdf += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`
  return Buffer.from(pdf)
}
await fs.writeFile(path.join(scratch, 'fixture.pdf'), fixturePdf())
await fs.writeFile(path.join(scratch, 'index.html'), '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><body><div id="app"></div><script type="module" src="./main.js"></script></body></html>')
await fs.writeFile(path.join(scratch, 'main.js'), `
import {createApp, h, ref} from 'vue';
import PreviewSlides from '/src/components/panels/preview/PreviewSlides.vue';
import {setLocale} from '/src/i18n/index.ts';
import '/src/style.css';
setLocale('zh-CN');
const data=ref(null); const mode=ref('loaded'); window.events=[];
window.evidenceMode=(value)=>mode.value=value;
createApp({setup(){return()=>h('div',{style:'height:640px;display:flex;flex-direction:column'},[
 h(PreviewSlides,{data:mode.value==='loaded'?data.value:null,title:'示例幻灯片 · 实际 PDF.js 渲染',pending:mode.value==='loading',error:mode.value==='error'?'示例：PDF 文件损坏':'',rendererMissing:mode.value==='missing',canDownload:true,context:{topicId:'fixture-room',path:'fixture.pptx',source:'committed',taskId:'fixture-task',version:'fixture-v1'},onPageContext:(event)=>window.events.push(event),onDownload:()=>window.events.push({download:true})})])}}).mount('#app');
fetch('./fixture.pdf').then(response=>response.arrayBuffer()).then(value=>data.value=value);
`)
const server = await createServer({ root: frontend, server: { host: '127.0.0.1', port: 5397, strictPort: true } })
await server.listen()
let browser
try {
  browser = await chromium.launch()
  const page = await browser.newPage({ viewport: { width: 1120, height: 760 }, locale: 'zh-CN' })
  const errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.goto('http://127.0.0.1:5397/.tmp/slides-reader-evidence/index.html')
  await page.getByRole('button', { name: '下一页', exact: true }).waitFor()
  await page.waitForFunction(() => document.querySelector('.slide-text-layer span'))
  const textGeometry = await page.evaluate(() => {
    const host = document.querySelector('.slides__sheet')
    const canvas = host.querySelector('canvas')
    const scale = canvas.getBoundingClientRect().width / 960
    return [...host.querySelectorAll('.slide-text-layer span')].filter(span => span.textContent.trim()).map(span => {
      const range = document.createRange()
      range.selectNodeContents(span)
      const box = range.getBoundingClientRect()
      const origin = canvas.getBoundingClientRect()
      const style = getComputedStyle(span)
      return { text: span.textContent, font: parseFloat(style.fontSize), height: parseFloat(span.style.getPropertyValue('--font-height')), transform: style.transform, scale, x: box.x-origin.x, y: box.y-origin.y, width: box.width, boxHeight: box.height }
    })
  })
  await fs.writeFile(path.join(output, 'text-geometry.json'), JSON.stringify(textGeometry, null, 2))
  for (const item of textGeometry) assert.ok(Math.abs(item.font-item.height*item.scale) < 0.2, JSON.stringify(item))
  const rotated = textGeometry.find(item => item.text === 'Rotated text')
  assert.ok(rotated.boxHeight > rotated.width * 2, JSON.stringify(rotated))
  const stretched = textGeometry.find(item => item.text === 'Stretched text')
  assert.notEqual(stretched.transform, 'none')
  const sheet = page.locator('.slides__sheet')
  await sheet.screenshot({ path: path.join(output, 'actual-pdf-page.png') })
  await page.screenshot({ path: path.join(output, 'light.png') })
  await page.getByRole('button', { name: '下一页', exact: true }).click()
  assert.equal(await page.getByRole('spinbutton', { name: '页码' }).inputValue(), '2')
  await page.getByRole('button', { name: '对整页提问' }).click()
  await page.waitForFunction(() => window.events.length === 1)
  const context = await page.evaluate(() => window.events[0])
  assert.equal(context.page, 2)
  assert.match(context.text, /Slide 2: actual PDF fixture/)
  assert.equal(context.context.source, 'committed')
  const trigger = page.getByRole('button', { name: '演示', exact: true })
  await trigger.click()
  await page.keyboard.press('ArrowRight')
  assert.equal(await page.getByRole('spinbutton', { name: '页码' }).inputValue(), '3')
  await page.screenshot({ path: path.join(output, 'presentation.png') })
  await page.keyboard.press('Escape')
  assert.equal(await page.evaluate(() => document.activeElement?.textContent?.trim()), '演示')
  await page.evaluate(() => document.documentElement.dataset.theme = 'dark')
  await page.screenshot({ path: path.join(output, 'dark.png') })
  await page.setViewportSize({ width: 380, height: 760 })
  await page.waitForFunction(() => !document.querySelector('.slide-rail'))
  const geometry = await page.evaluate(() => {
    const pane = document.querySelector('.slides').getBoundingClientRect()
    const sheet = document.querySelector('.slides__sheet').getBoundingClientRect()
    return { pane: pane.width, sheet: sheet.width, right: sheet.right, viewport: innerWidth }
  })
  assert.ok(geometry.sheet <= geometry.pane, JSON.stringify(geometry))
  assert.ok(geometry.right <= geometry.viewport, JSON.stringify(geometry))
  await page.screenshot({ path: path.join(output, 'narrow-dark.png') })
  await page.getByRole('button', { name: '缩略图', exact: true }).click()
  await page.getByRole('navigation', { name: '缩略图' }).waitFor()
  await page.getByRole('button', { name: '缩略图', exact: true }).click()
  await page.setViewportSize({ width: 1120, height: 760 })
  await page.evaluate(() => document.documentElement.dataset.theme = 'light')
  for (const mode of ['loading', 'error', 'missing']) {
    await page.evaluate((value) => window.evidenceMode(value), mode)
    await page.screenshot({ path: path.join(output, `${mode}.png`) })
    await page.getByRole('button', { name: '下载原文件' }).click()
  }
  assert.deepEqual(errors, [])
  await fs.writeFile(path.join(output, 'behavior.json'), JSON.stringify({ boundary: 'actual PreviewSlides + PDF.js, synthetic PDF; no API/Office/deployment', context, geometry, errors }, null, 2))
  const report = await browser.newPage({ viewport: { width: 1120, height: 760 } })
  const sections = [
    ['light', '阅读与页导航', '示例文件使用真实 PDF.js 解析和文字层。上一页、下一页、页码跳转、缩略图与适合页面可用。'],
    ['dark', '深色主题', '工具栏和阅读区使用产品主题。PDF 原页面保持其实际颜色，不修改原文件。'],
    ['narrow-dark', '窄面板', '缩略图默认收起，可手动展开。内容按面板宽度显示；工具栏换行。'],
    ['presentation', '演示与退出', '演示保持同一文档与当前页。阅读器拥有焦点时支持逐页操作；退出回到触发按钮。'],
    ['loading', '加载状态', '加载时显示状态，原始文件下载仍可用。示例状态由 fixture 控制。'],
    ['error', '读取错误', '显示实际错误原因，保留下载出口。此屏是示例错误，不是线上事故。'],
    ['missing', '转换服务不可用', '保留原文件下载；此屏仅验证状态展示，没有调用实际 Office 转换。'],
  ]
  const images = await Promise.all(sections.map(async ([name, title, body]) => {
    const bytes = await fs.readFile(path.join(output, `${name}.png`))
    return `<section><h2>${title}</h2><p>${body}</p><img src="data:image/png;base64,${bytes.toString('base64')}"></section>`
  }))
  await report.setContent(`<html lang="zh-CN"><meta charset="utf-8"><style>body{font:16px sans-serif;margin:24px;color:#222}h1{font-size:26px}section{break-before:page}img{width:100%}p{line-height:1.7}</style><h1>幻灯片阅读器 · 实际组件审阅</h1><p>未部署的正式 Vue 组件，示例 PDF 字节。浏览器使用真实 PDF.js；没有 API 替身请求，也没有实际 PPTX 转换、OnlyOffice 或部署验收。</p><p>整页提问从实际第 2 页提取全部 PDF 文字，输出明确整页上下文和文件身份。选中文字仍走独立 quote 事件；正式对话接线尚需 owner 合入。</p>${images.join('')}</html>`)
  await report.pdf({ path: path.join(output, 'slides-reader-review-zh.pdf'), format: 'A4', printBackground: true })
  console.log('BROWSER_BEHAVIOR_EXIT=0; actual component/PDF.js fixture; review PDF:', path.join(output, 'slides-reader-review-zh.pdf'))
} finally {
  await browser?.close()
  await server.close()
}
