/** Unit tests for spaceStore domainGroups fetch logic.
 *
 * Tests the pure logic extracted from spaceStore.fetchDomainGroups():
 * - Guard: returns early if currentSpaceId is null
 * - Data mapping: response.data.groups ?? [] → domainGroups
 * - Error handling: catch + log, preserve existing data
 *
 * These mirror the logic in spaceStore.fetchDomainGroups() and the
 * ManageDomainGroups/PublishTask wrappers that delegate to it.
 */
import { describe, expect, it } from 'vitest'

// ---------------------------------------------------------------------------
// Types — mirror spaceStore state shape
// ---------------------------------------------------------------------------

interface ApiDomainGroup {
  id: number
  spaceId: number
  name: string
  description: string | null
  domains: string[]
  createdAt: number
  updatedAt: number
}

interface DomainGroupStoreState {
  currentSpaceId: number | null
  domainGroups: ApiDomainGroup[]
}

interface ListResponse {
  data: { groups: ApiDomainGroup[] | null | undefined }
}

// ---------------------------------------------------------------------------
// Extracted pure logic — mirrors spaceStore.fetchDomainGroups()
// ---------------------------------------------------------------------------

async function fetchDomainGroupsLogic(
  state: DomainGroupStoreState,
  apiFn: (spaceId: number) => Promise<ListResponse>,
  explicitSpaceId?: number,
): Promise<void> {
  const id = explicitSpaceId ?? state.currentSpaceId
  if (!id) return
  try {
    const { data } = await apiFn(id)
    state.domainGroups = data.groups ?? []
  } catch (error) {
    console.error('获取域名组失败:', error)
  }
}

