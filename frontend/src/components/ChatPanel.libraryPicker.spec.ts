// 输入栏里引用资料库里已有的一份文件——形式和 @ 一个人相同。
//
// 这解决的是「跨话题拿不到之前上传的文件」：那份预算表是上周在另一个房间传的，
// 而说「上周那份预算表」的人正在这个房间里。所以挑中它不是往正文里写一个名字，
// 是把它附到这条消息上：@ 连同打了一半的名字从正文里消失，文件进待发条。
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ChatPanel from './ChatPanel.vue'

const Panel = ChatPanel as unknown as Component

const topic = {
  id: 't1',
  project_id: 'p1',
  parent_id: null,
  title: 't',
  kind: 'topic',
  status: 'active',
  created_by: 'u',
  created_at: '2026-08-18T00:00:00Z',
  updated_at: '2026-08-18T00:00:00Z',
} as Topic

const calls: Array<{ url: string; body: FormData | null }> = []

beforeEach(() => {
  calls.length = 0
  vi.stubGlobal(
    'WebSocket',
    class {
      close() {}
      send() {}
      addEventListener() {}
      removeEventListener() {}
    }
  )
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const href = String(url)
    calls.push({ url: href, body: (init?.body as FormData) ?? null })
    let data: unknown = { data: [], total: 0 }
    if (href.includes('/library')) {
      data = {
        data: [
          { path: '预算表.xlsx', bytes: 2048, modified: 1758000000 },
          { path: '合同.docx', bytes: 4096, modified: 1757000000 },
        ],
        total: 2,
      }
    } else if (href.includes('/attachments')) {
      data = {
        path: 'uploads/预算表.xlsx',
        mime: 'application/octet-stream',
        bytes: 2048,
        library_path: '预算表.xlsx',
      }
    } else if (href.includes('/progress')) {
      data = { items: [], updated_at: null }
    }
    return { ok: true, status: 200, json: async () => ({ code: 200, data }) }
  })
})

async function flush() {
  for (let i = 0; i < 10; i += 1) await new Promise((r) => setTimeout(r, 0))
}

function composer() {
  const { container } = render(Panel, {
    props: { topic, showComposer: true },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  return container
}

async function typeAt(container: Element, text: string) {
  const box = container.querySelector('textarea')!
  await fireEvent.update(box, text)
  await flush()
  return box as HTMLTextAreaElement
}

describe('输入栏引用资料库里的文件', () => {
  it('打一个 @ 之后，资料库里的文件也在候选里', async () => {
    const c = composer()
    await flush()
    await typeAt(c, '@预算')

    const menu = c.querySelector('.mention-menu')!
    expect(menu).toBeTruthy()
    expect(menu.textContent).toContain('预算表.xlsx')
    expect(menu.textContent).toContain('资料库')
    // 打的是「预算」，另一份不该出现在候选里。
    expect(menu.textContent).not.toContain('合同.docx')
  })

  it('挑中一份文件：它附在这条消息上，正文里不留那个 @', async () => {
    const c = composer()
    await flush()
    const box = await typeAt(c, '看看 @预算')

    const item = Array.from(c.querySelectorAll('.mention-menu-item')).find((b) =>
      b.textContent?.includes('预算表.xlsx')
    )
    expect(item, '候选里没有那份文件').toBeTruthy()
    await fireEvent.click(item!)
    await flush()

    // 引用一份已有的文件，走的是同一个附件端点，带着它在资料库里的名字。
    const attach = calls.filter((x) => x.url.includes('/attachments') && x.body)
    expect(attach.length).toBe(1)
    expect(attach[0].body?.get('library_path')).toBe('预算表.xlsx')

    // 正文只剩人写的那句话，文件在待发条里。
    expect(box.value).toBe('看看 ')
    expect(c.querySelector('.att-strip')?.textContent).toContain('预算表.xlsx')
  })
})
