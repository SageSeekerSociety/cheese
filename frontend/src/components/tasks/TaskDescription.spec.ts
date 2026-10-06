// @vitest-environment jsdom
//
// A task imported from a PDF keeps its description as the Markdown the import
// wrote. The person who opens it reads it formatted, with its pictures, and a
// video link on its own line is a player, the same as in a description written
// in the editor.
import { createApp, h } from 'vue'
import { waitFor } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import TaskDescription from './TaskDescription.vue'

import i18n from '@/i18n'

async function rendered(source: string, check: (host: HTMLElement) => void) {
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp({ render: () => h(TaskDescription, { source, empty: 'empty' }) })
  app.use(i18n)
  app.mount(host)
  try {
    await waitFor(() => check(host))
  } finally {
    app.unmount()
    host.remove()
  }
}

describe('a description imported from a PDF', () => {
  it('reads as formatted text, with its pictures and its video', async () => {
    const source = [
      '## 要求',
      '',
      '- 提交代码仓库链接',
      '',
      '![网络结构](https://files.test/net.png)',
      '',
      'https://www.bilibili.com/video/BV1xx411c7mD',
    ].join('\n')

    await rendered(source, (host) => {
      expect(host.querySelector('h2')?.textContent).toBe('要求')
      expect(host.querySelector('li')?.textContent).toBe('提交代码仓库链接')
      expect(host.querySelector('img')?.getAttribute('src')).toBe('https://files.test/net.png')
      expect(host.querySelector('iframe')?.getAttribute('src')).toContain('bvid=BV1xx411c7mD')
      expect(host.textContent).not.toContain('##')
    })
  })
})
