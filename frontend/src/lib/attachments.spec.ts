import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { pastedImageName, pastedTextName, uploaded, usePendingAttachments } from './attachments'

import { setLocale } from '@/i18n'

function file(name: string, type: string): File {
  return new File(['x'], name, { type })
}

/** The upload API talks XHR — the only transport that reports upload progress —
 *  so the tests drive a fake one instead of `fetch`. `handleUpload` decides what
 *  each `send()` does; the default echoes the file back as a finished
 *  attachment, and `sent` records the request bodies the way the old
 *  `fetch.mock.calls` did. */
class FakeXhr {
  method = ''
  url = ''
  status = 0
  responseText = ''
  upload = new EventTarget()
  headers: Record<string, string> = {}
  private listeners: Record<string, ((e?: unknown) => void)[]> = {}

  open(method: string, url: string) {
    this.method = method
    this.url = url
  }
  setRequestHeader(key: string, value: string) {
    this.headers[key] = value
  }
  addEventListener(type: string, cb: (e?: unknown) => void) {
    ;(this.listeners[type] ??= []).push(cb)
  }
  send(form: FormData) {
    handleUpload(form, this)
  }
  /** One upload-progress event, the way the browser fires them. */
  progress(loaded: number, total: number) {
    this.upload.dispatchEvent(Object.assign(new Event('progress'), { lengthComputable: true, loaded, total }))
  }
  respond(status: number, body: unknown) {
    this.status = status
    this.responseText = typeof body === 'string' ? body : JSON.stringify(body)
    for (const cb of this.listeners.load ?? []) cb()
  }
  fail() {
    this.status = 0
    for (const cb of this.listeners.error ?? []) cb()
  }
}

let handleUpload: (form: FormData, xhr: FakeXhr) => void
const sent: FormData[] = []

/** Read the picked file out of the form and finish the upload. */
function echoUpload(form: FormData, xhr: FakeXhr) {
  sent.push(form)
  const picked = form.get('file') as File
  xhr.progress(picked.size, picked.size)
  xhr.respond(200, { code: 200, data: { path: `uploads/id/${picked.name}`, mime: picked.type } })
}

function pasteImage(name = 'image.png', type = 'image/png'): ClipboardEvent {
  return {
    clipboardData: { items: [{ kind: 'file', getAsFile: () => file(name, type) }] },
    preventDefault: () => {},
  } as unknown as ClipboardEvent
}

beforeEach(() => {
  // These assertions read the Chinese copy.
  setLocale('zh-CN')
  sent.length = 0
  handleUpload = echoUpload
  vi.stubGlobal('XMLHttpRequest', FakeXhr)
})
afterEach(() => vi.unstubAllGlobals())

