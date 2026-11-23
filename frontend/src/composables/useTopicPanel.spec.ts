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
