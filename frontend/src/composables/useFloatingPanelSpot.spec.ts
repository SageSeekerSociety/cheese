// 芝士面板取消固定之后那块浮层：拖到哪停在哪、换屏幕还在那个相对角落、窗口改小收得回屏幕里、
// 双击回右下角、没拖过就交给 CSS 停在右下角。
import { ref } from 'vue'
import { beforeEach, expect, it } from 'vitest'

import { useFloatingPanelSpot } from './useFloatingPanelSpot'

const KEY = 'test.floatSpot'

/** jsdom／happy-dom 不排版：给浮层一个量得出的尺寸，再摆一个视口。 */
function panelIn(width = 400, height = 600) {
  const node = document.createElement('div')
  Object.defineProperty(node, 'offsetWidth', { configurable: true, value: width })
  Object.defineProperty(node, 'offsetHeight', { configurable: true, value: height })
  node.getBoundingClientRect = () => ({ left: 584, top: 168, width, height, right: 984, bottom: 768 }) as DOMRect
  document.body.appendChild(node)
  return ref<HTMLElement | null>(node)
}

function setViewport(width: number, height: number) {
  const w = window as unknown as { innerWidth: number; innerHeight: number }
  w.innerWidth = width
  w.innerHeight = height
}

function drag(spot: ReturnType<typeof useFloatingPanelSpot>, fromX: number, fromY: number, dx: number, dy: number) {
  spot.startDrag(new PointerEvent('pointerdown', { button: 0, pointerId: 1, clientX: fromX, clientY: fromY }))
  window.dispatchEvent(
    new PointerEvent('pointermove', { pointerId: 1, buttons: 1, clientX: fromX + dx, clientY: fromY + dy })
  )
  window.dispatchEvent(new PointerEvent('pointerup', { pointerId: 1 }))
}

beforeEach(() => {
  localStorage.clear()
  setViewport(1000, 800)
  document.body.innerHTML = ''
})

it('没拖过时什么样式都不给：浮层停在自己的右下角', () => {
  const spot = useFloatingPanelSpot(KEY, panelIn())
  expect(spot.style.value).toBeUndefined()
})

it('拖到哪停在哪，位置记着，下次打开还在那儿', () => {
  const spot = useFloatingPanelSpot(KEY, panelIn())
  drag(spot, 700, 200, -300, -100)

  expect(spot.style.value).toMatchObject({ left: '284px', top: '68px', right: 'auto', bottom: 'auto' })
  expect(spot.spot.value).toEqual({ x: 0.284, y: 0.085 })
  expect(useFloatingPanelSpot(KEY, panelIn()).style.value).toMatchObject({ left: '284px', top: '68px' })
})

it('记的是比例：窗口拉宽之后还在那个相对位置', () => {
  const spot = useFloatingPanelSpot(KEY, panelIn())
  drag(spot, 700, 200, -300, -100)
  setViewport(2000, 1600)
  spot.settle()

  // 0.284 × 2000 = 568，0.085 × 1600 = 136。面板也还在屏幕里。
  expect(spot.style.value).toMatchObject({ left: '568px', top: '136px' })
})

it('窗口改小到浮层放不下时，收回屏幕里，离边留 12px', () => {
  const spot = useFloatingPanelSpot(KEY, panelIn())
  // 视口 1000×800，面板 400×600：右下角最多到 (588, 188)。
  drag(spot, 700, 200, 280, 500)
  expect(spot.style.value).toMatchObject({ left: '588px', top: '188px' })

  // 新视口 640×400：面板 400×600 比视口还高，左上角只能收到上边那 12px。
  setViewport(640, 400)
  spot.settle()
  expect(spot.style.value).toMatchObject({ left: '228px', top: '12px' })
})

it('按一下不挪位置：挪过 3px 才算拖', () => {
  const spot = useFloatingPanelSpot(KEY, panelIn())
  spot.startDrag(new PointerEvent('pointerdown', { button: 0, pointerId: 1, clientX: 700, clientY: 200 }))
  window.dispatchEvent(new PointerEvent('pointermove', { pointerId: 1, buttons: 1, clientX: 701, clientY: 201 }))
  expect(spot.dragging.value).toBe(false)
  window.dispatchEvent(new PointerEvent('pointerup', { pointerId: 1 }))
  expect(spot.style.value).toBeUndefined()
  expect(localStorage.getItem(KEY)).toBeNull()
})

it('拖动中途不写存储，松手写一次；取消也算松手', () => {
  const spot = useFloatingPanelSpot(KEY, panelIn())
  spot.startDrag(new PointerEvent('pointerdown', { button: 0, pointerId: 1, clientX: 700, clientY: 200 }))
  window.dispatchEvent(new PointerEvent('pointermove', { pointerId: 1, buttons: 1, clientX: 600, clientY: 150 }))
  expect(localStorage.getItem(KEY)).toBeNull()
  window.dispatchEvent(new PointerEvent('pointercancel', { pointerId: 1 }))
  expect(localStorage.getItem(KEY)).not.toBeNull()
  expect(spot.dragging.value).toBe(false)

  // 松手之后再动鼠标不挪。
  window.dispatchEvent(new PointerEvent('pointermove', { pointerId: 1, buttons: 1, clientX: 100, clientY: 100 }))
  expect(spot.style.value).toMatchObject({ left: '484px', top: '118px' })
})

