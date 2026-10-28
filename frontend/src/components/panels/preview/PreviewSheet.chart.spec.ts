// @vitest-environment jsdom
/**
 * 工作簿里带的图表在预览里画得出来。
 *
 * 走 jsdom：图表要从 zip 里读一段带命名空间的 XML（`c:chartSpace`、`c:pieChart`），
 * happy-dom 的 DOMParser 会把前缀吃掉，读出来是一棵没有名字的树。
 *
 * 这一份只盯两件：带图的工作簿底部有那颗「图表 N」，以及**看着图的时候切到一张没图的
 * 表要退回表格**——不退的话图表那一支照旧赢，而那颗开关因为新表没图已经不画了，表就
 * 再也摸不到。
 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it } from 'vitest'

import PreviewSheet from './PreviewSheet.vue'

import { setLocale } from '@/i18n'
import { analysisWorkbookBytes } from '@/test/analysisWorkbook'

beforeEach(() => setLocale('zh-CN'))

// jsdom 没有 ResizeObserver，而 `usePreviewNarrow`（量这一格有多宽）要用它。这一档只
// 关心「图表按钮在不在、切表会不会把表锁住」，不需要真的量宽度。
if (!('ResizeObserver' in globalThis)) {
  ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
}
afterEach(cleanup)

const vuetify = createVuetify({ components, directives })

function mountWorkbook() {
  return render(PreviewSheet, {
    props: { data: analysisWorkbookBytes(), kind: 'workbook' as const },
    global: { plugins: [vuetify] },
  })
}

it('图挂在它自己那张表上：切过去才有「图表 N」', async () => {
  const { container } = mountWorkbook()
  await waitFor(() => expect(container.querySelector('table[role=grid]')).not.toBeNull())

  // 第一张表「数据」上没有图。
  expect(screen.queryByTestId('sheet-charts')).toBeNull()

  await fireEvent.click(await screen.findByRole('tab', { name: '汇总' }))
  expect(await screen.findByTestId('sheet-charts')).toBeTruthy()
})

it('看着图表时切到一张没图的表，退回表格，不会把表锁在外面', async () => {
  const { container } = mountWorkbook()
  await waitFor(() => expect(container.querySelector('table[role=grid]')).not.toBeNull())

  await fireEvent.click(await screen.findByRole('tab', { name: '汇总' }))
  await fireEvent.click(await screen.findByTestId('sheet-charts'))
  expect(container.querySelector('.ps__charts')).not.toBeNull()

  // 「说明」那张表没有图。
  await fireEvent.click(await screen.findByRole('tab', { name: '说明' }))
  await waitFor(() => expect(container.querySelector('.ps__charts')).toBeNull())
  expect(container.querySelector('table[role=grid]')).not.toBeNull()
})

it('这一张表上没有图时不画那句「图看不了」', async () => {
  const { container } = mountWorkbook()
  await waitFor(() => expect(container.querySelector('table[role=grid]')).not.toBeNull())
  // 第一张表「数据」上没有图，也就没有跳过的图。
  expect(container.querySelector('[data-testid=sheet-charts-skipped]')).toBeNull()
})
