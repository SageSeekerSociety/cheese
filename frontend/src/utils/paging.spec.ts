import { describe, expect, it } from 'vitest'

import { usePaging } from './paging'

describe('usePaging', () => {
  it('clears a previous fetch error after a successful refresh', async () => {
    let attempts = 0
    const paging = usePaging(async () => {
      attempts += 1
      if (attempts === 1) throw new Error('temporary outage')
      return { data: ['answer'], page: { pageSize: 1, hasMore: false } }
    })

    await paging.refresh()
    expect(paging.error.value?.message).toBe('temporary outage')

    await paging.refresh()

    expect(paging.data.value).toEqual(['answer'])
    expect(paging.error.value).toBeNull()
  })
})
