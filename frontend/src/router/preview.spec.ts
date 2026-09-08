import { expect, it } from 'vitest'

import router from './index'

it('resolves a standalone preview launch while preserving the content path', () => {
  const route = router.resolve('/previews/topic-a?path=%2Freport%3Fpage%3D2')
  expect(route.name).toBe('preview-open')
  expect(route.params.topicId).toBe('topic-a')
  expect(route.query.path).toBe('/report?page=2')
  expect(route.meta.isFullPage).toBe(true)
})
