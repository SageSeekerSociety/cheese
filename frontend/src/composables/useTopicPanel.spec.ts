/** 右侧面板从并排换成浮层的那一下：原来并排开着的那一格要让开。
 *
 * 主区变窄有两个寻常的来路：打开个人芝士（它自己占掉右边一条）、把窗口拖窄。那时
 * 并排放不下，面板改画成压在对话上的一张纸；要是它顺手把「上次看过的那一格」也拉开，
 * 对话就被一块面板和一层遮罩盖住了——2026-11-05 用户报的就是这个。
 */
import { ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

// 主区的宽度在这里直接给（happy-dom 不排版）。
const mainWidth = ref(1300)
vi.mock('@vueuse/core', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@vueuse/core')>()),
  useElementSize: () => ({ width: mainWidth, height: ref(600) }),
}))

vi.mock('@/stores/workspace', () => ({
  useWorkspaceStore: () => ({ panelPref: null, setPanelPref: vi.fn() }),
}))

import { useTopicPanel } from './useTopicPanel'

function setup(initialTab?: string) {
  const tab = ref<string | undefined>(initialTab)
  const setTab = vi.fn((next: string | undefined) => {
    tab.value = next
  })
  const panel = useTopicPanel({
    panes: ref(null),
    tab,
    desktop: ref(true),
    thread: ref(false),
    setTab,
    showing: () => 'site',
  })
  return { tab, setTab, panel }
}

describe('右侧面板换档', () => {
  it('并排换成浮层时，原来并排开着的那一格让开', async () => {
    mainWidth.value = 1300
    const { tab, setTab, panel } = setup('site')
    expect(panel.float.value).toBe(false)
    expect(panel.open.value).toBe(true)

    mainWidth.value = 800 // 打开个人芝士，主区窄了
    await Promise.resolve()
    expect(panel.float.value).toBe(true)
    expect(setTab).toHaveBeenCalledWith(undefined)
    expect(tab.value).toBeUndefined()
  })

  it('本来就在浮层里、人自己点开的那一格不会被让掉', async () => {
    mainWidth.value = 800
    const { tab, setTab, panel } = setup()
    expect(panel.float.value).toBe(true)

    panel.show()
    expect(setTab).toHaveBeenCalledWith('site')
    expect(tab.value).toBe('site')
    expect(panel.open.value).toBe(true)
  })

  it('换回并排时不动地址', async () => {
    mainWidth.value = 800
    const { tab, setTab } = setup()
    setTab.mockClear()
    mainWidth.value = 1300
    await Promise.resolve()
    expect(setTab).not.toHaveBeenCalled()
    expect(tab.value).toBeUndefined()
  })
})

describe('频道页和任务页「对话」那一种：开没开只算这一页', () => {
  it('在一件任务上全关掉，换一件任务、回到频道，旁边又开着', async () => {
    mainWidth.value = 1300
    const where = ref<string | null>('k1')
    const panel = useTopicPanel({
      panes: ref(null),
      tab: ref<string | undefined>(undefined),
      desktop: ref(true),
      thread: ref(false),
      setTab: () => {},
      showing: () => 'overview',
      scope: () => where.value,
    })
    expect(panel.open.value).toBe(true)
    panel.hide()
    expect(panel.open.value).toBe(false)
    where.value = 'k2'
    await Promise.resolve()
    expect(panel.open.value).toBe(true)
    panel.hide()
    where.value = 'channel'
    await Promise.resolve()
    expect(panel.open.value).toBe(true)
  })
})

describe('进来时开不开：频道页放得下对话加概览那一栏就开着，任务页照 1000', () => {
  function enter(width: number, openMin?: number) {
    mainWidth.value = width
    return useTopicPanel({
      panes: ref(null),
      tab: ref<string | undefined>(undefined),
      desktop: ref(true),
      thread: ref(false),
      setTab: () => {},
      showing: () => 'overview',
      scope: () => 'here',
      openMin: openMin === undefined ? undefined : () => openMin,
    })
  }

  it('频道页（概览一栏 316，对话至少 340）：主区 1000 以下、并排时也开着', () => {
    expect(enter(1000, 656).open.value).toBe(true)
    expect(enter(900, 656).open.value).toBe(true)
  })

  it('任务页：主区不到 1000 收着，1000 起开着', () => {
    expect(enter(900).open.value).toBe(false)
    expect(enter(1000).open.value).toBe(true)
  })
})
