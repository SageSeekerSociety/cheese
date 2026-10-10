/** 「开始清单」的判据和退休规则。
 *
 *  这张卡不落字段：每一步做没做都从别处的真实状态推出来，两件必做的都做完它就
 *  自己退场。这里锁的就是这套推导——什么算做完了、什么时候整个消失、以及「问
 *  服务端」的那两条什么时候**不问**（项目本体是每次进来都会开的那一页，不该每次
 *  都多发两个请求）。
 */
import { effectScope, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ library: vi.fn(), started: vi.fn() }))

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return { ...actual, getGettingStarted: mocks.started }
})
vi.mock('../lib/libraryApi', async () => {
  const actual = await vi.importActual<typeof import('../lib/libraryApi')>('../lib/libraryApi')
  return { ...actual, listProjectLibrary: mocks.library }
})

import { useGettingStarted } from './useGettingStarted'

const okLibrary = (count: number) => ({
  data: Array.from({ length: count }, (_, i) => ({ path: `f${i}` })),
  total: count,
})

function inScope<T>(fn: () => T): { value: T; stop: () => void } {
  const scope = effectScope()
  const value = scope.run(fn) as T
  return { value, stop: () => scope.stop() }
}

async function flush() {
  for (let i = 0; i < 4; i += 1) await new Promise((r) => setTimeout(r, 0))
}

interface Setup {
  projectId?: string
  on?: boolean
  agentHasSpoken?: boolean
  roomHasAttachment?: boolean
  members?: { user_handle: string; agent?: boolean }[]
  /** 项目本体上那层手把手引导还在不在场（它就是那个「除了这张卡还有人要读」的人）。 */
  alsoProbe?: boolean
}

function setup(opts: Setup = {}) {
  const projectId = ref<string | null>(opts.projectId ?? 'p1')
  const spoken = ref(opts.agentHasSpoken ?? false)
  const attached = ref(opts.roomHasAttachment ?? false)
  const onLine = ref(opts.on ?? true)
  const { value, stop } = inScope(() =>
    useGettingStarted({
      projectId: () => projectId.value,
      on: () => onLine.value,
      agentHasSpoken: () => spoken.value,
      roomHasAttachment: () => attached.value,
      members: () => opts.members ?? [],
      alsoProbe: () => opts.alsoProbe ?? false,
    })
  )
  return { ...value, projectId, spoken, attached, onLine, stop }
}

function done(key: string, steps: { key: string; done: boolean }[]): boolean {
  return steps.find((s) => s.key === key)?.done ?? false
}

beforeEach(() => {
  mocks.library.mockReset().mockResolvedValue(okLibrary(0))
  mocks.started.mockReset().mockResolvedValue({ talked: false, landed: false })
  localStorage.clear()
})
afterEach(() => localStorage.clear())

