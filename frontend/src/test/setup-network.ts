import { afterEach, expect } from 'vitest'

const unexpectedRequests: string[] = []

globalThis.fetch = async (input) => {
  const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
  unexpectedRequests.push(url)
  throw new Error(`Unexpected fetch in unit test: ${url}`)
}

afterEach(() => {
  // Components may catch fetch failures; unexpected requests must still fail the test.
  const requests = unexpectedRequests.splice(0)
  expect(requests, 'Mock every HTTP request used by this test').toEqual([])
})
