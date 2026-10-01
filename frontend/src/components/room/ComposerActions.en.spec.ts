/** 界面切到英文时，输入框下面那一行按钮的提示不能再是中文。 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it } from 'vitest'

import ComposerActions from './ComposerActions.vue'

import { setLocale } from '@/i18n'

const CJK = /[㐀-䶿一-鿿豈-﫿]/

beforeEach(() => setLocale('en'))
afterEach(() => {
  cleanup()
  setLocale('zh-CN')
})

it('reads in English, photo button included', () => {
  const { baseElement, getByTitle } = render(ComposerActions, {
    props: {
      uploading: false,
      canSend: true,
      showImagePicker: true,
      enterSends: true,
      alwaysSummon: false,
      summonOn: false,
      summonReady: true,
      agentName: 'Cheese',
    },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  getByTitle('Upload files (up to 10 MB each)')
  getByTitle('Send photos')
  getByTitle('Send')
  const attrs = Array.from(baseElement.querySelectorAll('[title],[aria-label]')).flatMap((el) => [
    el.getAttribute('title') ?? '',
    el.getAttribute('aria-label') ?? '',
  ])
  expect([baseElement.textContent ?? '', ...attrs].filter((s) => CJK.test(s))).toEqual([])
})
