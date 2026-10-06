/**
 * 资料库里那一份是用户给进来的原件，只读。
 *
 * 修订仍然列出来——它们是这份文档的一部分，读者有权看见谁改了哪里；能做的只是不动
 * 它：接受一处修订会改写所有房间都在引用的那一份，而没有人要求过这件事。
 */
import { defineComponent, h } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { useDocumentRevisions } from '../../../composables/useDocumentRevisions'

import RevisionList from './RevisionList.vue'

import { setLocale } from '@/i18n'

// 断言读的是中文界面上的那一行字，语言钉在中文上。
beforeEach(() => setLocale('zh-CN'))

vi.mock('../../../api', () => ({
  documentRevisions: vi.fn(),
  decideDocumentRevisions: vi.fn(),
}))

const { documentRevisions } = await import('../../../api')

const vuetify = createVuetify({ components, directives })

afterEach(cleanup)

beforeEach(() => {
  vi.mocked(documentRevisions).mockResolvedValue({
    path: 'x',
    version: 'v7',
    revisions: [
      {
        number: 1,
        paragraph: 4,
        kind: 'replace',
        removed: '30 天',
        added: '60 天',
        author: '芝士',
        date: '2026-09-18T02:00:00Z',
      },
    ],
  })
})

// 这一只只画：清单由宿主取（产品里是 `components/work/PanelChangesHost.vue` 和
// `PanelPreviewHost.vue`），所以这里也搭一个最小的宿主，取数照旧走被 mock 掉的接口层。
function mount(path: string) {
  const Host = defineComponent({
    setup() {
      const revs = useDocumentRevisions({
        topicId: () => 'topic-A',
        path: () => path,
        version: () => 'v7',
        task: () => null,
        source: () => 'live',
      })
      return () => h(RevisionList, { revs, path })
    },
  })
  return render(Host, { global: { plugins: [vuetify] } })
}

function buttons(container: Element): string[] {
  return Array.from(container.querySelectorAll('button')).map((b) => b.textContent?.trim() ?? '')
}

it('资料库里那份文档的修订看得见，但按不动', async () => {
  const { container } = mount('library/合同.docx')

  await waitFor(() => expect(container.textContent).toContain('把「30 天」改成「60 天」'))
  expect(buttons(container)).toEqual([])
  expect(container.textContent).toContain('资料库里的原件不改')
})

it('房间自己那份照旧逐条处理', async () => {
  const { container } = mount('output/合同.docx')

  await waitFor(() => expect(container.textContent).toContain('把「30 天」改成「60 天」'))
  expect(buttons(container)).toContain('接受')
  expect(buttons(container)).toContain('拒绝')
  expect(container.textContent).not.toContain('资料库里的原件不改')
})
