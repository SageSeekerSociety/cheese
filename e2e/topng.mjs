import { chromium } from '@playwright/test'
import { readFileSync, writeFileSync } from 'node:fs'
const b = await chromium.launch()
const p = await b.newPage()
for (const n of process.argv.slice(2)) {
  const b64 = readFileSync(`/tmp/shots/${n}.webp`).toString('base64')
  const png = await p.evaluate(async (d) => {
    const img = new Image(); img.src = 'data:image/webp;base64,' + d; await img.decode()
    const c = document.createElement('canvas'); c.width = img.naturalWidth; c.height = img.naturalHeight
    c.getContext('2d').drawImage(img, 0, 0)
    return c.toDataURL('image/png').split(',')[1]
  }, b64)
  writeFileSync(`/tmp/shots/${n}.png`, Buffer.from(png, 'base64'))
}
await b.close()
