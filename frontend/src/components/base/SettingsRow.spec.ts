/** SettingsRow 收的是「标签在左、控件在右」这一种排法,和它那一列宽度该按容器还是按
 *  窗口算。宽度靠容器查询,happy-dom 量不到——所以那一条用源码断言钉(照仓库老办法,
 *  见 `components/admin/AdminGrid.spec.ts`),别的在这挂一遍。 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { cleanup, render } from '@testing-library/vue'
import { afterEach, describe, expect, it } from 'vitest'

import SettingsRow from './SettingsRow.vue'

afterEach(cleanup)

type RowProps = {
  label: string
  description?: string
  width?: 'text' | 'select-wide' | 'select' | 'code' | 'list' | 'none'
  for?: string
}

function mount(props: RowProps, slot = '控件') {
  return render(SettingsRow, { props, slots: { default: slot } })
}

describe('SettingsRow', () => {
  it('画出标签、说明和控件', () => {
    const view = mount({ label: '默认模型', description: '新任务用哪一个' })
    expect(view.getByText('默认模型')).toBeTruthy()
    expect(view.getByText('新任务用哪一个')).toBeTruthy()
    expect(view.getByText('控件')).toBeTruthy()
  })

  it('没有说明就不画说明那一行', () => {
    const view = mount({ label: '默认模型' })
    expect(view.container.querySelector('.base-settings-row__desc')).toBeNull()
  })

  it('给了 for 时标签是 <label for>,不给时只是一行字', () => {
    const withFor = mount({ label: '名字', for: 'row-name' })
    const label = withFor.container.querySelector('.base-settings-row__label') as HTMLElement
    expect(label.tagName).toBe('LABEL')
    expect(label.getAttribute('for')).toBe('row-name')

    const withoutFor = mount({ label: '名字' })
    const span = withoutFor.container.querySelector('.base-settings-row__label') as HTMLElement
    expect(span.tagName).toBe('SPAN')
    expect(span.hasAttribute('for')).toBe(false)
  })

  it('width 折成控件那一栏的类名,默认是 text', () => {
    const widths = ['text', 'select-wide', 'select', 'code', 'list', 'none'] as const
    for (const width of widths) {
      const view = mount({ label: '名字', width })
      const row = view.container.querySelector('.base-settings-row') as HTMLElement
      expect(row.classList.contains(`base-settings-row--${width}`)).toBe(true)
      cleanup()
    }
    const view = mount({ label: '名字' })
    expect(view.container.querySelector('.base-settings-row')!.classList.contains('base-settings-row--text')).toBe(true)
  })
})

describe('SettingsRow 的几何', () => {
  const source = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'SettingsRow.vue'), 'utf8')

  it('是一个容器查询的查询容器,并且自己撑满宽度', () => {
    // container-type 行内尺寸包含之后宽度推不出来,必须 width: 100%(AppPage 的注释)。
    expect(source).toContain('container-type: inline-size')
    expect(source).toMatch(/\.base-settings-row \{[\s\S]*?width: 100%/)
  })

  it('窄于 672px 的容器里标签换到控件上面', () => {
    expect(source).toContain('@container (width < 672px)')
  })
})
