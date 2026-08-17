/** Unit tests for ManageDomainGroups domain validation and data transformation logic.
 *
 * Covers:
 * - Domain format validation (zod schema)
 * - Domain normalization (filtering empty strings, trimming)
 * - Domain array management (add/remove)
 * - Form submission payload construction
 * - API response → component state mapping
 * - DomainGroup → v-select items transformation (used in TaskForm)
 */
import { describe, expect, it } from 'vitest'
import { z } from 'zod'

// ---------------------------------------------------------------------------
// Domain validation schema — mirrors ManageDomainGroups.vue
// ---------------------------------------------------------------------------

const domainSchema = z
  .string()
  .min(1, { message: '请输入有效的域名格式' })
  .refine((v) => !v.includes('@') && !v.includes(' ') && v.includes('.'), {
    message: '请输入有效的域名格式',
  })

const formSchema = z.object({
  name: z.string().min(1, { message: '域名组名称不能为空' }),
  description: z.string().nullable().optional(),
  domains: z.array(domainSchema).min(1, { message: '请输入有效的域名格式' }),
})

// ---------------------------------------------------------------------------
// Domain array helpers — mirrors ManageDomainGroups.vue
// ---------------------------------------------------------------------------

function updateDomain(domains: string[], index: number, value: string): string[] {
  return domains.map((d, i) => (i === index ? value : d))
}

function addDomain(domains: string[]): string[] {
  return [...domains, '']
}

function removeDomain(domains: string[], index: number): string[] | null {
  if (domains.length <= 1) return null
  return domains.filter((_, i) => i !== index)
}

// ---------------------------------------------------------------------------
// Submission payload builder — mirrors submitForm in ManageDomainGroups.vue
// ---------------------------------------------------------------------------

interface DomainGroupPayload {
  name: string
  description: string | null
  domains: string[]
}

function buildPayload(values: { name: string; description?: string | null; domains: string[] }): DomainGroupPayload {
  return {
    name: values.name,
    description: values.description || null,
    domains: values.domains.filter((d) => d.trim() !== ''),
  }
}

// ---------------------------------------------------------------------------
// API response → DomainGroup list transformation — mirrors fetchDomainGroups
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

interface ListResponse {
  data: { groups: ApiDomainGroup[] }
}

function mapApiResponse(response: ListResponse): ApiDomainGroup[] {
  return response.data.groups ?? []
}

// ---------------------------------------------------------------------------
// DomainGroup → select items — mirrors domainGroupItems in TaskForm.vue
// ---------------------------------------------------------------------------

interface SelectItem {
  title: string
  value: number
  subtitle: string
}

function toSelectItems(groups: ApiDomainGroup[]): SelectItem[] {
  return groups.map((g) => ({ title: g.name, value: g.id, subtitle: g.domains.join(', ') }))
}

// ---------------------------------------------------------------------------
// Tests: Domain format validation
// ---------------------------------------------------------------------------

