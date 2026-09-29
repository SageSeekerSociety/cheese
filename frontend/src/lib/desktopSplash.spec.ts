import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { landBootSplash } from './desktopSplash'

// index.html's boot splash covers the whole window in the desktop app; once the
// app is up it must never be left covering it, whether or not there is a rail
// to land in.
function splash() {
  document.body.insertAdjacentHTML(
    'afterbegin',
    '<div id="boot-splash" style="position: fixed; inset: 0"><div class="boot-tile"></div></div>'
  )
  const tile = document.querySelector<HTMLElement>('.boot-tile')!
  tile.getBoundingClientRect = () => new DOMRect(500, 300, 112, 112)
}

function railHomeSlot() {
  document.body.insertAdjacentHTML('beforeend', '<a class="app-rail-item-cheese"></a>')
  const slot = document.querySelector<HTMLElement>('.app-rail-item-cheese')!
  slot.getBoundingClientRect = () => new DOMRect(8, 40, 48, 48)
}

// happy-dom has no Web Animations; every animation here finishes at once.
beforeEach(() => {
  HTMLElement.prototype.animate = vi.fn(() => ({ finished: Promise.resolve() }) as unknown as Animation)
})

afterEach(() => {
  document.body.innerHTML = ''
  delete (HTMLElement.prototype as Partial<HTMLElement>).animate
})

describe('landBootSplash', () => {
  it('uncovers the app after landing the tile in the rail', async () => {
    splash()
    railHomeSlot()
    await landBootSplash()
    expect(document.getElementById('boot-splash')).toBeNull()
  })

  it('uncovers the app when there is no rail to land in', async () => {
    splash()
    await landBootSplash()
    expect(document.getElementById('boot-splash')).toBeNull()
  })

  it('does nothing outside the desktop app, where there is no splash', async () => {
    document.body.innerHTML = '<div id="app"></div>'
    await landBootSplash()
    expect(document.getElementById('app')).not.toBeNull()
  })
})
