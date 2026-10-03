// 资料库清单的取数（#944）。三件事值得钉，都是页面上会看出来的：
//   1. 换了块板，先发的那次响应晚到时不许落下来 —— 落下来的会是别人的清单；
//   2. 读不出来不许把清单清成空 —— 选择器拿「清单里没有」判失效，一次网络失败会让人
//      删掉有效的引用；
//   3. 还没有板可问时维持「在读」，不替一块还不认识的板下结论说「一份都没有」。
import { nextTick, ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const listMaterials = vi.fn()

vi.mock('@/network/api/spaces', () => ({
  SpacesApi: { listMaterials: (...args: unknown[]) => listMaterials(...args) },
}))

import { useSpaceMaterials } from '../useSpaceMaterials'

function row(id: number) {
  return {
    id,
    name: `f${id}`,
    visibility: 'members',
    type: 'file',
    size: null,
    mime: null,
    uploaderId: null,
    createdAt: 0,
    downloadCount: 0,
  }
}

/** 一个能自己决定什么时候落地的响应。 */
function deferred<T>() {
  let settle!: (value: T) => void
  let fail!: (reason: unknown) => void
  const promise = new Promise<T>((resolve, reject) => {
    settle = resolve
    fail = reject
  })
  return { promise, settle, fail }
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

beforeEach(() => {
  listMaterials.mockReset()
})

describe('useSpaceMaterials', () => {
  it('读回来的清单进 materials，状态是 ready', async () => {
    listMaterials.mockResolvedValue({ data: { materials: [row(161)], canManage: true } })
    const spaceId = ref<number | undefined>(7)

    const { materials, state } = useSpaceMaterials(spaceId)
    await flush()

    expect(listMaterials).toHaveBeenCalledWith(7)
    expect(materials.value.map((item) => item.id)).toEqual([161])
    expect(state.value).toBe('ready')
  })

  it('还没有板可问时维持 loading，不说「一份都没有」', async () => {
    const spaceId = ref<number | undefined>(undefined)

    const { materials, state } = useSpaceMaterials(spaceId)
    await flush()

    expect(listMaterials).not.toHaveBeenCalled()
    expect(materials.value).toEqual([])
    expect(state.value).toBe('loading')
  })

  it('换了块板：先发的那次响应晚到，不许覆盖新板的清单', async () => {
    const first = deferred<{ data: { materials: unknown[] } }>()
    const second = deferred<{ data: { materials: unknown[] } }>()
    listMaterials.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)

    const spaceId = ref<number | undefined>(7)
    const { materials, state } = useSpaceMaterials(spaceId)

    spaceId.value = 8
    await nextTick()

    // 新板先回来，旧板后回来 —— 旧的那份必须是废的。
    second.settle({ data: { materials: [row(200)] } })
    await flush()
    first.settle({ data: { materials: [row(100)] } })
    await flush()

    expect(materials.value.map((item) => item.id)).toEqual([200])
    expect(state.value).toBe('ready')
  })

  it('读不出来：状态写成 error，已经拿到的清单原样留着', async () => {
    listMaterials.mockResolvedValueOnce({ data: { materials: [row(161)] } })
    const spaceId = ref<number | undefined>(7)
    const { materials, state } = useSpaceMaterials(spaceId)
    await flush()

    listMaterials.mockRejectedValueOnce(new Error('boom'))
    spaceId.value = 8
    await nextTick()
    await flush()

    expect(state.value).toBe('error')
    // 留着上一份，是给「别把它当成空清单」留出余地：调用方按 state 决定摆什么。
    expect(materials.value.map((item) => item.id)).toEqual([161])
  })
})
