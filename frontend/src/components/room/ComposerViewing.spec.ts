/**
 * 撰写框上那一行「芝士会看到：你在看 X」：右栏开着一份文件才出现，点一下就不带，
 * 这条消息已经指了一处时不出现。
 */
import { computed, ref } from 'vue'
import { createVuetify } from 'vuetify'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it } from 'vitest'

import ComposerViewing from './ComposerViewing.vue'

import { setLocale } from '@/i18n'
import { useShareViewing, VIEWING_CONTEXT } from '@/lib/viewingContext'

beforeEach(() => {
  setLocale('zh-CN')
  const { share } = useShareViewing()
  share.value = true
})
afterEach(cleanup)

function mount(path: string | null, suppressed = false) {
  const viewing = ref(path)
  return render(ComposerViewing, {
    props: { suppressed },
    global: {
      plugins: [createVuetify()],
      provide: {
        [VIEWING_CONTEXT as symbol]: computed(() =>
          viewing.value ? { kind: 'viewing' as const, path: viewing.value, task_id: null } : null
        ),
      },
    },
  })
}

it('右栏开着一份文件：写明芝士会看到哪一份；点一下就不带，再点带回来', async () => {
  const ui = mount('output/招募方案.docx')
  const chip = ui.getByTestId('composer-viewing')
  expect(chip.textContent).toContain('芝士会看到：你在看 招募方案.docx')
  expect(chip.getAttribute('aria-pressed')).toBe('true')
  await fireEvent.click(chip)
  expect(chip.textContent).toContain('不带你在看的文件')
  expect(chip.getAttribute('aria-pressed')).toBe('false')
  await fireEvent.click(chip)
  expect(chip.getAttribute('aria-pressed')).toBe('true')
})

it('右栏什么也没开、或这条消息已经指了一处：不出现', () => {
  expect(mount(null).queryByTestId('composer-viewing')).toBeNull()
  cleanup()
  expect(mount('output/a.md', true).queryByTestId('composer-viewing')).toBeNull()
})
