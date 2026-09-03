// 拖一个 PDF 进输入栏，过去是：没上传、没报错、没有任何提示——文件就是消失了。
// 只收图片这条限制本身不是 bug（后端的 raw 读取按扩展名白名单，是有意的），
// 不出声才是（issue #450：no silent degradation）。
import { describe, expect, it, vi } from 'vitest'

import { usePendingAttachments } from './attachments'

function file(name: string, type: string): File {
  return new File(['x'], name, { type })
}

describe('把文件交给输入栏', () => {
  it('不认识的类型会说出来，而不是静默丢掉', async () => {
    const onError = vi.fn()
    const { addFiles, pending } = usePendingAttachments(() => 't1', onError)

    await addFiles([file('paper.pdf', 'application/pdf')])

    expect(onError).toHaveBeenCalledTimes(1)
    expect(onError.mock.calls[0][0]).toContain('图片')
    expect(pending.value).toHaveLength(0)
  })

  it('图片和别的混在一起：图片照传，被挡下的那些也要说一声', async () => {
    const onError = vi.fn()
    const uploaded: string[] = []
    vi.stubGlobal('fetch', async () => ({
      ok: true,
      status: 200,
      json: async () => ({ code: 200, data: { path: 'uploads/img-1.png', mime: 'image/png' } }),
    }))
    const { addFiles, pending } = usePendingAttachments(() => 't1', onError)

    await addFiles([file('a.png', 'image/png'), file('b.csv', 'text/csv')])

    expect(onError).toHaveBeenCalledTimes(1)
    expect(pending.value.length + uploaded.length).toBeGreaterThan(0)
  })

  it('没有话题就什么都不做——附件是传进某个话题的工作区的', async () => {
    const onError = vi.fn()
    const { addFiles, pending } = usePendingAttachments(() => null, onError)
    await addFiles([file('paper.pdf', 'application/pdf')])
    expect(onError).not.toHaveBeenCalled()
    expect(pending.value).toHaveLength(0)
  })
})
