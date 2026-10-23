/** 界面切到英文时，输入框下面那一行按钮的提示不能再是中文。 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import ComposerActions from './ComposerActions.vue'

import { setLocale } from '@/i18n'

const CJK = /[㐀-䶿一-鿿豈-﫿]/

beforeEach(() => {
  setLocale('en')
  // 「+」的菜单和底部面板是 VOverlay：happy-dom 没有 ResizeObserver、visualViewport，不补上浮层挂不起来。
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  vi.stubGlobal('visualViewport', {
    width: 390,
    height: 844,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  setLocale('zh-CN')
})

it('reads in English, the + menu included', async () => {
  const { baseElement, getByTitle, findAllByRole } = render(ComposerActions, {
    props: {
      uploading: false,
      canSend: true,
      showImagePicker: true,
      enterSends: true,
      canVoice: true,
      canChecklist: true,
      canRemind: true,
    },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  getByTitle('Send')
  await fireEvent.click(getByTitle('More actions'))
  await findAllByRole('menuitem')
  const attrs = Array.from(baseElement.querySelectorAll('[title],[aria-label]')).flatMap((el) => [
    el.getAttribute('title') ?? '',
    el.getAttribute('aria-label') ?? '',
  ])
  expect([baseElement.textContent ?? '', ...attrs].filter((s) => CJK.test(s))).toEqual([])
})
