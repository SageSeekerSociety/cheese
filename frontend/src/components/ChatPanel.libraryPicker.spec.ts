// 输入栏里引用资料库里已有的一份文件——入口和 @ 一个人是同一个。
//
// 这解决的是「跨话题拿不到之前上传的文件」：那份预算表是上周在另一个房间传的，
// 而说「上周那份预算表」的人正在这个房间里。所以挑中它不是往正文里写一个名字，
// 是把它附到这条消息上：@ 连同打了一半的名字从正文里消失，文件进待发条。
//
// 菜单分两级。没打字的时候资料库只是一行入口：一个项目的文件会比房间里的人多得
// 多，平铺进来等于把「@ 一个人」这件最常做的事挤掉。打了字就不分级了，人、话题、
// 文件一起搜。
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
          { path: '现场照片.png', bytes: 8192, modified: 1756000000 },
        ],
        total: 3,
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

function row(container: Element, text: string): Element | undefined {
  return Array.from(container.querySelectorAll('.mention-menu-item')).find((b) => b.textContent?.includes(text))
}

describe('输入栏引用资料库里的文件', () => {
  it('刚打上 @ 的时候资料库是一行入口，文件不平铺进来', async () => {
    const c = composer()
    await flush()
    await typeAt(c, '@')

    const menu = c.querySelector('.mention-menu')!
    expect(menu.textContent).toContain('资料库')
    expect(menu.textContent).toContain('3 份文件')
    expect(menu.textContent).not.toContain('预算表.xlsx')
  })

  it('翻进资料库：文件和图片分开列，Esc 退回来', async () => {
    const c = composer()
    await flush()
    await typeAt(c, '@')
    await fireEvent.click(row(c, '资料库')!)
    await flush()

    const menu = c.querySelector('.mention-menu')!
    const groups = Array.from(menu.querySelectorAll('.mention-menu-group')).map((g) => g.textContent?.trim())
    expect(groups).toEqual(['文件', '图片'])
    expect(menu.textContent).toContain('预算表.xlsx')
    expect(menu.textContent).toContain('现场照片.png')
    // 图片排在文件后面——找资料的人多半在找文档。
    expect(menu.textContent!.indexOf('预算表.xlsx')).toBeLessThan(menu.textContent!.indexOf('现场照片.png'))

    await fireEvent.keyDown(c.querySelector('textarea')!, { key: 'Escape' })
    await flush()
    expect(c.querySelector('.mention-menu')!.textContent).not.toContain('预算表.xlsx')
  })

  it('打了字就是搜索：文件和人、话题一起出现', async () => {
    const c = composer()
    await flush()
    await typeAt(c, '@预算')

    const menu = c.querySelector('.mention-menu')!
    expect(menu.textContent).toContain('预算表.xlsx')
    // 打的是「预算」，另外两份不该出现。
    expect(menu.textContent).not.toContain('合同.docx')
    expect(menu.textContent).not.toContain('现场照片.png')
  })

  it('挑中一份文件：它附在这条消息上，正文里不留那个 @', async () => {
    const c = composer()
    await flush()
    const box = await typeAt(c, '看看 @预算')

    const item = row(c, '预算表.xlsx')
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