it('在窗口外松手（收不到 pointerup）之后再动鼠标，面板不跟着漂', () => {
  const spot = useFloatingPanelSpot(KEY, panelIn())
  // 松手发生在窗口外：后面来的是一个没有按键的 pointermove。
  spot.startDrag(new PointerEvent('pointerdown', { button: 0, pointerId: 1, clientX: 700, clientY: 200 }))
  window.dispatchEvent(new PointerEvent('pointermove', { pointerId: 1, buttons: 1, clientX: 690, clientY: 190 }))
  window.dispatchEvent(new PointerEvent('pointermove', { pointerId: 1, buttons: 0, clientX: 400, clientY: 100 }))

  expect(spot.dragging.value).toBe(false)
  window.dispatchEvent(new PointerEvent('pointermove', { pointerId: 1, buttons: 0, clientX: 100, clientY: 50 }))

  // 停在没有按键之前最后一次按住时挪到的位置（700−10, 200−10 起算），不是后来飘过去的点。
  expect(spot.style.value).toMatchObject({ left: '574px', top: '158px' })
})

it('拖到一半面板被关掉（元素没了），不会写进一个坏位置', () => {
  const el = panelIn()
  const spot = useFloatingPanelSpot(KEY, el)
  spot.startDrag(new PointerEvent('pointerdown', { button: 0, pointerId: 1, clientX: 700, clientY: 200 }))
  window.dispatchEvent(new PointerEvent('pointermove', { pointerId: 1, buttons: 1, clientX: 600, clientY: 150 }))

  el.value = null
  window.dispatchEvent(new PointerEvent('pointermove', { pointerId: 1, buttons: 1, clientX: 590, clientY: 140 }))

  // 用的是按下时量到的尺寸，不是 0×0。
  expect(spot.spot.value!.x).toBeGreaterThan(0.4)
  expect(spot.spot.value!.y).toBeGreaterThan(0)
})

it('双击回右下角：位置忘掉，样式交还给 CSS', () => {
  const spot = useFloatingPanelSpot(KEY, panelIn())
  drag(spot, 700, 200, -300, -100)
  spot.reset()

  expect(spot.style.value).toBeUndefined()
  expect(localStorage.getItem(KEY)).toBeNull()
  expect(useFloatingPanelSpot(KEY, panelIn()).style.value).toBeUndefined()
})

it('存着的值读不懂就当没记过', () => {
  localStorage.setItem(KEY, '{"x":"left","y":3}')
  expect(useFloatingPanelSpot(KEY, panelIn()).style.value).toBeUndefined()
  localStorage.setItem(KEY, 'wide')
  expect(useFloatingPanelSpot(KEY, panelIn()).style.value).toBeUndefined()
})

it('一只手指拖到一半，另一只手指按下来不算新的一拖', () => {
  const spot = useFloatingPanelSpot(KEY, panelIn())
  spot.startDrag(new PointerEvent('pointerdown', { button: 0, pointerId: 1, clientX: 700, clientY: 200 }))
  window.dispatchEvent(new PointerEvent('pointermove', { pointerId: 1, buttons: 1, clientX: 650, clientY: 180 }))

  // 第二根手指从别处按下来：不改变这一拖的起点。
  spot.startDrag(new PointerEvent('pointerdown', { button: 0, pointerId: 2, clientX: 200, clientY: 100 }))
  window.dispatchEvent(new PointerEvent('pointermove', { pointerId: 1, buttons: 1, clientX: 600, clientY: 160 }))
  expect(spot.style.value).toMatchObject({ left: '484px', top: '128px' })

  // 第一根手指抬起：拖结束，位置记下来。
  window.dispatchEvent(new PointerEvent('pointerup', { pointerId: 1 }))
  expect(spot.dragging.value).toBe(false)
  expect(localStorage.getItem(KEY)).not.toBeNull()

  // 之后第二根手指再动，面板不动。
  window.dispatchEvent(new PointerEvent('pointermove', { pointerId: 2, buttons: 1, clientX: 300, clientY: 60 }))
  expect(spot.style.value).toMatchObject({ left: '484px', top: '128px' })
})

it('第二根手指抬起，结束不了第一根手指在拖的那一次', () => {
  const spot = useFloatingPanelSpot(KEY, panelIn())
  spot.startDrag(new PointerEvent('pointerdown', { button: 0, pointerId: 1, clientX: 700, clientY: 200 }))
  window.dispatchEvent(new PointerEvent('pointermove', { pointerId: 1, buttons: 1, clientX: 650, clientY: 180 }))

  // 第二根手指按下来又抬起：这一拖还在继续。
  spot.startDrag(new PointerEvent('pointerdown', { button: 0, pointerId: 2, clientX: 200, clientY: 100 }))
  window.dispatchEvent(new PointerEvent('pointerup', { pointerId: 2 }))
  expect(spot.dragging.value).toBe(true)
  expect(localStorage.getItem(KEY)).toBeNull()

  window.dispatchEvent(new PointerEvent('pointermove', { pointerId: 1, buttons: 1, clientX: 600, clientY: 160 }))
  expect(spot.style.value).toMatchObject({ left: '484px', top: '128px' })

  // 第一根手指抬起才算完。
  window.dispatchEvent(new PointerEvent('pointerup', { pointerId: 1 }))
  expect(spot.dragging.value).toBe(false)
  expect(localStorage.getItem(KEY)).not.toBeNull()
})