// Wrapper used by ManageDomainGroups/PublishTask:
// delegates to store.fetchDomainGroups (which internally calls the above logic)
async function viewFetchDomainGroups(
  state: DomainGroupStoreState,
  apiFn: (spaceId: number) => Promise<ListResponse>,
): Promise<void> {
  if (!state.currentSpaceId) return
  await fetchDomainGroupsLogic(state, apiFn)
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeGroup(overrides: Partial<ApiDomainGroup> = {}): ApiDomainGroup {
  return {
    id: 1,
    spaceId: 10,
    name: 'test-group',
    description: null,
    domains: ['example.com'],
    createdAt: 0,
    updatedAt: 0,
    ...overrides,
  }
}

function makeState(spaceId: number | null, groups?: ApiDomainGroup[]): DomainGroupStoreState {
  return { currentSpaceId: spaceId, domainGroups: groups ?? [] }
}

// ---------------------------------------------------------------------------
// Tests: fetchDomainGroupsLogic
// ---------------------------------------------------------------------------

describe('fetchDomainGroups core logic', () => {
  describe('guard: currentSpaceId', () => {
    it('returns early when currentSpaceId is null', async () => {
      const state = makeState(null)
      let apiCalled = false
      const apiFn = async (_: number) => {
        apiCalled = true
        return { data: { groups: [] } }
      }

      await fetchDomainGroupsLogic(state, apiFn)

      expect(apiCalled).toBe(false)
    })

    it('returns early when currentSpaceId is 0 (falsy)', async () => {
      const state = makeState(0 as unknown as null)
      let apiCalled = false
      const apiFn = async (_: number) => {
        apiCalled = true
        return { data: { groups: [] } }
      }

      await fetchDomainGroupsLogic(state, apiFn)

      expect(apiCalled).toBe(false)
    })

    it('proceeds when currentSpaceId is set', async () => {
      const state = makeState(42)
      let apiCalled = false
      const apiFn = async (id: number) => {
        apiCalled = true
        return { data: { groups: [makeGroup({ id, spaceId: id })] } }
      }

      await fetchDomainGroupsLogic(state, apiFn)

      expect(apiCalled).toBe(true)
    })
  })

  describe('data mapping', () => {
    it('populates domainGroups from API response', async () => {
      const state = makeState(10)
      const groups = [makeGroup({ id: 1, name: 'A' }), makeGroup({ id: 2, name: 'B' })]
      const apiFn = async () => ({ data: { groups } })

      await fetchDomainGroupsLogic(state, apiFn)

      expect(state.domainGroups).toEqual(groups)
    })

    it('handles null groups → empty array', async () => {
      const state = makeState(10, [makeGroup()]) // pre-populated
      const apiFn = async () => ({ data: { groups: null } })

      await fetchDomainGroupsLogic(state, apiFn)

      expect(state.domainGroups).toEqual([])
    })

    it('handles undefined groups → empty array', async () => {
      const state = makeState(10)
      const apiFn = async () => ({ data: {} as ListResponse })

      await fetchDomainGroupsLogic(state, apiFn)

      expect(state.domainGroups).toEqual([])
    })

    it('handles empty groups array', async () => {
      const state = makeState(10, [makeGroup()]) // pre-populated
      const apiFn = async () => ({ data: { groups: [] } })

      await fetchDomainGroupsLogic(state, apiFn)

      expect(state.domainGroups).toEqual([])
    })

    it('updates domainGroups on subsequent fetch (simulates CRUD refresh)', async () => {
      const state = makeState(10, [makeGroup({ id: 1, name: 'Old' })])

      // First fetch returns updated groups
      const apiFn = async () => ({
        data: {
          groups: [
            makeGroup({ id: 1, name: 'Updated' }),
            makeGroup({ id: 2, name: 'New' }),
          ],
        },
      })
      await fetchDomainGroupsLogic(state, apiFn)

      expect(state.domainGroups).toHaveLength(2)
      expect(state.domainGroups[0].name).toBe('Updated')
      expect(state.domainGroups[1].name).toBe('New')
    })
  })

  describe('explicit spaceId parameter (page refresh fix)', () => {
    it('fetches with explicit spaceId even when currentSpaceId is null', async () => {
      const state = makeState(null) // simulating fresh page load
      let calledWithId: number | null = null
      const apiFn = async (id: number) => {
        calledWithId = id
        return { data: { groups: [makeGroup({ id: 1 })] } }
      }

      await fetchDomainGroupsLogic(state, apiFn, 42)

      expect(calledWithId).toBe(42)
      expect(state.domainGroups).toHaveLength(1)
    })

    it('uses explicit spaceId over currentSpaceId when both are set', async () => {
      const state = makeState(10, [makeGroup({ id: 1, name: 'Old' })])
      let calledWithId: number | null = null
      const apiFn = async (id: number) => {
        calledWithId = id
        return { data: { groups: [makeGroup({ id: 2, name: 'New' })] } }
      }

      await fetchDomainGroupsLogic(state, apiFn, 99)

      expect(calledWithId).toBe(99)
      expect(state.domainGroups).toHaveLength(1)
      expect(state.domainGroups[0].name).toBe('New')
    })

    it('returns early when both explicit spaceId and currentSpaceId are null/undefined', async () => {
      const state = makeState(null)
      let apiCalled = false
      const apiFn = async (_: number) => {
        apiCalled = true
        return { data: { groups: [] } }
      }

      await fetchDomainGroupsLogic(state, apiFn)

      expect(apiCalled).toBe(false)
    })
  })

  describe('error handling', () => {
    it('preserves existing data on API error', async () => {
      const existing = [makeGroup({ id: 99, name: 'Preserved' })]
      const state = makeState(10, existing)
      let errorLogged = false
      const originalError = console.error
      console.error = (..._args: unknown[]) => {
        errorLogged = true
      }

      const apiFn = async () => {
        throw new Error('Network failure')
      }

      await fetchDomainGroupsLogic(state, apiFn)

      expect(errorLogged).toBe(true)
      expect(state.domainGroups).toEqual(existing) // unchanged

      console.error = originalError
    })

    it('keeps empty array on error when no pre-existing data', async () => {
      const state = makeState(10, [])
      const originalError = console.error
      console.error = () => {}

      const apiFn = async () => {
        throw new Error('Network failure')
      }

      await fetchDomainGroupsLogic(state, apiFn)

      expect(state.domainGroups).toEqual([])

      console.error = originalError
    })
  })
})

// ---------------------------------------------------------------------------
// Tests: viewFetchDomainGroups (PublishTask/ManageDomainGroups wrapper)
// ---------------------------------------------------------------------------

describe('viewFetchDomainGroups (wrapper that delegates to store)', () => {
  it('delegates to store fetch when spaceId is set', async () => {
    const state = makeState(10)
    let apiCalled = false
    const apiFn = async () => {
      apiCalled = true
      return { data: { groups: [makeGroup()] } }
    }

    await viewFetchDomainGroups(state, apiFn)

    expect(apiCalled).toBe(true)
    expect(state.domainGroups).toHaveLength(1)
  })

  it('returns early when spaceId is null (same guard as store)', async () => {
    const state = makeState(null, [makeGroup()])
    let apiCalled = false
    const apiFn = async () => {
      apiCalled = true
      return { data: { groups: [] } }
    }

    await viewFetchDomainGroups(state, apiFn)

    expect(apiCalled).toBe(false)
    // Existing data untouched
    expect(state.domainGroups).toHaveLength(1)
  })
})
