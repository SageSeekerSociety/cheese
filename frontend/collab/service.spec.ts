// The collaboration service against a real Hocuspocus server and real
// providers, with a stand-in for the backend that keeps what a store sends
// and answers loads the way the backend does.

import { createHmac } from 'node:crypto'
import { createServer, type Server as HttpServer } from 'node:http'

import type { AddressInfo } from 'node:net'

import { HocuspocusProvider, HocuspocusProviderWebsocket } from '@hocuspocus/provider'
import { afterEach, describe, expect, it } from 'vitest'
import * as Y from 'yjs'

import {
  compareRoundTrip,
  DOC_SCHEMA_MISMATCH,
  DOC_SCHEMA_PARAM,
  DOC_SCHEMA_VERSION,
  exportMarkdown,
  liveNode,
  liveSuggestions,
  writeMarkdown,
} from '../src/lib/docSchema'

import { deriveKey } from './auth'
import { applyEdits, writeNode } from './edit'
import { createCollabServer } from './service'

const SECRET = 'test-secret'
const DOC = 'room:6f1c0a52-8a51-4f8e-9d55-2f4d2b1f8a10'

interface Version {
  content: string
  actors: string[]
  converted?: boolean
}

/** What one store sent, besides the state. */
interface StoreSent {
  content: string | null
  actors: string[]
  suggestions: { id: string; author: string; old: string; new: string }[]
  requested_by?: string | null
  edits?: { old: string; new: string; suggestion_id?: string }[] | null
  suggested?: boolean
}

/** The backend's side, kept in memory: what was stored, and by whom. */
class FakeBackend {
  state: Buffer | null = null
  content = ''
  versions: Version[] = []
  stores = 0
  sent: StoreSent[] = []
  server: HttpServer
  constructor(seed = '') {
    this.content = seed
    const bearer = `Bearer ${deriveKey(SECRET, 'internal')}`
    this.server = createServer((req, res) => {
      if (req.headers.authorization !== bearer) {
        res.writeHead(403).end()
        return
      }
      const chunks: Buffer[] = []
      req.on('data', (c: Buffer) => chunks.push(c))
      req.on('end', () => {
        res.setHeader('Content-Type', 'application/json')
        if (req.method === 'GET') {
          res.end(
            JSON.stringify({
              state: this.state ? this.state.toString('base64') : null,
              content: this.content,
              doc_version: this.versions.length,
            })
          )
          return
        }
        const body = JSON.parse(Buffer.concat(chunks).toString())
        this.stores++
        this.sent.push({ ...body, state: undefined })
        this.state = Buffer.from(body.state, 'base64')
        if (body.content !== null && body.content !== this.content) {
          this.content = body.content
          this.versions.push({ content: body.content, actors: body.actors, converted: !!body.converted })
        }
        res.end(JSON.stringify({ doc_version: this.versions.length }))
      })
    })
  }
  async listen(): Promise<string> {
    await new Promise<void>((resolve) => this.server.listen(0, '127.0.0.1', resolve))
    return `http://127.0.0.1:${(this.server.address() as AddressInfo).port}`
  }
}

function ticket(sub: string, opts: { ro?: boolean; doc?: string; key?: string } = {}): string {
  const enc = (v: unknown) => Buffer.from(JSON.stringify(v)).toString('base64url')
  const head = enc({ alg: 'HS256', typ: 'JWT' })
  const body = enc({ doc: opts.doc ?? DOC, sub, agent: false, ro: !!opts.ro, exp: Date.now() / 1000 + 60 })
  const sig = createHmac('sha256', opts.key ?? deriveKey(SECRET, 'ticket'))
    .update(`${head}.${body}`)
    .digest('base64url')
  return `${head}.${body}.${sig}`
}

const cleanups: (() => Promise<void> | void)[] = []
afterEach(async () => {
  for (const cleanup of cleanups.splice(0).reverse()) await cleanup()
})

