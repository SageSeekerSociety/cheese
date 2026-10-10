// 一版提交的评审结果：判过的写结果，写了评语才有「评语:」那一行。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import SubmissionReviewStatus from './SubmissionReviewStatus.vue'

import i18n, { setLocale } from '@/i18n'

function show(comment: string) {
  return render(SubmissionReviewStatus, {
    props: { review: { reviewed: true, detail: { accepted: false, score: 0, comment } } },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

describe('评审结果', () => {
  beforeEach(() => setLocale('zh-CN'))
  afterEach(cleanup)

  it('写了评语：下面一行是「评语: …」', () => {
    const { container } = show('旋转那一步写反了')

    expect(container.textContent).toContain('已驳回')
    expect(container.textContent).toContain('评语: 旋转那一步写反了')
  })

  it.each([
    ['没写评语', ''],
    ['评语只有空白', '  \n'],
  ])('%s：只有结果，没有光秃秃的「评语:」', (_, comment) => {
    const { container } = show(comment)

    expect(container.textContent).toContain('已驳回')
    expect(container.textContent).not.toContain('评语')
  })
})
