// The download page offers the build for the visitor's own computer first.
import { afterEach, describe, expect, it, vi } from 'vitest'

import { downloadForThisComputer } from './desktop'

function visitor(userAgent: string, architecture?: string) {
  vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue(userAgent)
  Object.defineProperty(navigator, 'userAgentData', {
    configurable: true,
    value: architecture === undefined ? undefined : { getHighEntropyValues: async () => ({ architecture }) },
  })
}

afterEach(() => {
  vi.restoreAllMocks()
  Object.defineProperty(navigator, 'userAgentData', { configurable: true, value: undefined })
})

describe('downloadForThisComputer', () => {
  it('gives Windows the Windows installer', async () => {
    visitor('Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/129.0', 'x86')
    expect((await downloadForThisComputer()).os).toBe('windows')
  })

  it('gives an Intel Mac the Intel build when the browser says so', async () => {
    visitor('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/129.0', 'x86')
    expect((await downloadForThisComputer()).href).toMatch(/x64\.dmg$/)
  })

  it("gives a Mac whose chip the browser does not say Apple's build", async () => {
    visitor('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Version/18.0 Safari/605.1.15')
    expect((await downloadForThisComputer()).href).toMatch(/arm64\.dmg$/)
  })
})