describe('domain format validation', () => {
  it('accepts standard domain names', () => {
    const valid = ['example.com', 'ruc.edu.cn', 'cs.mit.edu', 'mail.qq.com', 'a.b', 'sub.domain.example.co.uk']
    for (const d of valid) {
      const result = domainSchema.safeParse(d)
      expect(result.success, `domain "${d}" should be valid`).toBe(true)
    }
  })

  it('rejects domains containing @ (email addresses)', () => {
    const invalid = ['user@example.com', '@cs.edu.cn', 'admin@mail.ruc.edu.cn']
    for (const d of invalid) {
      const result = domainSchema.safeParse(d)
      expect(result.success, `domain "${d}" should be rejected`).toBe(false)
    }
  })

  it('rejects domains containing spaces', () => {
    const invalid = ['example .com', ' example.com', 'example.com ', 'exa mple.com']
    for (const d of invalid) {
      const result = domainSchema.safeParse(d)
      expect(result.success, `domain "${d}" should be rejected`).toBe(false)
    }
  })

  it('rejects values without a dot', () => {
    const invalid = ['localhost', 'example', 'intranet', 'abc']
    for (const d of invalid) {
      const result = domainSchema.safeParse(d)
      expect(result.success, `domain "${d}" should be rejected`).toBe(false)
    }
  })

  it('rejects empty strings', () => {
    const result = domainSchema.safeParse('')
    expect(result.success).toBe(false)
  })

  it('is case-sensitive (uppercase passes refine but may fail elsewhere)', () => {
    // The zod schema itself does NOT lowercase; the backend does.
    // Uppercase domains should pass the frontend refine check.
    const result = domainSchema.safeParse('Example.COM')
    expect(result.success).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// Tests: Full form validation
// ---------------------------------------------------------------------------

describe('form schema validation', () => {
  it('accepts valid form data with one domain', () => {
    const result = formSchema.safeParse({
      name: '中国人民大学',
      description: '人大校内邮箱',
      domains: ['ruc.edu.cn'],
    })
    expect(result.success).toBe(true)
  })

  it('accepts valid form data with multiple domains', () => {
    const result = formSchema.safeParse({
      name: '多域名组',
      domains: ['cs.edu.cn', 'math.edu.cn', 'physics.edu.cn'],
    })
    expect(result.success).toBe(true)
  })

  it('accepts form with null description', () => {
    const result = formSchema.safeParse({
      name: '测试组',
      description: null,
      domains: ['example.com'],
    })
    expect(result.success).toBe(true)
  })

  it('rejects form with empty name', () => {
    const result = formSchema.safeParse({
      name: '',
      domains: ['example.com'],
    })
    expect(result.success).toBe(false)
  })

  it('rejects form with empty domains array', () => {
    const result = formSchema.safeParse({
      name: '测试组',
      domains: [],
    })
    expect(result.success).toBe(false)
  })

  it('rejects form with only empty-string domains', () => {
    const result = formSchema.safeParse({
      name: '测试组',
      domains: ['', ''],
    })
    expect(result.success).toBe(false)
  })

  it('rejects form when one domain in array is invalid', () => {
    const result = formSchema.safeParse({
      name: '测试组',
      domains: ['valid.com', 'invalid@domain.com'],
    })
    expect(result.success).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// Tests: Domain array helpers
// ---------------------------------------------------------------------------

describe('domain array helpers', () => {
  describe('updateDomain', () => {
    it('updates value at given index', () => {
      const result = updateDomain(['a.com', 'b.com', 'c.com'], 1, 'updated.com')
      expect(result).toEqual(['a.com', 'updated.com', 'c.com'])
    })

    it('does not mutate the original array', () => {
      const original = ['a.com', 'b.com']
      const result = updateDomain(original, 0, 'new.com')
      expect(original).toEqual(['a.com', 'b.com'])
      expect(result).toEqual(['new.com', 'b.com'])
    })

    it('handles index 0', () => {
      expect(updateDomain(['old.com'], 0, 'new.com')).toEqual(['new.com'])
    })
  })

  describe('addDomain', () => {
    it('appends an empty string to the list', () => {
      expect(addDomain(['a.com'])).toEqual(['a.com', ''])
    })

    it('creates a new array', () => {
      const original = ['a.com']
      const result = addDomain(original)
      expect(result).not.toBe(original)
    })
  })

  describe('removeDomain', () => {
    it('removes the domain at given index', () => {
      expect(removeDomain(['a.com', 'b.com', 'c.com'], 1)).toEqual(['a.com', 'c.com'])
    })

    it('returns null when only one domain exists', () => {
      expect(removeDomain(['only.com'], 0)).toBeNull()
    })

    it('can remove the first element when multiple exist', () => {
      expect(removeDomain(['a.com', 'b.com'], 0)).toEqual(['b.com'])
    })

    it('can remove the last element when multiple exist', () => {
      expect(removeDomain(['a.com', 'b.com'], 1)).toEqual(['a.com'])
    })
  })
})

// ---------------------------------------------------------------------------
// Tests: Submission payload construction
// ---------------------------------------------------------------------------

describe('payload construction', () => {
  it('filters out empty domain strings', () => {
    const payload = buildPayload({
      name: '测试组',
      description: 'desc',
      domains: ['ruc.edu.cn', '', '  ', 'cs.edu.cn'],
    })
    expect(payload.domains).toEqual(['ruc.edu.cn', 'cs.edu.cn'])
  })

  it('handles all valid domains', () => {
    const payload = buildPayload({
      name: '测试组',
      domains: ['a.com', 'b.com', 'c.com'],
    })
    expect(payload.domains).toEqual(['a.com', 'b.com', 'c.com'])
  })

  it('converts nullish description to null', () => {
    const p1 = buildPayload({ name: 'x', domains: ['a.com'], description: undefined })
    expect(p1.description).toBeNull()

    const p2 = buildPayload({ name: 'x', domains: ['a.com'], description: '' })
    expect(p2.description).toBeNull()

    const p3 = buildPayload({ name: 'x', domains: ['a.com'], description: 'valid desc' })
    expect(p3.description).toBe('valid desc')
  })

  it('does not trim or lowercase domains (backend handles that)', () => {
    const payload = buildPayload({
      name: 'x',
      domains: ['  Example.COM  '],
    })
    // Frontend only filters out .trim() !== '' — it does NOT transform case/whitespace
    expect(payload.domains).toEqual(['  Example.COM  '])
  })
})

// ---------------------------------------------------------------------------
// Tests: API response mapping
// ---------------------------------------------------------------------------

describe('API response mapping', () => {
  const sampleGroups: ApiDomainGroup[] = [
    {
      id: 1,
      spaceId: 1,
      name: '中国人民大学',
      description: '人大校内邮箱',
      domains: ['ruc.edu.cn'],
      createdAt: 1700000000000,
      updatedAt: 1700000000000,
    },
    {
      id: 2,
      spaceId: 1,
      name: '多校联盟',
      description: null,
      domains: ['tsinghua.edu.cn', 'pku.edu.cn'],
      createdAt: 1700000001000,
      updatedAt: 1700000001000,
    },
  ]

  it('extracts groups from API response data', () => {
    const response: ListResponse = { data: { groups: sampleGroups } }
    const result = mapApiResponse(response)
    expect(result).toHaveLength(2)
    expect(result[0].name).toBe('中国人民大学')
  })

  it('handles empty groups array', () => {
    const response: ListResponse = { data: { groups: [] } }
    const result = mapApiResponse(response)
    expect(result).toEqual([])
  })

  it('handles undefined groups (nullish coalescing)', () => {
    const response: ListResponse = { data: { groups: undefined as unknown as ApiDomainGroup[] } }
    const result = mapApiResponse(response)
    expect(result).toEqual([])
  })
})

// ---------------------------------------------------------------------------
// Tests: Select item transformation
// ---------------------------------------------------------------------------

describe('domainGroupItems transformation', () => {
  it('maps domain groups to v-select items', () => {
    const groups: ApiDomainGroup[] = [
      {
        id: 1,
        spaceId: 1,
        name: 'A组',
        description: null,
        domains: ['a.com'],
        createdAt: 0,
        updatedAt: 0,
      },
      {
        id: 2,
        spaceId: 1,
        name: 'B组',
        description: null,
        domains: ['b1.com', 'b2.com'],
        createdAt: 0,
        updatedAt: 0,
      },
    ]
    const items = toSelectItems(groups)
    expect(items).toEqual([
      { title: 'A组', value: 1, subtitle: 'a.com' },
      { title: 'B组', value: 2, subtitle: 'b1.com, b2.com' },
    ])
  })

  it('returns empty array when no groups', () => {
    expect(toSelectItems([])).toEqual([])
  })

  it('produces empty subtitle for group with no domains', () => {
    const groups: ApiDomainGroup[] = [
      { id: 1, spaceId: 1, name: '空组', description: null, domains: [], createdAt: 0, updatedAt: 0 },
    ]
    expect(toSelectItems(groups)).toEqual([{ title: '空组', value: 1, subtitle: '' }])
  })
})

// ---------------------------------------------------------------------------
// Tests: Store-based fetchDomainGroups (mirrors updated ManageDomainGroups.vue)
// ---------------------------------------------------------------------------

interface StoreState {
  domainGroups: ApiDomainGroup[]
  fetchCalled: boolean
}

/** Simulates the new fetchDomainGroups that delegates to spaceStore */
async function fetchThroughStore(
  store: StoreState,
  apiResponse: ListResponse | null,
  shouldThrow: boolean
): Promise<StoreState> {
  if (shouldThrow) {
    // Simulated API error — store should keep existing data
    return store
  }

  // Simulates: store.fetchDomainGroups()
  store.fetchCalled = true
  if (apiResponse?.data?.groups) {
    store.domainGroups = apiResponse.data.groups
  } else {
    store.domainGroups = []
  }
  return store
}

/** Simulates the CRUD → refresh pattern:
 *  1. API call (create/update/delete)
 *  2. await fetchDomainGroups() (which calls store.fetchDomainGroups())
 */
async function crudAndRefresh(
  apiSucceeds: boolean,
  freshApiResponse: ListResponse
): Promise<{ storeUpdated: boolean; finalGroups: ApiDomainGroup[] }> {
  const store: StoreState = {
    domainGroups: [makeGroup({ id: 1, name: 'Old' })],
    fetchCalled: false,
  }

  if (!apiSucceeds) {
    // API error — fetch is NOT called, store retains old data
    return { storeUpdated: false, finalGroups: store.domainGroups }
  }

  // After successful CRUD, fetchDomainGroups() is called
  // → delegates to store.fetchDomainGroups()
  await fetchThroughStore(store, freshApiResponse, false)

  return { storeUpdated: true, finalGroups: store.domainGroups }
}

function makeGroup(overrides: Partial<ApiDomainGroup> = {}): ApiDomainGroup {
  return {
    id: 1,
    spaceId: 10,
    name: 'Group',
    description: null,
    domains: ['example.com'],
    createdAt: 0,
    updatedAt: 0,
    ...overrides,
  }
}

describe('store-based fetchDomainGroups (ManageDomainGroups → store integration)', () => {
  it('after create, store is refreshed with updated groups', async () => {
    const { storeUpdated, finalGroups } = await crudAndRefresh(true, {
      data: {
        groups: [makeGroup({ id: 1, name: 'Old' }), makeGroup({ id: 2, name: 'New' })],
      },
    })

    expect(storeUpdated).toBe(true)
    expect(finalGroups).toHaveLength(2)
    expect(finalGroups[1].name).toBe('New')
  })

  it('after update, store reflects the modified group', async () => {
    const { storeUpdated, finalGroups } = await crudAndRefresh(true, {
      data: {
        groups: [makeGroup({ id: 1, name: 'Updated Name' })],
      },
    })

    expect(storeUpdated).toBe(true)
    expect(finalGroups).toHaveLength(1)
    expect(finalGroups[0].name).toBe('Updated Name')
  })

  it('after delete, store reflects removal', async () => {
    const { storeUpdated, finalGroups } = await crudAndRefresh(true, {
      data: { groups: [] },
    })

    expect(storeUpdated).toBe(true)
    expect(finalGroups).toEqual([])
  })

  it('when CRUD API fails, store is NOT refreshed (retains old data)', async () => {
    const { storeUpdated, finalGroups } = await crudAndRefresh(false, {
      data: { groups: [] },
    })

    expect(storeUpdated).toBe(false)
    // Old data preserved
    expect(finalGroups).toHaveLength(1)
    expect(finalGroups[0].name).toBe('Old')
  })

  it('fetchThroughStore handles null groups (→ empty)', async () => {
    const store: StoreState = { domainGroups: [makeGroup()], fetchCalled: false }

    await fetchThroughStore(store, { data: { groups: null as unknown as ApiDomainGroup[] } }, false)

    expect(store.fetchCalled).toBe(true)
    expect(store.domainGroups).toEqual([])
  })

  it('fetchThroughStore preserves old data on error', async () => {
    const store: StoreState = {
      domainGroups: [makeGroup({ id: 1, name: 'Preserved' })],
      fetchCalled: false,
    }

    await fetchThroughStore(store, null, true)

    expect(store.fetchCalled).toBe(false)
    expect(store.domainGroups).toHaveLength(1)
    expect(store.domainGroups[0].name).toBe('Preserved')
  })
})

// ---------------------------------------------------------------------------
// Tests: Page refresh — explicit spaceId passed from route
// ---------------------------------------------------------------------------

/** Mirrors the updated ManageDomainGroups.vue fetchDomainGroups():
 *    await spaceStore.fetchDomainGroups(spaceId)
 *  where spaceId comes from route.params.spaceId.
 */
async function fetchDomainGroupsWithExplicitSpaceId(
  spaceId: number,
  storeState: { currentSpaceId: number | null; domainGroups: ApiDomainGroup[] },
  apiFn: (id: number) => Promise<ListResponse>
): Promise<{ apiCalledWith: number | null; domainGroups: ApiDomainGroup[] }> {
  let apiCalledWith: number | null = null

  const id = spaceId ?? storeState.currentSpaceId
  if (!id) return { apiCalledWith: null, domainGroups: storeState.domainGroups }

  try {
    const { data } = await apiFn(id)
    apiCalledWith = id
    storeState.domainGroups = data.groups ?? []
  } catch {
    // preserve existing
  }

  return { apiCalledWith, domainGroups: storeState.domainGroups }
}

describe('page refresh: fetchDomainGroups with explicit spaceId from route', () => {
  it('fetches with route spaceId when store currentSpaceId is still null', async () => {
    const storeState = { currentSpaceId: null, domainGroups: [] as ApiDomainGroup[] }
    let calledWithId: number | null = null
    const apiFn = async (id: number) => {
      calledWithId = id
      return { data: { groups: [makeGroup({ id: 1, name: 'CS' })] } }
    }

    const result = await fetchDomainGroupsWithExplicitSpaceId(42, storeState, apiFn)

    expect(calledWithId).toBe(42)
    expect(result.apiCalledWith).toBe(42)
    expect(result.domainGroups).toHaveLength(1)
    expect(result.domainGroups[0].name).toBe('CS')
  })

  it('uses route spaceId even when store has a different currentSpaceId', async () => {
    const storeState = { currentSpaceId: 10, domainGroups: [] as ApiDomainGroup[] }
    let calledWithId: number | null = null
    const apiFn = async (id: number) => {
      calledWithId = id
      return { data: { groups: [makeGroup({ id: 2, spaceId: 99 })] } }
    }

    const result = await fetchDomainGroupsWithExplicitSpaceId(99, storeState, apiFn)

    expect(calledWithId).toBe(99)
    expect(result.domainGroups[0].spaceId).toBe(99)
  })

  it('replaces stale data from previous space with fresh data', async () => {
    const storeState = {
      currentSpaceId: 10,
      domainGroups: [makeGroup({ id: 1, name: 'Old Space Group' })],
    }
    const apiFn = async (_id: number) => ({
      data: { groups: [makeGroup({ id: 2, name: 'New Space Group', spaceId: 42 })] },
    })

    const result = await fetchDomainGroupsWithExplicitSpaceId(42, storeState, apiFn)

    expect(result.domainGroups).toHaveLength(1)
    expect(result.domainGroups[0].name).toBe('New Space Group')
  })

  it('returns empty when route spaceId is 0', async () => {
    const storeState = { currentSpaceId: null, domainGroups: [] as ApiDomainGroup[] }
    let apiCalled = false
    const apiFn = async () => {
      apiCalled = true
      return { data: { groups: [] } }
    }

    const result = await fetchDomainGroupsWithExplicitSpaceId(0, storeState, apiFn)

    expect(apiCalled).toBe(false)
    expect(result.apiCalledWith).toBeNull()
  })
})