async function setup(seed = '', { debounceMs = 50, maxDebounceMs = 200 } = {}) {
  const backend = new FakeBackend(seed)
  const backendUrl = await backend.listen()
  cleanups.push(() => new Promise<void>((resolve) => backend.server.close(() => resolve())))
  const server = createCollabServer({
    port: 0,
    backendUrl,
    secret: SECRET,
    debounceMs,
    maxDebounceMs,
    retryMs: 50,
    quiet: true,
  })
  await server.listen()
  cleanups.push(() => server.destroy())
  return { backend, server, url: server.webSocketURL, http: server.httpURL }
}

/** A page connecting to the service. It speaks this build's document schema
 *  unless `schema` says otherwise (null: a build that sends none). */
function client(url: string, token: string, { schema = String(DOC_SCHEMA_VERSION) as string | null } = {}) {
  const doc = new Y.Doc()
  const address = new URL(url)
  if (schema !== null) address.searchParams.set(DOC_SCHEMA_PARAM, schema)
  const socket = new HocuspocusProviderWebsocket({ url: address.toString() })
  let failed: string | null = null
  const provider = new HocuspocusProvider({
    websocketProvider: socket,
    name: DOC,
    document: doc,
    token,
    onAuthenticationFailed: ({ reason }) => {
      failed = reason
    },
  })
  provider.attach()
  cleanups.push(() => {
    provider.destroy()
    socket.destroy()
  })
  return { doc, provider, failed: () => failed !== null, reason: () => failed }
}

async function until(check: () => boolean, ms = 5000) {
  const start = Date.now()
  while (!check()) {
    if (Date.now() - start > ms) throw new Error('timed out waiting')
    await new Promise((r) => setTimeout(r, 20))
  }
}

