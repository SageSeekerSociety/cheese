// 品牌位只放一种名字：中文界面是「知是」，其他语言是 cheese，切换语言时跟着换。
// 读屏软件读出的名字要和画出来的字标一致，所以这里按无障碍名称找图。
import { nextTick } from 'vue'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, describe, expect, it } from 'vitest'

import BrandLockup from './BrandLockup.vue'

import { setLocale } from '@/i18n'

afterEach(() => {
  cleanup()
  setLocale('zh-CN')
})

describe('BrandLockup', () => {
  it('shows 知是 on a Chinese page and cheese on an English one', async () => {
    setLocale('zh-CN')
    const view = render(BrandLockup)
    expect(view.getByRole('img', { name: '知是' })).toBeTruthy()
    expect(view.queryByRole('img', { name: 'cheese' })).toBeNull()

    setLocale('en')
    await nextTick()
    expect(view.getByRole('img', { name: 'cheese' })).toBeTruthy()
    expect(view.queryByRole('img', { name: '知是' })).toBeNull()
  })
})
