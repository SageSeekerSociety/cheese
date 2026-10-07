// 这台设备上接自定义模型服务，人说得出的两条：地址、密钥、模型名称没填齐（或地址不是网址）
// 就保存不了，不会把半截配置交给电脑；保存交出去的就是填的那三样。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen } from '@testing-library/vue'
import { beforeAll, describe, expect, it } from 'vitest'

import ClaudeCodeLogin from './ClaudeCodeLogin.vue'

import { setLocale, t } from '@/i18n'

beforeAll(() => setLocale('zh-CN'))

async function openForm() {
  const view = render(ClaudeCodeLogin, {
    props: { loggedIn: false, plan: null, state: 'idle', canUseModelService: true },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  await fireEvent.click(screen.getByRole('button', { name: t('account.thisDevice.modelServiceOpen') }))
  return view
}

async function fill(url: string, key: string, model: string) {
  await fireEvent.update(screen.getByLabelText(t('account.thisDevice.modelServiceUrl')), url)
  await fireEvent.update(screen.getByLabelText(t('account.thisDevice.modelServiceToken')), key)
  await fireEvent.update(screen.getByLabelText(t('account.thisDevice.modelServiceModel')), model)
}

describe('ClaudeCodeLogin model service', () => {
  it.each([
    ['open.bigmodel.cn/api/anthropic', 'key', 'glm-4.6'],
    ['https://open.bigmodel.cn/api/anthropic', '', 'glm-4.6'],
    ['https://open.bigmodel.cn/api/anthropic', 'key', ''],
  ])('does not save an incomplete service (%s, %s, %s)', async (url, key, model) => {
    const view = await openForm()
    await fill(url, key, model)
    await fireEvent.click(screen.getByRole('button', { name: t('account.thisDevice.modelServiceSave') }))
    expect(view.emitted('service')).toBeUndefined()
  })

  it('hands over the address, key and model that were filled in', async () => {
    const view = await openForm()
    await fill(' https://open.bigmodel.cn/api/anthropic ', 'the-key', 'glm-4.6')
    await fireEvent.click(screen.getByRole('button', { name: t('account.thisDevice.modelServiceSave') }))
    expect(view.emitted('service')).toEqual([
      [{ baseUrl: 'https://open.bigmodel.cn/api/anthropic', token: 'the-key', model: 'glm-4.6' }],
    ])
  })
})
