// 「导出项目」那一块。它是哑的：只从 props 画状态、只往外 emit `export`（发请求与
// 状态在 composables/useProjectExport，另有一份 spec）。这里钉四件事：
//   1. 画出这一块的标题、说明和那颗按钮；
//   2. 点下去把 `export` 抛给页面；
//   3. busy 时按钮禁用（打包期间不许连点）；
//   4. 有失败话术就画出来，没有就不画。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import ProjectExportSection from './ProjectExportSection.vue'

import { setLocale } from '@/i18n'

function mountSection(props: { busy?: boolean; error?: string; onExport?: () => void } = {}) {
  return render(ProjectExportSection, {
    props: { busy: props.busy ?? false, error: props.error ?? '', onExport: props.onExport },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

const exportButton = () => screen.getByRole('button', { name: '导出项目' }) as HTMLButtonElement

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
})

beforeEach(() => {
  setLocale('zh-CN')
})

afterEach(cleanup)

describe('「导出项目」那一块', () => {
  it('画出标题、说明和按钮', () => {
    const { container } = mountSection()
    expect(container.textContent).toContain('导出项目')
    expect(container.textContent).toContain('打包整个项目')
    expect(exportButton()).toBeTruthy()
  })

  it('点下去把 export 抛给页面', async () => {
    const onExport = vi.fn()
    mountSection({ onExport })

    await fireEvent.click(exportButton())

    expect(onExport).toHaveBeenCalledOnce()
  })

  it('busy 时按钮禁用', () => {
    mountSection({ busy: true })
    expect(exportButton().disabled).toBe(true)
  })

  it('有失败话术就画出来，没有就不画', () => {
    const withError = mountSection({ error: '导出失败：网络' })
    expect(withError.container.textContent).toContain('导出失败：网络')
    withError.unmount()

    const without = mountSection({ error: '' })
    expect(without.container.querySelector('.v-alert')).toBeNull()
  })
})
