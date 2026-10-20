import { describe, expect, it } from 'vitest'

import { stepError } from './assistantView'

import { t } from '@/i18n'

describe('stepError', () => {
  it('平台原样的回应只说是哪一类', () => {
    const body = '{"code":404,"message":"Resource space not found"}'
    expect(stepError(`Platform HTTP 404: ${body}`)).toBe(t('tasks.assistant.steps.notFound'))
    expect(stepError('Platform HTTP 403: {}')).toBe(t('tasks.assistant.steps.denied'))
    expect(stepError('Platform HTTP 500: oops')).toBe(t('tasks.assistant.steps.failed'))
    expect(stepError('Traceback (most recent call last)')).toBe(t('tasks.assistant.steps.failed'))
  })

  it('平台拒绝时说的那句原样留着', () => {
    const said = '这份草稿已经发布，不能再改'
    expect(stepError(said)).toBe(said)
  })
})
