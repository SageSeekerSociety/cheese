/** 后端枚举值在英文界面下的名字，以及几处兜底的字：不能再是中文。 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { getErrorMessage } from '@/utils/errors'

import { setLocale } from '@/i18n'
import { label, NOTIF_KIND, TOPIC_STATUS } from '@/labels'

beforeEach(() => setLocale('en'))
afterEach(() => setLocale('zh-CN'))

describe('enum labels', () => {
  it('name every inbox kind and topic status in the reader’s language', () => {
    expect(Object.keys(NOTIF_KIND).map((k) => label(NOTIF_KIND, k))).toEqual([
      'Decision request',
      'Change alert',
      'Review',
    ])
    expect(Object.keys(TOPIC_STATUS).map((k) => label(TOPIC_STATUS, k))).toEqual(['In progress', 'Archived', 'Draft'])
    setLocale('zh-CN')
    expect(label(NOTIF_KIND, 'change_alert')).toBe('变更提醒')
  })

  it('show an unknown value as it came, and nothing for none', () => {
    expect(label(NOTIF_KIND, 'brand_new_kind')).toBe('brand_new_kind')
    expect(label(TOPIC_STATUS, null)).toBe('')
  })
})

it('an error with nothing to say reads "Unknown error"', () => {
  expect(getErrorMessage({})).toBe('Unknown error')
})