describe('chat attachments', () => {
  it('does not attach an upload to a different topic after navigation', async () => {
    let topic = 'first'
    let xhr!: FakeXhr
    handleUpload = (_form, x) => {
      xhr = x
    }
    const { addFiles, pending } = usePendingAttachments(() => topic)
    const uploading = addFiles([file('paper.pdf', 'application/pdf')])
    topic = 'second'
    xhr.respond(200, { code: 200, data: { path: 'uploads/paper.pdf', mime: 'application/pdf' } })
    await uploading
    expect(pending.value).toHaveLength(0)
  })

  it('holds the file a place in the strip while it is still going up', async () => {
    let xhr!: FakeXhr
    handleUpload = (_form, x) => {
      xhr = x
    }
    const { addFiles, pending } = usePendingAttachments(() => 't1')
    const done = addFiles([file('paper.pdf', 'application/pdf')])

    expect(pending.value).toHaveLength(1)
    expect(pending.value[0]).toMatchObject({ uploading: true, name: 'paper.pdf', mime: 'application/pdf' })
    // 占位那一格的 path 不在工作区里，发不出去。
    expect(uploaded(pending.value)).toEqual([])

    xhr.respond(200, { code: 200, data: { path: 'uploads/id/paper.pdf', mime: 'application/pdf' } })
    await done
    expect(pending.value).toHaveLength(1)
    expect(uploaded(pending.value)).toEqual([{ path: 'uploads/id/paper.pdf', mime: 'application/pdf' }])
  })

  // 上传不再只是一转到底的圈：XHR 报的进度喂进这一格，条上画的是确定的百分比。
  it('tracks determinate upload progress on the slot', async () => {
    let xhr!: FakeXhr
    handleUpload = (_form, x) => {
      xhr = x
    }
    const { addFiles, pending } = usePendingAttachments(() => 't1')
    const done = addFiles([file('big.bin', 'application/octet-stream')])

    expect(pending.value[0]).toMatchObject({ uploading: true, progress: 0 })

    xhr.progress(3, 10)
    expect(pending.value[0].progress).toBeCloseTo(0.3)
    xhr.progress(10, 10)
    expect(pending.value[0].progress).toBe(1)

    xhr.respond(200, { code: 200, data: { path: 'uploads/id/big.bin', mime: 'application/octet-stream' } })
    await done
    // 传完那一格换成真的附件，进度不再挂着。
    expect(pending.value).toEqual([{ path: 'uploads/id/big.bin', mime: 'application/octet-stream' }])
  })

  it('drops an upload removed from the strip while it was going up', async () => {
    let xhr!: FakeXhr
    handleUpload = (_form, x) => {
      xhr = x
    }
    const { addFiles, pending, removeAt } = usePendingAttachments(() => 't1')
    const done = addFiles([file('paper.pdf', 'application/pdf')])
    removeAt(0)
    xhr.respond(200, { code: 200, data: { path: 'uploads/id/paper.pdf', mime: 'application/pdf' } })
    await done
    expect(pending.value).toHaveLength(0)
  })

  it('uploads a PDF with its filename', async () => {
    const onError = vi.fn()
    const { addFiles, pending } = usePendingAttachments(() => 't1', onError)

    await addFiles([file('paper.pdf', 'application/pdf')])

    expect(onError).not.toHaveBeenCalled()
    expect(pending.value).toEqual([{ path: 'uploads/id/paper.pdf', mime: 'application/pdf' }])
  })

  it('uploads images and documents together', async () => {
    const onError = vi.fn()
    const { addFiles, pending } = usePendingAttachments(() => 't1', onError)

    await addFiles([file('a.png', 'image/png'), file('b.csv', 'text/csv')])

    expect(onError).not.toHaveBeenCalled()
    expect(pending.value.map((a) => a.path)).toEqual(['uploads/id/a.png', 'uploads/id/b.csv'])
  })

  it('does not upload without a topic', async () => {
    const onError = vi.fn()
    const { addFiles, pending } = usePendingAttachments(() => null, onError)
    await addFiles([file('paper.pdf', 'application/pdf')])
    expect(onError).not.toHaveBeenCalled()
    expect(pending.value).toHaveLength(0)
  })

  it('reports the count limit', async () => {
    const onError = vi.fn()
    const { addFiles, pending } = usePendingAttachments(() => 't1', onError)
    await addFiles(Array.from({ length: 10 }, (_, i) => file(`${i}.txt`, 'text/plain')))
    expect(pending.value).toHaveLength(9)
    expect(onError).toHaveBeenCalledWith('每条消息最多添加 9 个附件')
  })

  // 贴进来的那一份不进资料库：资料库按名字寻址，而剪贴板里的截图没有名字——
  // 浏览器编的那个（`image.png`）换成带时间的。这一格证明「来路」跟着请求走、
  // 名字也换了；挑进来的一份用原名，也不改名。
  it('says a pasted file came off the clipboard, and a picked one did not', async () => {
    const { addFiles, onPaste } = usePendingAttachments(() => 't1')
    onPaste(pasteImage())
    await new Promise((r) => setTimeout(r, 0))
    await addFiles([file('预算表.xlsx', 'application/octet-stream')])

    const origins = sent.map((form) => form.get('origin'))
    expect(origins).toEqual(['clipboard', 'file'])
    expect((sent[0].get('file') as File).name).toMatch(/^粘贴的图片-\d{8}-\d{6}\.png$/)
    expect((sent[1].get('file') as File).name).toBe('预算表.xlsx')
  })

  // 有真名字的图不动它：文件管理器里拖出来的一张图，名字是它的。
  it('keeps a pasted image that came with a real name', async () => {
    const { onPaste } = usePendingAttachments(() => 't1')
    onPaste(pasteImage('screenshot-2026.png', 'image/png'))
    await new Promise((r) => setTimeout(r, 0))
    expect((sent[0].get('file') as File).name).toBe('screenshot-2026.png')
  })

  it('reports an oversized file and uploads the next one', async () => {
    const onError = vi.fn()
    const large = file('large.pdf', 'application/pdf')
    Object.defineProperty(large, 'size', { value: 10 * 1024 * 1024 + 1 })
    const { addFiles, pending } = usePendingAttachments(() => 't1', onError)
    await addFiles([large, file('small.pdf', 'application/pdf')])
    expect(onError).toHaveBeenCalledWith('large.pdf 超过 10MB，无法上传')
    expect(pending.value).toHaveLength(1)
    expect(pending.value[0].path).toContain('small.pdf')
  })

  // 失败的那一枚留在待发条里：它带着名字和 File，重试就是拿着同一份再传一次。
  // 原来它被删掉、错误只在一条会自己走掉的提示里，人回头看时既不知道少了哪个
  // 文件，也没有再试一次的路。
  it('keeps a failed upload in the strip, with its File, and retry sends it again', async () => {
    let attempts = 0
    handleUpload = (form, xhr) => {
      attempts += 1
      if (attempts === 1) {
        xhr.fail()
        return
      }
      const picked = form.get('file') as File
      xhr.respond(200, { code: 200, data: { path: `uploads/id/${picked.name}`, mime: picked.type } })
    }
    const { addFiles, pending, retry } = usePendingAttachments(() => 't1')
    await addFiles([file('paper.pdf', 'application/pdf')])

    expect(pending.value).toHaveLength(1)
    expect(pending.value[0]).toMatchObject({ error: true, uploading: false, name: 'paper.pdf' })
    expect(pending.value[0].file?.name).toBe('paper.pdf')
    // 没传上去的那一枚不能进这条消息。
    expect(uploaded(pending.value)).toEqual([])

    await retry(0)

    expect(attempts).toBe(2)
    expect(pending.value).toHaveLength(1)
    expect(pending.value[0].error).toBeFalsy()
    expect(uploaded(pending.value)).toEqual([{ path: 'uploads/id/paper.pdf', mime: 'application/pdf' }])
  })

  it('a failed upload is removed like any other, and retry does nothing after that', async () => {
    handleUpload = (_form, xhr) => xhr.fail()
    const { addFiles, pending, removeAt, retry } = usePendingAttachments(() => 't1')
    await addFiles([file('paper.pdf', 'application/pdf')])
    expect(pending.value).toHaveLength(1)

    removeAt(0)
    expect(pending.value).toHaveLength(0)

    await retry(0)
    expect(pending.value).toHaveLength(0)
  })
})

describe('pasted attachment names', () => {
  it('names a pasted screenshot after what it is and when', () => {
    const at = new Date(2026, 9, 4, 15, 30, 12)
    expect(pastedImageName(file('image.png', 'image/png'), at)).toBe('粘贴的图片-20261004-153012.png')
    // 扩展名跟着 mime，不跟着那个编出来的名字。
    expect(pastedImageName(file('image.png', 'image/jpeg'), at)).toBe('粘贴的图片-20261004-153012.jpg')
  })

  it('names a long pasted text turned into a file', () => {
    const at = new Date(2026, 9, 4, 15, 30, 12)
    expect(pastedTextName('txt', at)).toBe('粘贴的文字-20261004-153012.txt')
    expect(pastedTextName('md', at)).toBe('粘贴的文字-20261004-153012.md')
  })
})