function replace(http: string, body: { content: string; base: string | null; actor: string; check?: boolean }) {
  return fetch(`${http}/internal/documents/${encodeURIComponent(DOC)}/replace`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${deriveKey(SECRET, 'internal')}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

const SEED = [
  '# 题目说明',
  '',
  '本题要求每组提交一份**报告**，截止时间为十月十日。',
  '',
  '- 第一项',
  '- 第二项',
  '',
  '| 项目 | 分值 |',
  '| --- | --- |',
  '| 完成度 | 60 |',
  '',
  '```python',
  'print("hi")',
  '```',
  '',
].join('\n')

describe('the live document', () => {
  it('converts a Markdown document on first open without losing content, and stores the text it exports', async () => {
    const { backend, url } = await setup(SEED)
    const a = client(url, ticket('xiaowang'))
    await until(() => a.doc.getXmlFragment('default').length > 0)
    expect(compareRoundTrip(SEED, exportMarkdown(a.doc)).clean).toBe(true)
    await until(() => backend.state !== null)
    // What the backend hands out from now on is what the people editing see,
    // and the respelling is the platform's, not the person who opened it.
    expect(backend.content).toBe(exportMarkdown(a.doc))
    expect(backend.versions.map((v) => v.actors)).toEqual([['system']])
  })

  it('converges when two people edit at the same time, and stores what both see', async () => {
    const { backend, url } = await setup('第一段。\n\n第二段。\n')
    const a = client(url, ticket('xiaowang'))
    const b = client(url, ticket('teacher'))
    await until(() => exportMarkdown(a.doc).includes('第二段') && exportMarkdown(b.doc).includes('第二段'))
    writeMarkdown(a.doc, '第一段，A 补的。\n\n第二段。\n')
    writeMarkdown(b.doc, '第一段。\n\n第二段，B 补的。\n')
    await until(() => {
      const left = exportMarkdown(a.doc)
      return left === exportMarkdown(b.doc) && left.includes('A 补的') && left.includes('B 补的')
    })
    await until(() => backend.content === exportMarkdown(a.doc))
    const actors = backend.versions.flatMap((v) => v.actors)
    expect(actors).toContain('xiaowang')
    expect(actors).toContain('teacher')
  })

  it('refuses a connection without a valid ticket for this document', async () => {
    const { url } = await setup('原文。\n')
    const forged = client(url, ticket('mallory', { key: 'not-the-key' }))
    const elsewhere = client(url, ticket('mallory', { doc: 'room:00000000-0000-0000-0000-000000000000' }))
    await until(() => forged.failed() && elsewhere.failed())
    expect(forged.doc.getXmlFragment('default').length).toBe(0)
  })

  it('refuses a page built with another document schema, and the document is left as it was', async () => {
    const { backend, url } = await setup('第一段。\n\n第二段。\n')
    const current = client(url, ticket('xiaowang'))
    await until(() => exportMarkdown(current.doc).includes('第二段') && backend.state !== null)
    const stores = backend.stores
    const stored = backend.content

    // An older page (no version) and one of another build, each holding a
    // document that reads differently — what dropping unknown content looks like.
    const older = client(url, ticket('teacher'), { schema: null })
    const other = client(url, ticket('teacher'), { schema: String(DOC_SCHEMA_VERSION + 1) })
    writeMarkdown(older.doc, '第一段。\n')
    writeMarkdown(other.doc, '另一份。\n')
    await until(() => older.failed() && other.failed())
    expect(older.reason()).toBe(DOC_SCHEMA_MISMATCH)
    expect(other.reason()).toBe(DOC_SCHEMA_MISMATCH)
    // Neither got the document, and nothing they hold reached it.
    expect(exportMarkdown(older.doc)).not.toContain('第二段')
    await new Promise((r) => setTimeout(r, 300))
    expect(exportMarkdown(current.doc)).toBe(stored)
    expect(backend.stores).toBe(stores)
    expect(backend.content).toBe(stored)

    // A page of this build opens it as before.
    const later = client(url, ticket('teacher'))
    await until(() => exportMarkdown(later.doc) === stored)
    expect(later.failed()).toBe(false)
  })

  it('drops changes from a read-only connection', async () => {
    const { backend, url } = await setup('原文。\n')
    const reader = client(url, ticket('guest', { ro: true }))
    const writer = client(url, ticket('xiaowang'))
    await until(() => exportMarkdown(reader.doc).includes('原文') && exportMarkdown(writer.doc).includes('原文'))
    writeMarkdown(reader.doc, '原文。\n\n只读者写的。\n')
    writeMarkdown(writer.doc, '原文。\n\n作者写的。\n')
    await until(() => backend.content.includes('作者写的'))
    await new Promise((r) => setTimeout(r, 300))
    expect(backend.content).not.toContain('只读者写的')
    expect(exportMarkdown(writer.doc)).not.toContain('只读者写的')
  })

  it('keeps a backend write when the live document stores again, together with typing that came after', async () => {
    const { backend, url, http } = await setup('第一段。\n')
    const a = client(url, ticket('xiaowang'))
    await until(() => exportMarkdown(a.doc).includes('第一段'))
    const response = await replace(http, {
      content: '第一段。\n\n芝士写的第二段。\n',
      base: '第一段。\n',
      actor: 'cheese-agent',
    })
    expect(response.status).toBe(200)
    expect(backend.versions.at(-1)?.actors).toEqual(['cheese-agent'])
    expect(backend.versions.at(-1)?.content).toContain('芝士写的第二段')
    await until(() => exportMarkdown(a.doc).includes('芝士写的'))
    writeMarkdown(a.doc, exportMarkdown(a.doc) + '\n人后来写的。\n')
    await until(() => backend.content.includes('人后来写的'))
    expect(backend.content).toContain('芝士写的第二段')
  })

  it('refuses a backend write based on an older document, and stores the newer one first', async () => {
    const { backend, url, http } = await setup('第一段。\n')
    const a = client(url, ticket('xiaowang'))
    await until(() => exportMarkdown(a.doc).includes('第一段'))
    writeMarkdown(a.doc, '第一段，人刚改的。\n')
    await until(() => !a.provider.hasUnsyncedChanges)
    const response = await replace(http, { content: '芝士的版本。\n', base: '第一段。\n', actor: 'cheese-agent' })
    expect(response.status).toBe(409)
    expect(backend.content).toContain('人刚改的')
    expect((await response.json()).doc_version).toBe(backend.versions.length)
    await new Promise((r) => setTimeout(r, 200))
    expect(exportMarkdown(a.doc)).not.toContain('芝士的版本')
  })

  it('takes a writer that read again after a conflict and retries while nobody types', async () => {
    // Long enough that only the conflict itself can store the typing.
    const { backend, url, http } = await setup('第一段。\n', { debounceMs: 10_000, maxDebounceMs: 30_000 })
    const a = client(url, ticket('xiaowang'))
    await until(() => exportMarkdown(a.doc).includes('第一段'))
    const read = backend.content
    writeMarkdown(a.doc, '第一段，人刚改的。\n')
    await until(() => !a.provider.hasUnsyncedChanges)
    const refused = await replace(http, { content: '芝士的版本。\n', base: read, actor: 'cheese-agent' })
    expect(refused.status).toBe(409)
    const reread = backend.content
    const retried = await replace(http, { content: reread + '\n芝士补的。\n', base: reread, actor: 'cheese-agent' })
    expect(retried.status).toBe(200)
    await until(() => exportMarkdown(a.doc).includes('芝士补的'))
    expect(exportMarkdown(a.doc)).toContain('人刚改的')
  })

  it('refuses a Markdown write that would lose visible text or misshape a block, says which line, and changes nothing', async () => {
    const { backend, url, http } = await setup('第一段。\n')
    const a = client(url, ticket('xiaowang'))
    await until(() => exportMarkdown(a.doc).includes('第一段'))
    const versions = backend.versions.length
    const footnote = '第一段。[^1]\n\n## 资料\n\n[^2]: 出自教务处二〇二五年的数据\n'
    const table = '第一段。\n\n| 项目 | 分值 |\n| --- | --- |\n| 完成度 | 60 | 备注写在这里 |\n'
    for (const [content, line] of [
      [footnote, 1],
      [table, 5],
    ] as const) {
      const response = await replace(http, { content, base: backend.content, actor: 'cheese-agent', check: true })
      expect(response.status).toBe(422)
      const body = await response.json()
      expect(body.line).toBe(line)
      expect(body.message).toContain(`第 ${line} 行`)
    }
    await new Promise((r) => setTimeout(r, 200))
    expect(backend.versions.length).toBe(versions)
    expect(exportMarkdown(a.doc)).not.toContain('教务处')
    expect(exportMarkdown(a.doc)).not.toContain('备注')
  })

  it('takes a Markdown write that only differs in how it is spelled', async () => {
    const { backend, url, http } = await setup('第一段。\n')
    const a = client(url, ticket('xiaowang'))
    await until(() => exportMarkdown(a.doc).includes('第一段'))
    const respelled =
      '第一段。\n\n* 星号列表\n+ 加号列表\n\n1) 括号编号\n\n转义 \\* 号、&nbsp;实体、<span>字面标签</span>、$x^2$\n\n|a|b|\n|-|-|\n|1|2|\n'
    const response = await replace(http, {
      content: respelled,
      base: backend.content,
      actor: 'cheese-agent',
      check: true,
    })
    expect(response.status).toBe(200)
    await until(() => exportMarkdown(a.doc).includes('字面标签'))
  })
})

interface EditBody {
  edits: { old: string; new: string }[]
  actor: string
  requested_by?: string | null
  mode: 'direct' | 'suggest'
  reason?: string | null
}

function edit(http: string, body: EditBody) {
  return fetch(`${http}/internal/documents/${encodeURIComponent(DOC)}/edit`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${deriveKey(SECRET, 'internal')}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

const TWO = '第一段讲目标。\n\n第二段讲范围。'

async function opened(seed = TWO) {
  const env = await setup(seed)
  const a = client(env.url, ticket('xiaowang'))
  await until(() => exportMarkdown(a.doc).includes('第二段'))
  await until(() => env.backend.state !== null)
  return { ...env, a }
}

describe('an edit to part of the live document', () => {
  it('changes that passage, stores it as the writer and for whoever asked, and reaches every editor', async () => {
    const { backend, http, a } = await opened()
    const response = await edit(http, {
      edits: [{ old: '第二段讲范围。', new: '第二段讲范围和不做的事。' }],
      actor: 'cheese-agent',
      requested_by: 'teacher',
      mode: 'direct',
    })
    expect(response.status).toBe(200)
    expect((await response.json()).edits).toEqual([{ old: '第二段讲范围。', new: '第二段讲范围和不做的事。' }])
    expect(backend.content).toBe('第一段讲目标。\n\n第二段讲范围和不做的事。')
    expect(backend.versions.at(-1)?.actors).toEqual(['cheese-agent'])
    expect(backend.sent.at(-1)?.requested_by).toBe('teacher')
    await until(() => exportMarkdown(a.doc).includes('不做的事'))
  })

  it('keeps what someone is typing elsewhere in the document at the same time', async () => {
    const { backend, http, a } = await opened()
    writeMarkdown(a.doc, '第一段讲目标，人正在补。\n\n第二段讲范围。\n')
    const response = await edit(http, {
      edits: [{ old: '第二段讲范围。', new: '第二段讲范围，芝士补的。' }],
      actor: 'cheese-agent',
      mode: 'direct',
    })
    expect(response.status).toBe(200)
    const both = (text: string) => text.includes('人正在补') && text.includes('芝士补的')
    await until(() => both(exportMarkdown(a.doc)))
    await until(() => both(backend.content))
  })

  it('applies nothing when a passage is missing or appears more than once, and says which one', async () => {
    const { backend, http, a } = await opened()
    const versions = backend.versions.length
    for (const [edits, index, reason] of [
      [
        [
          { old: '第一段讲目标。', new: '第一段改了。' },
          { old: '第三段', new: '没有这段' },
        ],
        1,
        'not_found',
      ],
      [[{ old: '段讲', new: '段说' }], 0, 'ambiguous'],
    ] as const) {
      const response = await edit(http, { edits: [...edits], actor: 'cheese-agent', mode: 'direct' })
      expect(response.status).toBe(422)
      expect(await response.json()).toMatchObject({ error: 'edit', index, reason })
    }
    await new Promise((r) => setTimeout(r, 200))
    expect(backend.versions.length).toBe(versions)
    expect(exportMarkdown(a.doc)).toBe(TWO)
  })

  it('refuses an edit that would lose visible text, and changes nothing', async () => {
    const { backend, http, a } = await opened()
    const versions = backend.versions.length
    const response = await edit(http, {
      edits: [
        {
          old: '第二段讲范围。',
          new: '第二段讲范围。\n\n| 项目 | 分值 |\n| --- | --- |\n| 出自教务处 | 60 | 多一格 |',
        },
      ],
      actor: 'cheese-agent',
      mode: 'direct',
    })
    expect(response.status).toBe(422)
    expect((await response.json()).error).toBe('content')
    await new Promise((r) => setTimeout(r, 200))
    expect(backend.versions.length).toBe(versions)
    expect(exportMarkdown(a.doc)).not.toContain('教务处')
  })

  it('as a suggestion leaves the text as it was and shows everyone a pending change by its author', async () => {
    const { backend, http, a } = await opened()
    const response = await edit(http, {
      edits: [{ old: '第二段讲范围。', new: '第二段讲边界。' }],
      actor: 'cheese-agent',
      mode: 'suggest',
      reason: '范围这个词太宽',
    })
    expect(response.status).toBe(200)
    const [applied] = (await response.json()).edits
    expect(backend.content).toBe(TWO)
    const last = backend.sent.at(-1)!
    expect(last.suggested).toBe(true)
    expect(last.suggestions).toEqual([{ id: applied.suggestion_id, author: 'cheese-agent', old: '范围', new: '边界' }])
    await until(() => liveSuggestions(a.doc).length === 1)
    expect(exportMarkdown(a.doc)).toBe(TWO)
  })

  it('applied directly, leaves somebody else’s pending suggestion where it was', async () => {
    const { backend, http, a } = await opened()
    await edit(http, {
      edits: [{ old: '第一段讲目标。', new: '第一段讲目的。' }],
      actor: 'teacher',
      mode: 'suggest',
    })
    const response = await edit(http, {
      edits: [{ old: '第二段讲范围。', new: '第二段讲范围和时间。' }],
      actor: 'cheese-agent',
      mode: 'direct',
    })
    expect(response.status).toBe(200)
    expect(backend.content).toBe('第一段讲目标。\n\n第二段讲范围和时间。')
    expect(backend.sent.at(-1)?.suggestions).toEqual([expect.objectContaining({ author: 'teacher', new: '的' })])
    await until(() => exportMarkdown(a.doc).includes('和时间'))
    expect(liveSuggestions(a.doc)).toEqual([expect.objectContaining({ author: 'teacher' })])
  })

  it('refuses to change text that has a suggestion pending on it', async () => {
    const { backend, http, a } = await opened()
    await edit(http, {
      edits: [{ old: '第一段讲目标。', new: '第一段讲目的。' }],
      actor: 'teacher',
      mode: 'suggest',
    })
    const versions = backend.versions.length
    const response = await edit(http, {
      edits: [{ old: '讲目标', new: '讲任务' }],
      actor: 'cheese-agent',
      mode: 'direct',
    })
    expect(response.status).toBe(409)
    expect(await response.json()).toMatchObject({ error: 'edit', index: 0, reason: 'suggested' })
    expect(backend.versions.length).toBe(versions)
    expect(exportMarkdown(a.doc)).toBe(TWO)
  })

  it('cannot suggest reshaping paragraphs, and changes nothing', async () => {
    const { backend, http, a } = await opened()
    const versions = backend.versions.length
    for (const change of ['- 第二段讲范围。', '第一段讲目标。第二段讲范围。']) {
      const old = change.startsWith('-') ? '第二段讲范围。' : TWO
      const response = await edit(http, { edits: [{ old, new: change }], actor: 'cheese-agent', mode: 'suggest' })
      expect(response.status).toBe(422)
      expect(await response.json()).toMatchObject({ error: 'edit', index: 0, reason: 'structure' })
    }
    expect(backend.versions.length).toBe(versions)
    expect(backend.sent.some((sent) => sent.suggested || sent.suggestions.length)).toBe(false)
    expect(liveSuggestions(a.doc)).toEqual([])
  })

  it('tells the backend what is still pending every time the document is stored', async () => {
    const { backend, http, a } = await opened()
    await edit(http, {
      edits: [{ old: '第二段讲范围。', new: '第二段讲边界。' }],
      actor: 'cheese-agent',
      mode: 'suggest',
    })
    await until(() => liveSuggestions(a.doc).length === 1)
    const stores = backend.stores
    // Somebody types in the first paragraph; their editor keeps the suggestion.
    const typed = applyEdits(liveNode(a.doc), [{ old: '讲目标', new: '讲目标，人改的' }], 'direct', 'xiaowang')
    if (!typed.ok) throw new Error('could not type')
    writeNode(a.doc, typed.doc)
    await until(() => backend.stores > stores && backend.content.includes('人改的'))
    expect(backend.sent.at(-1)?.suggestions).toEqual([expect.objectContaining({ author: 'cheese-agent' })])
  })
})
