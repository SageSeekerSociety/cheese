// 手机上传文件/照片这件事。
//
// 两条都不是样式偏好：
//  * 文件选择框藏起来的方式决定它还打不打得开。iOS Safari 拒绝用脚本打开一个
//    display:none 的 <input type=file>，按钮点下去毫无反应——这正是手机上「上传
//    那个键点不动」的样子。所以这里断言的是"它还在布局里"。
//  * 手机上没有截图可贴、也没有东西可拖，所以照片得有自己的入口。
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ChatPanel from './ChatPanel.vue'

import { setLocale } from '@/i18n'

const Panel = ChatPanel as unknown as Component

const topic = {
  id: 't1',
  project_id: 'p1',
  parent_id: null,
  title: 't',
  kind: 'topic',
  status: 'active',
  created_by: 'u',
  created_at: '2026-09-08T00:00:00Z',
  updated_at: '2026-09-08T00:00:00Z',
} as Topic

beforeEach(() => {
  vi.stubGlobal(
    'WebSocket',
    class {
      close() {}
      send() {}
      addEventListener() {}
      removeEventListener() {}
    }
  )
  vi.stubGlobal('fetch', async (url: string) => ({
    ok: true,
    status: 200,
    json: async () =>
      String(url).includes('/progress')
        ? { code: 200, data: { items: [], updated_at: null } }
        : { code: 200, data: { data: [], total: 0 } },
  }))
})

/** 手机宽度：Vuetify 的断点读的是 window.innerWidth。 */
function mount(width: number) {
  Object.defineProperty(window, 'innerWidth', { value: width, configurable: true })
  const { container } = render(Panel, {
    props: { topic, showComposer: true },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  return container
}

function fileInputs(c: Element): HTMLInputElement[] {
  return Array.from(c.querySelectorAll<HTMLInputElement>('.composer-actions input[type="file"]'))
}

// 默认语言是 en（happy-dom 的 navigator.language 是 en-US），而这份用例断言的是
// 中文界面的字。先把语言钉住，别让它跟着环境飘。
beforeEach(() => setLocale('zh-CN'))
describe('输入区的上传入口', () => {
  it('文件选择框藏起来但没有被 display:none —— 那样 iOS 上按钮就点不动了', () => {
    const inputs = fileInputs(mount(390))
    expect(inputs.length).toBeGreaterThan(0)
    for (const input of inputs) {
      expect(input.className).not.toContain('d-none')
      expect(getComputedStyle(input).display).not.toBe('none')
      expect(getComputedStyle(input).visibility).not.toBe('hidden')
    }
  })

  it('手机上照片有自己的入口，点它打开的是相册（accept=image/*）', async () => {
    const c = mount(390)
    const image = fileInputs(c).find((i) => i.accept === 'image/*')
    expect(image).toBeTruthy()
    const opened = vi.spyOn(image!, 'click')
    await fireEvent.click(c.querySelector('[title="发送照片"]')!)
    expect(opened).toHaveBeenCalled()
  })

  it('点回形针打开的是任意文件，不是相册', async () => {
    const c = mount(390)
    const any = fileInputs(c).find((i) => !i.accept)
    expect(any).toBeTruthy()
    const opened = vi.spyOn(any!, 'click')
    await fireEvent.click(c.querySelector('[title="上传文件（每个最大 10MB）"]')!)
    expect(opened).toHaveBeenCalled()
  })

  // 桌面上截图一贴、文件一拖就完事了，再摆一颗「照片」只是把这一行挤窄。
  it('桌面上不摆「照片」那一颗', () => {
    const c = mount(1440)
    expect(c.querySelector('[title="发送照片"]')).toBeNull()
  })
})