describe('useGettingStarted', () => {
  it('刚建好的项目：四步都在，卡片在', async () => {
    const gs = setup()
    await flush()
    expect(gs.visible.value).toBe(true)
    expect(gs.steps.value.map((s) => s.done)).toEqual([false, false, false, false])
    gs.stop()
  })

  it('跟芝士说上话 + 放进材料，两件必做的都做完，卡片自己退场', async () => {
    const gs = setup({ agentHasSpoken: true, roomHasAttachment: true })
    await flush()
    expect(done('talk', gs.steps.value)).toBe(true)
    expect(done('materials', gs.steps.value)).toBe(true)
    expect(gs.visible.value).toBe(false)
    gs.stop()
  })

  it('材料放进项目资料库也算数，不必非得拖进这个房间', async () => {
    mocks.library.mockResolvedValue(okLibrary(2))
    const gs = setup()
    await flush()
    expect(done('materials', gs.steps.value)).toBe(true)
    gs.stop()
  })

  it('仓库里合进过一次采纳的改动、名册上有第二个人：各自打勾，但卡片还在（必做的没做完）', async () => {
    mocks.started.mockResolvedValue({ talked: false, landed: true })
    const gs = setup({
      members: [{ user_handle: 'me' }, { user_handle: 'cheese', agent: true }, { user_handle: 'bobby' }],
    })
    localStorage.setItem('user', JSON.stringify({ username: 'me' }))
    await flush()
    expect(done('repo', gs.steps.value)).toBe(true)
    expect(done('people', gs.steps.value)).toBe(true)
    expect(done('talk', gs.steps.value)).toBe(false)
    expect(gs.visible.value).toBe(true)
    gs.stop()
  })

  it('名册上只有自己和 AI 队友时，那一步不算做完', async () => {
    const gs = setup({
      members: [{ user_handle: 'me' }, { user_handle: 'cheese', agent: true }],
    })
    localStorage.setItem('user', JSON.stringify({ username: 'me' }))
    await flush()
    expect(done('people', gs.steps.value)).toBe(false)
    gs.stop()
  })

  // 跟芝士的来回可能只发生在任务对话里，这一栏读不到那里（dev，2026-10-09）。
  it('在任务里跟芝士说上过话也算：服务端说说过了，这一步打勾', async () => {
    mocks.started.mockResolvedValue({ talked: true, landed: false })
    const gs = setup()
    await flush()
    expect(done('talk', gs.steps.value)).toBe(true)
    gs.stop()
  })

  it('去任务里聊完再回到这一栏，重新问一次', async () => {
    const gs = setup()
    await flush()
    expect(done('talk', gs.steps.value)).toBe(false)

    gs.onLine.value = false
    mocks.started.mockResolvedValue({ talked: true, landed: false })
    await flush()
    gs.onLine.value = true
    await flush()
    expect(done('talk', gs.steps.value)).toBe(true)
    gs.stop()
  })

  it('这一栏自己看见芝士开过口就算说过话，服务端没看见也不改回去', async () => {
    const gs = setup({ agentHasSpoken: true })
    await flush()
    expect(done('talk', gs.steps.value)).toBe(true)
    gs.stop()
  })

  // dev，2026-10-10：平台托管的项目往资料库里放一个文件，仓库就备好了、算「接上了」，
  // 这一步跟着打勾，而仓库里什么都没合进去过。
  it('仓库接上了但还没合进过采纳的改动：「让成果进代码仓库」不打勾', async () => {
    mocks.library.mockResolvedValue(okLibrary(1))
    mocks.started.mockResolvedValue({ talked: false, landed: false })
    const gs = setup()
    await flush()
    expect(done('materials', gs.steps.value)).toBe(true)
    expect(done('repo', gs.steps.value)).toBe(false)
    gs.stop()
  })

  it('去采纳了一次交付再回到这一栏，重新问一次，这一步打勾', async () => {
    const gs = setup()
    await flush()
    expect(done('repo', gs.steps.value)).toBe(false)

    gs.onLine.value = false
    mocks.started.mockResolvedValue({ talked: false, landed: true })
    await flush()
    gs.onLine.value = true
    await flush()
    expect(done('repo', gs.steps.value)).toBe(true)
    gs.stop()
  })

  it('说过话、也进过仓库了，就不再去问服务端', async () => {
    mocks.started.mockResolvedValue({ talked: true, landed: true })
    const gs = setup()
    await flush()
    gs.onLine.value = false
    await flush()
    gs.onLine.value = true
    await flush()
    expect(mocks.started).toHaveBeenCalledTimes(1)
    gs.stop()
  })

  it('不在项目本体上（普通话题）就不画，也不去问服务端', async () => {
    const gs = setup({ on: false })
    await flush()
    expect(gs.visible.value).toBe(false)
    expect(mocks.library).not.toHaveBeenCalled()
    expect(mocks.started).not.toHaveBeenCalled()
    gs.stop()
  })

  it('房间里已经放过附件就不再问资料库；卡片退场后也不再问服务端', async () => {
    const gs = setup({ roomHasAttachment: true, agentHasSpoken: true })
    await flush()
    expect(mocks.library).not.toHaveBeenCalled()
    expect(mocks.started).not.toHaveBeenCalled()
    gs.stop()
  })

  it('引导还在场时，卡片退场了也继续问仓库那一步——不然气泡会把「不知道」当成「还没做」', async () => {
    const gs = setup({ agentHasSpoken: true, roomHasAttachment: true, alsoProbe: true })
    await flush()
    expect(gs.visible.value).toBe(false)
    expect(mocks.started).toHaveBeenCalled()
    gs.stop()
  })

  it('「不再提示」按项目记：同一项目不再出现，换个项目照常出现', async () => {
    const gs = setup()
    await flush()
    gs.dismiss()
    expect(gs.visible.value).toBe(false)

    gs.projectId.value = 'p2'
    await flush()
    expect(gs.visible.value).toBe(true)
    gs.stop()
  })

  it('关掉的那个项目，下次进来也不再出现', async () => {
    const first = setup()
    await flush()
    first.dismiss()
    first.stop()

    const again = setup()
    await flush()
    expect(again.visible.value).toBe(false)
    again.stop()
  })
})
