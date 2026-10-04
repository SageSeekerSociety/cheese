/** 房间当前预览那根指针的缓存。
 *
 * 它存在的理由只有一个：冷开一个房间时，那份 `GET /preview` 不该等到面板挂上来才
 * 发得出。路由守卫先起头，面板挂上时先读这份——所以这里钉的是「守卫和面板要的是
 * 同一样东西（只发一条）」「取过就记得」「失败不算数」「没取过和没有是两回事」。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getPreview = vi.fn()
vi.mock('@/api', () => ({ getPreview: (...a: unknown[]) => getPreview(...a) }))

import {
  cachedPreviewPointer,
  refreshPreviewPointer,
  resetPreviewPointerCache,
  setPreviewPointer,
  warmPreviewPointer,
} from './previewPointer'

function deferred<T>() {
  let settle!: (v: T) => void
  let fail!: (e: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    settle = res
    fail = rej
  })
  // 没人接的那条分支不该在测试里炸成 unhandled rejection。
  promise.catch(() => {})
  return { promise, settle, fail }
}

beforeEach(() => {
  vi.clearAllMocks()
  resetPreviewPointerCache()
})

describe('房间当前预览的指针缓存', () => {
  it('没取过是 undefined，取过而没有是 null——两者不是一回事', async () => {
    expect(cachedPreviewPointer('t1')).toBeUndefined()
    getPreview.mockResolvedValue(null)
    await refreshPreviewPointer('t1')
    expect(cachedPreviewPointer('t1')).toBeNull()
    // 取过之后还是 null，而不是又变回「没取过」。
    expect(cachedPreviewPointer('t1')).toBeNull()
  })

  it('取回来的那一份留着：同一次会话里再读就不必再问', async () => {
    getPreview.mockResolvedValue({ path: 'a.html', artifact_id: 'a1' })
    await refreshPreviewPointer('t1')
    expect(cachedPreviewPointer('t1')).toEqual({ path: 'a.html', artifact_id: 'a1' })
  })

  it('同一个话题同时只发一条——守卫和面板要的是同一样东西', async () => {
    const gate = deferred<{ path: string; artifact_id: string }>()
    getPreview.mockReturnValue(gate.promise)

    const first = refreshPreviewPointer('t1')
    const second = refreshPreviewPointer('t1')
    expect(getPreview).toHaveBeenCalledTimes(1)

    gate.settle({ path: 'a.html', artifact_id: 'a1' })
    await expect(first).resolves.toEqual({ path: 'a.html', artifact_id: 'a1' })
    await expect(second).resolves.toEqual({ path: 'a.html', artifact_id: 'a1' })
    expect(getPreview).toHaveBeenCalledTimes(1)

    // 落定之后再问才重新发一条。
    getPreview.mockResolvedValue({ path: 'a.html', artifact_id: 'a2' })
    await refreshPreviewPointer('t1')
    expect(getPreview).toHaveBeenCalledTimes(2)
  })

  it('没问成不写缓存、也不吞掉——调用方要分得出「没问成」和「没有预览」', async () => {
    getPreview.mockRejectedValue(new Error('boom'))
    await expect(refreshPreviewPointer('t1')).rejects.toThrow('boom')
    expect(cachedPreviewPointer('t1')).toBeUndefined()
  })

  it('面板自己取回来的那一份也能写回来', () => {
    setPreviewPointer('t1', { path: 'b.html', artifact_id: 'b1' })
    expect(cachedPreviewPointer('t1')).toEqual({ path: 'b.html', artifact_id: 'b1' })
  })

  // 首屏那一步要的是「已经在手边的答案」，不是「最新」：取过的直接给，正在飞的复用，
  // 都没有就什么都不发。没有这一条，面板挂上来还是自己发一条 /preview——那正是守卫先
  // 起头想省掉的那一轮网络。
  it('首屏拿答案：取过的直接给、正在飞的复用，手边没有就不发', async () => {
    setPreviewPointer('t1', { path: 'a.html', artifact_id: 'a1' })
    await expect(warmPreviewPointer('t1')).resolves.toEqual({ path: 'a.html', artifact_id: 'a1' })
    expect(getPreview).not.toHaveBeenCalled()

    // 守卫刚起头、还没落定：面板这一问复用同一条，不必自己再发。
    const gate = deferred<{ path: string; artifact_id: string }>()
    getPreview.mockReturnValue(gate.promise)
    const started = refreshPreviewPointer('t2')
    const warm = warmPreviewPointer('t2')!
    expect(getPreview).toHaveBeenCalledTimes(1)
    gate.settle({ path: 'b.html', artifact_id: 'b1' })
    await expect(warm).resolves.toEqual({ path: 'b.html', artifact_id: 'b1' })
    await expect(started).resolves.toEqual({ path: 'b.html', artifact_id: 'b1' })
    expect(getPreview).toHaveBeenCalledTimes(1)

    // 手边什么都没有：不发（由调用方自己决定现问），也不把这次读当成「记下来」。
    expect(warmPreviewPointer('t3')).toBeUndefined()
    expect(getPreview).toHaveBeenCalledTimes(1)
    expect(cachedPreviewPointer('t3')).toBeUndefined()
  })
})
