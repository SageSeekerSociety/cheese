// The collaboration service against a real Hocuspocus server and real
// providers, with a stand-in for the backend that keeps what a store sends
// and answers loads the way the backend does.

import { createHmac } from 'node:crypto'
import { createServer, type Server as HttpServer } from 'node:http'

import type { AddressInfo } from 'node:net'

import { HocuspocusProvider, HocuspocusProviderWebsocket } from '@hocuspocus/provider'
import { afterEach, describe, expect, it } from 'vitest'
import * as Y from 'yjs'

import { compareRoundTrip, exportMarkdown, writeMarkdown } from '../src/lib/docSchema'

import { deriveKey } from './auth'
import { createCollabServer } from './service'

const SECRET = 'test-secret'
const DOC = 'room:6f1c0a52-8a51-4f8e-9d55-2f4d2b1f8a10'

interface Version {
  content: string
  actors: string[]
}

/** The backend's side, kept in memory: what was stored, and by whom. */
class FakeBackend {
  state: Buffer | null = null
  content = ''
  versions: Version[] = []
  stores = 0
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
        this.state = Buffer.from(body.state, 'base64')
        if (body.content !== null && body.content !== this.content) {
          this.content = body.content
          this.versions.push({ content: body.content, actors: body.actors })
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

async function setup(seed = '') {
  const backend = new FakeBackend(seed)
  const backendUrl = await backend.listen()
  cleanups.push(() => new Promise<void>((resolve) => backend.server.close(() => resolve())))
  const server = createCollabServer({
    port: 0,
    backendUrl,
    secret: SECRET,
    debounceMs: 50,
    maxDebounceMs: 200,
    retryMs: 50,
    quiet: true,
  })
  await server.listen()
  cleanups.push(() => server.destroy())
  return { backend, server, url: server.webSocketURL, http: server.httpURL }
}

function client(url: string, token: string) {
  const doc = new Y.Doc()
  const socket = new HocuspocusProviderWebsocket({ url })
  let failed = false
  const provider = new HocuspocusProvider({
    websocketProvider: socket,
    name: DOC,
    document: doc,
    token,
    onAuthenticationFailed: () => {
      failed = true
    },
  })
  provider.attach()
  cleanups.push(() => {
    provider.destroy()
    socket.destroy()
  })
  return { doc, provider, failed: () => failed }
}

async function until(check: () => boolean, ms = 5000) {
  const start = Date.now()
  while (!check()) {
    if (Date.now() - start > ms) throw new Error('timed out waiting')
    await new Promise((r) => setTimeout(r, 20))
  }
}

function replace(http: string, body: { content: string; base: string | null; actor: string }) {
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
  it('converts a Markdown document on first open without losing content or adding a version', async () => {
    const { backend, url } = await setup(SEED)
    const a = client(url, ticket('xiaowang'))
    await until(() => a.doc.getXmlFragment('default').length > 0)
    expect(compareRoundTrip(SEED, exportMarkdown(a.doc)).clean).toBe(true)
    await until(() => backend.state !== null)
    expect(backend.versions).toEqual([])
    expect(backend.content).toBe(SEED)
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
})
