// 下拉框在读屏里叫它自己的标签，不叫「打开」。
//
// Vuetify 3.9 在每个 v-select 的输入框上挂 `aria-label`，值是它自带的开合提示
// （中文界面下是「打开」），盖掉了字段的标签：整个应用的下拉框在读屏里都只念「打开」，
// 分不出哪个是哪个。这里用产品的真实配置（中文 locale），挂一个带标签的下拉，按标签找它。
import { h } from 'vue'
import { VSelect } from 'vuetify/components'
import { cleanup, render, screen } from '@testing-library/vue'
import { afterEach, expect, it } from 'vitest'

import vuetify from './vuetify'

afterEach(cleanup)

it('带标签的下拉在读屏里叫它的标签，不叫「打开」', () => {
  render(
    { render: () => h(VSelect, { label: '分类', items: ['课程作业', '竞赛'] }) },
    { global: { plugins: [vuetify] } }
  )

  expect(screen.getByRole('combobox', { name: '分类' })).toBeTruthy()
  expect(screen.queryByRole('combobox', { name: '打开' })).toBeNull()
})
