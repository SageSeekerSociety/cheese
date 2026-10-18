// @vitest-environment jsdom
// The frontend reaches the GitHub App install page through `connectGithubRepo`
// (its `install_url`), so the standalone install-url wrapper was dead code and
// was removed. This keeps it from quietly coming back.
import { describe, expect, it } from 'vitest'

describe('forge api surface', () => {
  it('no longer exports the unused getGithubInstallUrl wrapper', async () => {
    const forge = await import('./forge')
    expect(Object.keys(forge)).not.toContain('getGithubInstallUrl')
    expect(typeof forge.connectGithubRepo).toBe('function')
  })
})
