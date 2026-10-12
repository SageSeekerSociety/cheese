import type { Block } from '../../../cx_types'

import { computed, defineComponent, nextTick, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useTimeline } from '../../room/composables/useTimeline'
import RoomHoverBar from '../../room/RoomHoverBar.vue'

import { useChatRowActions } from './useChatRowActions'

import { setLocale, t } from '@/i18n'

const blocks: Block[] = ['m1', 'm2'].map((id) => ({
  id,
  conversation_id: 'conversation',
  kind: 'message',
  author_type: 'participant',
  author: 'author',
  content: `Message ${id}: selected passage and the rest`,
  created_at: '2026-10-12T00:00:00Z',
}))

const Harness = defineComponent({
  components: { RoomHoverBar },
  props: {
    editing: { type: String, default: null },
    onReply: { type: Function, required: true },
  },
  setup(props) {
    const timeline = useTimeline()
    timeline.show({ blocks, hasMore: false })
    const scrollRef = ref<HTMLElement | null>(null)
    const contentRef = ref<HTMLElement | null>(null)
    const actions = useChatRowActions({
      timeline,
      scrollRef,
      contentRef,
      rows: computed(() => timeline.messages.value),
      reactionPickerFor: ref(null),
      editingId: computed(() => props.editing),
      loadOlder() {},
    })
    return { blocks, scrollRef, contentRef, ...actions }
  },
  template: `
    <div ref="scrollRef" @mouseover="onTimelinePointer" @mouseleave="hideBar">
      <div ref="contentRef">
        <RoomHoverBar :block="barBlock" :shown="bar.shown" :top="bar.top" :jump="bar.jump"
          :menu-at="bar.menuAt" :is-agent="false" :picker-open="false" :editable="false"
          @reply="onReply" />
        <div v-for="block in blocks" :key="block.id" :data-mid="block.id" data-actions tabindex="0">
          <span class="im-text">{{ block.content }}</span>
          <a href="https://example.com">link</a><input value="editable" />
        </div>
      </div>
    </div>`,
})

const writeText = vi.fn().mockResolvedValue(undefined)
const onReply = vi.fn()
const elementFromPoint = document.elementFromPoint
let view: ReturnType<typeof render> | undefined

async function renderHarness(editing?: string) {
  view = render(Harness, {
    props: { editing, onReply },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  await nextTick()
  return view
}

async function flush() {
  for (let i = 0; i < 4; i++) await new Promise((resolve) => setTimeout(resolve, 0))
}

function row(id: string) {
  return view!.container.querySelector<HTMLElement>(`[data-mid="${id}"]`)!
}

function text(id: string) {
  return row(id).querySelector<HTMLElement>('.im-text')!
}

async function select(from = 'm1', to = from) {
  const range = document.createRange()
  range.setStart(text(from).firstChild!, 12)
  range.setEnd(text(to).firstChild!, 28)
  window.getSelection()!.removeAllRanges()
  window.getSelection()!.addRange(range)
  document.dispatchEvent(new Event('selectionchange'))
  await nextTick()
}

async function context(target: Element = text('m1')) {
  const event = new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 50, clientY: 60 })
  target.dispatchEvent(event)
  await flush()
  return event
}

async function choose(key: string) {
  const item = Array.from(document.querySelectorAll<HTMLElement>('.v-overlay .v-list-item')).find(
    (element) => element.textContent?.trim() === t(key)
  )
  expect(item, `Missing action: ${key}`).toBeTruthy()
  await fireEvent.click(item!)
  await flush()
}

beforeEach(() => {
  setLocale('en')
  writeText.mockClear()
  onReply.mockClear()
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
  vi.stubGlobal('visualViewport', {
    width: 1280,
    height: 800,
    offsetLeft: 0,
    offsetTop: 0,
    scale: 1,
    addEventListener() {},
    removeEventListener() {},
  })
  vi.stubGlobal('devicePixelRatio', 1)
  document.elementFromPoint = () => null
})

afterEach(() => {
  view?.unmount()
  view = undefined
  window.getSelection()!.removeAllRanges()
  document.elementFromPoint = elementFromPoint
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('message actions for selected passages', () => {
  it('selection reveals Reply and pins it to that message while moving to the bar', async () => {
    const current = await renderHarness()
    await select()
    await fireEvent.mouseLeave(current.container.firstElementChild!)
    await fireEvent.mouseOver(text('m2'))
    expect(current.container.querySelector('.hover-bar')?.getAttribute('aria-hidden')).toBe('false')
    await fireEvent.click(current.getByRole('button', { name: t('work.room.message.reply') }))
    expect(onReply).toHaveBeenCalledWith(blocks[0])
  })

  it('right-clicking a selected passage opens Reply for the original message', async () => {
    await renderHarness()
    await select()
    expect((await context()).defaultPrevented).toBe(true)
    await fireEvent.mouseOver(text('m2'))
    await choose('work.room.message.reply')
    expect(onReply).toHaveBeenCalledWith(blocks[0])
  })

  it('copies the selected passage even after the menu takes focus and the selection collapses', async () => {
    await renderHarness()
    await select()
    const selected = window.getSelection()!.toString()
    await context()
    window.getSelection()!.removeAllRanges()
    document.dispatchEvent(new Event('selectionchange'))
    await choose('work.room.message.copySelection')
    expect(writeText).toHaveBeenCalledWith(selected)
  })

  it('the original Copy action still copies the complete message', async () => {
    await renderHarness()
    await select()
    await context()
    await choose('work.room.message.copy')
    expect(writeText).toHaveBeenCalledWith(blocks[0]!.content)
  })

  it('without a selection, right-click Reply works as before', async () => {
    await renderHarness()
    expect((await context()).defaultPrevented).toBe(true)
    await choose('work.room.message.reply')
    expect(onReply).toHaveBeenCalledWith(blocks[0])
  })

  it('clearing the selection releases the bar for other messages', async () => {
    const current = await renderHarness()
    await select()
    window.getSelection()!.removeAllRanges()
    document.dispatchEvent(new Event('selectionchange'))
    await fireEvent.mouseOver(text('m2'))
    await fireEvent.click(current.getByRole('button', { name: t('work.room.message.reply') }))
    expect(onReply).toHaveBeenCalledWith(blocks[1])
  })

  it('cross-message selections retain the native copy menu', async () => {
    await renderHarness()
    await select('m1', 'm2')
    expect((await context()).defaultPrevented).toBe(false)
  })

  it('selection in a different message does not hijack this row’s native menu', async () => {
    await renderHarness()
    await select('m2')
    expect((await context()).defaultPrevented).toBe(false)
  })

  it('links and inputs retain their native menus even with a passage selected', async () => {
    await renderHarness()
    await select()
    expect((await context(row('m1').querySelector('a')!)).defaultPrevented).toBe(false)
    expect((await context(row('m1').querySelector('input')!)).defaultPrevented).toBe(false)
  })

  it('the row being edited keeps its native menu', async () => {
    await renderHarness('m1')
    await select()
    expect((await context()).defaultPrevented).toBe(false)
  })
})
