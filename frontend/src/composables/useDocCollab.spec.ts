// A page whose document schema the collaboration service does not speak must
// never bind an editor to the document: it would drop what it cannot parse and
// write the drop back for everyone. Refused for that reason, the page holds no
// document and says it needs a refresh; refused for any other reason, it is
// just offline.
import { defineComponent, nextTick } from 'vue'
import { render } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'

import { DOC_SCHEMA_MISMATCH } from '../lib/docSchema/version'

import { useDocCollab } from './useDocCollab'

const { providers, FakeProvider } = vi.hoisted(() => {
  type Handler = (payload: never) => void
  const providers: InstanceType<typeof FakeProvider>[] = []
  class FakeProvider {
    handlers = new Map<string, Handler[]>()
    awareness = { getStates: () => new Map(), on: () => {}, off: () => {} }
    destroyed = false
    constructor() {
      providers.push(this)
    }
    on(event: string, handler: Handler) {
      this.handlers.set(event, [...(this.handlers.get(event) ?? []), handler])
    }
    emit(event: string, payload: unknown) {
      for (const handler of this.handlers.get(event) ?? []) handler(payload as never)
    }
    attach() {}
    setAwarenessField() {}
    destroy() {
      this.destroyed = true
    }
  }
  return { providers, FakeProvider }
})

vi.mock('@hocuspocus/provider', () => ({
  HocuspocusProvider: FakeProvider,
  HocuspocusProviderWebsocket: class {
    destroy() {}
  },
}))
vi.mock('../api/docCollab', () => ({
  collabWsUrl: () => 'ws://collab.test/',
  getDocTicket: async () => ({ document: 'doc:r1', ticket: 'ticket', read_only: false }),
}))
vi.mock('../me', () => ({ myAccount: () => null }))

async function opened() {
  let collab!: ReturnType<typeof useDocCollab>
  const before = providers.length
  const page = render(
    defineComponent({
      setup() {
        collab = useDocCollab(() => 'r1')
        return () => null
      },
    })
  )
  await vi.waitFor(() => expect(providers.length).toBeGreaterThan(before))
  return { collab, provider: providers[providers.length - 1], stop: () => page.unmount() }
}

describe('opening a room’s document', () => {
  it('hands the document to the editor once the service has let the page in', async () => {
    const { collab, provider, stop } = await opened()
    expect(collab.session.value).toBeNull()
    provider.emit('authenticated', { scope: 'read-write' })
    await nextTick()
    expect(collab.session.value).not.toBeNull()
    expect(collab.outdated.value).toBe(false)
    stop()
  })

  it('refused for speaking another document schema, holds no document and asks for a refresh', async () => {
    const { collab, provider, stop } = await opened()
    provider.emit('authenticationFailed', { reason: DOC_SCHEMA_MISMATCH })
    await nextTick()
    expect(collab.outdated.value).toBe(true)
    expect(collab.session.value).toBeNull()
    expect(provider.destroyed).toBe(true)
    stop()
  })

  it('refused after it had the document, lets go of it', async () => {
    const { collab, provider, stop } = await opened()
    provider.emit('authenticated', { scope: 'read-write' })
    // The service is replaced by a build of another schema and the page reconnects.
    provider.emit('authenticationFailed', { reason: DOC_SCHEMA_MISMATCH })
    await nextTick()
    expect(collab.session.value).toBeNull()
    expect(collab.outdated.value).toBe(true)
    stop()
  })

  it('refused for any other reason, is offline and not asked to refresh', async () => {
    const { collab, provider, stop } = await opened()
    provider.emit('authenticationFailed', { reason: 'permission-denied' })
    await nextTick()
    expect(collab.outdated.value).toBe(false)
    expect(collab.connection.value).toBe('offline')
    stop()
  })
})

describe('a document deleted while it is open', () => {
  it('lets go of it and says so: nothing typed afterwards would be kept', async () => {
    const { collab, provider, stop } = await opened()
    provider.emit('authenticated', { scope: 'read-write' })
    await nextTick()
    provider.emit('stateless', { payload: JSON.stringify({ type: 'state', resource: 'deleted' }) })
    await nextTick()
    expect(collab.deleted.value).toBe(true)
    expect(collab.session.value).toBeNull()
    expect(provider.destroyed).toBe(true)
    stop()
  })
})
