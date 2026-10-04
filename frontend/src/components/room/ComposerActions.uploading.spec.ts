/** 附件还在上传时，那颗发送键说的不能只是「发送」——它此刻按不动，也没说为什么。
 *
 * 传的过程中名字换成一句说明，按钮同时是灰的：人不用猜为什么点了没反应。
 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { beforeEach, describe, expect, it } from 'vitest'

import ComposerActions from './ComposerActions.vue'

import { setLocale } from '@/i18n'

function mount(uploading: boolean, canSend = true) {
  return render(ComposerActions, {
    props: {
      uploading,
      canSend,
      showImagePicker: true,
      enterSends: true,
      alwaysSummon: false,
      summonOn: false,
      summonReady: true,
      agentName: '芝士',
    },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

function sendButton(container: Element) {
  return container.querySelector<HTMLButtonElement>('.composer-send')!
}

beforeEach(() => setLocale('zh-CN'))

describe('发送键：上传还没落地时', () => {
  it('说明在等什么，并且按不动', () => {
    const { container } = mount(true)
    const button = sendButton(container)
    expect(button.getAttribute('title')).toBe('附件上传中，传完再发')
    expect(button.disabled).toBe(true)
  })

  it('传完了就是平常那颗发送键', () => {
    const { container } = mount(false)
    const button = sendButton(container)
    expect(button.getAttribute('title')).toBe('发送')
    expect(button.disabled).toBe(false)
  })
})
