/** 文件格里一张仓库图片：`<img>` 挂的必须是带 Authorization 取回来的 object URL ——
 *  直接挂 `/file/raw` 是匿名请求，服务器答 404，读者看到一张裂图。取不到时说清楚，给
 *  下载，而不是留一块空白。 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import i18n, { setLocale, t } from '@/i18n'

vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  readFile: vi.fn().mockResolvedValue({
    path: 'docs/arch.png',
    content: null,
    version: 'v1',
    bytes: 2048,
    binary: true,
    too_large: false,
  }),
}))

import ProjectFileTab from './ProjectFileTab.vue'

let serves = true

beforeEach(() => {
  setLocale('zh-CN')
  localStorage.setItem('accessToken', 'tok')
  vi.stubGlobal(
    'fetch',
    vi.fn(async (_url: string, init?: RequestInit) =>
      serves && (init?.headers as Record<string, string>).Authorization === 'Bearer tok'
        ? new Response(new Blob(['png']), { status: 200 })
        : new Response('{}', { status: 404 })
    )
  )
  vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:arch')
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
})
afterEach(() => {
  serves = true
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  localStorage.clear()
})

function mount() {
  return render(ProjectFileTab, {
    props: { projectId: 'p1', channelId: 'c1', path: 'docs/arch.png', lines: null },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

describe('a repo image in the file tab', () => {
  it('is drawn from the fetched object URL', async () => {
    const view = mount()
    await vi.waitFor(() => expect(view.container.querySelector('img')?.getAttribute('src')).toBe('blob:arch'))
  })

  it('says it cannot be shown, with a download, when the image will not come', async () => {
    serves = false
    const view = mount()
    await view.findByText(t('work.room.changes.cantDisplay'))
    expect(view.container.querySelector('img')).toBeNull()
    expect(view.getByText(t('work.room.changes.downloadOriginal'))).toBeTruthy()
  })
})
