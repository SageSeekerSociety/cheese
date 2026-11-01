/**
 * 网页、SVG、图片顶上那一条：类型、三段、第几版，以及版本行没出来时历史还有入口。
 */
import { computed, ref } from 'vue'
import { createVuetify } from 'vuetify'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, expect, it } from 'vitest'

import { PREVIEW_MODE, usePreviewMode } from '@/composables/panelPreview/previewMode'

import PreviewFileBar from './PreviewFileBar.vue'

import { setLocale } from '@/i18n'

setLocale('zh-CN')
afterEach(cleanup)

const entry = {
  id: 'r2',
  seq: 2,
  author: '芝士',
  at: '2026-10-30T10:00:00Z',
  note: '',
  current: true,
  latest: true,
  lastSeen: false,
}

function mount(current: typeof entry | null) {
  const versions = { list: computed(() => (current ? [current] : [])), current: ref(current) }
  return render(PreviewFileBar, {
    props: {
      icon: 'mdi-file-image-outline',
      label: '图片',
      versions: versions as never,
      showHistory: false,
      fileHistory: {} as never,
      topicId: 't1',
    },
    global: {
      plugins: [createVuetify()],
      stubs: { VIcon: true },
      provide: {
        [PREVIEW_MODE as symbol]: usePreviewMode(
          () => false,
          () => 'a.png'
        ),
      },
    },
  })
}

it('摆出类型、「看 / 批注」和这一版；不能改的图上不摆「改」', () => {
  const ui = mount(entry)
  expect(ui.container.textContent).toContain('图片')
  expect(ui.getByTestId('version-picker').textContent).toContain('第 2 版')
  expect(ui.getByTestId('preview-mode-view')).toBeTruthy()
  expect(ui.getByTestId('preview-mode-note')).toBeTruthy()
  expect(ui.queryByTestId('preview-mode-edit')).toBeNull()
  expect(ui.queryByTestId('file-bar-history')).toBeNull()
})

it('版本行没出来时单摆一颗「历史」，历史不会因为清单没取到就没了入口', async () => {
  const ui = mount(null)
  expect(ui.queryByTestId('version-picker')).toBeNull()
  await fireEvent.click(ui.getByTestId('file-bar-history'))
  expect(ui.emitted()['toggle-history']).toHaveLength(1)
})
