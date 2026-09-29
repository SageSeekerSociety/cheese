// In the desktop app the page opens behind a boot splash (index.html): the
// rail's home tile, large, in the middle of the window, where the app's own
// first page left it. Once the app is on screen the tile shrinks into the home
// slot on the rail, which is the same tile, and the page shows through around
// it. Without a rail to land in (signed out, narrow window) the splash fades.

const LAND_MS = 360
const FADE_MS = 150
const HANDOVER_MS = 120
const EASE_OUT = 'cubic-bezier(0.2, 0.8, 0.2, 1)'
// The rail's items can arrive a moment after the page; this long is worth
// waiting for them rather than fading.
const SLOT_WAIT_MS = 800

function homeSlot(deadline: number): Promise<HTMLElement | null> {
  return new Promise((resolve) => {
    const look = () => {
      const slot = document.querySelector<HTMLElement>('.app-rail-item-cheese')
      if (slot && slot.getBoundingClientRect().width > 0) return resolve(slot)
      if (performance.now() >= deadline) return resolve(null)
      requestAnimationFrame(look)
    }
    look()
  })
}

function finished(animation: Animation): Promise<void> {
  return animation.finished.then(
    () => undefined,
    () => undefined
  )
}

export async function landBootSplash(): Promise<void> {
  const splash = document.getElementById('boot-splash')
  if (!splash) return
  if (getComputedStyle(splash).display === 'none' || typeof splash.animate !== 'function') {
    splash.remove()
    return
  }

  const tile = splash.querySelector<HTMLElement>('.boot-tile')
  const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  const slot = reduced ? null : await homeSlot(performance.now() + SLOT_WAIT_MS)
  const to = slot?.getBoundingClientRect()

  if (!tile || !slot || !to) {
    await finished(splash.animate([{ opacity: 1 }, { opacity: 0 }], { duration: FADE_MS, fill: 'forwards' }))
    splash.remove()
    return
  }

  const from = tile.getBoundingClientRect()
  const scale = to.width / from.width
  tile.style.transformOrigin = '0 0'
  const ground = getComputedStyle(splash).backgroundColor
  // The slot stays empty while the tile is on its way, then the two cross over
  // as it arrives, so whichever look the slot has (current page or not) takes
  // over without a jump.
  slot.style.visibility = 'hidden'
  const reveal = window.setTimeout(() => (slot.style.visibility = ''), LAND_MS - HANDOVER_MS)
  await Promise.all([
    finished(
      tile.animate(
        [
          { transform: 'none' },
          { transform: `translate(${to.left - from.left}px, ${to.top - from.top}px) scale(${scale})` },
        ],
        { duration: LAND_MS, easing: EASE_OUT, fill: 'forwards' }
      )
    ),
    finished(
      tile.animate([{ opacity: 1 }, { opacity: 0 }], {
        duration: HANDOVER_MS,
        delay: LAND_MS - HANDOVER_MS,
        fill: 'forwards',
      })
    ),
    finished(
      splash.animate([{ backgroundColor: ground }, { backgroundColor: 'transparent' }], {
        duration: LAND_MS - 100,
        easing: EASE_OUT,
        fill: 'forwards',
      })
    ),
  ])
  window.clearTimeout(reveal)
  slot.style.visibility = ''
  splash.remove()
}
