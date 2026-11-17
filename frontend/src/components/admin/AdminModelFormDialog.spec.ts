/**
 * 编辑模型：缓存有效期按分钟填，按秒存；清空就是去掉，不动就不带。
 *
 * 模子照 `AdminModelDetailDrawer.spec.ts`：vue-i18n 键透传、`createVuetify`、stub
 * `ResizeObserver` / `visualViewport`。
 */
import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

vi.mock('vue-i18n', async (importOriginal) => {
  const actual = await importOriginal<typeof import('vue-i18n')>()
  return {
    ...actual,
    useI18n: () => ({
      t: (key: string, params?: Record<string, unknown>) => (params ? `${key} ${JSON.stringify(params)}` : key),
    }),
  }
})

import AdminModelFormDialog from './AdminModelFormDialog.vue'

function seed(cache: number | null) {
  return {
    name: 'glm-5.2',
    label: 'GLM 5.2',
    selectable: true,
    priced: true,
    capabilities: {},
    cache_lifetime_s: cache,
  }
}

function mountEdit(cache: number | null) {
  const vuetify = createVuetify({ components, directives })
  const submitted: unknown[] = []
  const Wrapper = {
    components: { AdminModelFormDialog },
    template: '<v-app><AdminModelFormDialog v-bind="$attrs" /></v-app>',
    inheritAttrs: false,
  }
  const attrs = {
    modelValue: true,
    mode: 'edit',
    seed: seed(cache),
    onSubmit: (payload: unknown) => submitted.push(payload),
  }
  const page = render(Wrapper as unknown as Component, {
    global: { plugins: [vuetify] },
    attrs,
  })
  return { page, submitted }
}

async function save(page: ReturnType<typeof render>) {
  const buttons = await page.findAllByRole('button', { name: 'models.dialog.save' })
  await fireEvent.click(buttons[0])
}

beforeAll(() => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    pageLeft: 0,
    pageTop: 0,
    scale: 1,
    addEventListener() {},
    removeEventListener() {},
  })
})

afterEach(cleanup)

describe('编辑模型 · 缓存有效期', () => {
  it('填了分钟数，存的是秒', async () => {
    const { page, submitted } = mountEdit(null)
    const field = await page.findByLabelText('models.dialog.field.cacheMinutes')
    await fireEvent.update(field, '240')
    await save(page)
    expect(submitted).toHaveLength(1)
    expect(submitted[0]).toMatchObject({ cache_lifetime_s: 14400 })
  })

  it('清空已有的值，存 0 把它去掉', async () => {
    const { page, submitted } = mountEdit(300)
    const field = await page.findByLabelText('models.dialog.field.cacheMinutes')
    expect((field as HTMLInputElement).value).toBe('5')
    await fireEvent.update(field, '')
    await save(page)
    expect(submitted[0]).toMatchObject({ cache_lifetime_s: 0 })
  })

  it('没动这一项，保存时不带它', async () => {
    const { page, submitted } = mountEdit(3600)
    await page.findByLabelText('models.dialog.field.cacheMinutes')
    await save(page)
    expect(submitted).toHaveLength(1)
    expect(submitted[0]).not.toHaveProperty('cache_lifetime_s')
  })

  it('不是整数分钟时保存不了', async () => {
    const { page, submitted } = mountEdit(null)
    const field = await page.findByLabelText('models.dialog.field.cacheMinutes')
    await fireEvent.update(field, '1.5')
    await save(page)
    expect(submitted).toHaveLength(0)
  })
})
