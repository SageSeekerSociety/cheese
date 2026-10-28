import type { ProjectDocument } from '../api/projectDocuments'

import { defineComponent, h, ref } from 'vue'
import { cleanup, render, waitFor } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { libraryFile, listedDocument, servesLibrary } from '../test/fakeLibrary'

const FILES = [
  libraryFile('课程大纲.pdf', { modified: Date.parse('2026-10-21T10:00:00Z') / 1000 }),
  libraryFile('成绩.csv', { modified: Date.parse('2026-10-19T10:00:00Z') / 1000 }),
]

function doc(id: string, title: string, updated: string): ProjectDocument {
  return {
    id,
    team_id: 3,
    project_id: null,
    kind: 'doc',
    title,
    doc_version: 1,
    author: 'alice',
    created_by: 'alice',
    created_at: updated,
    updated_at: updated,
  } as ProjectDocument
}

const DOCS = [doc('d1', '组会记录', '2026-10-22T10:00:00Z'), doc('d2', '读书笔记', '2026-10-20T10:00:00Z')]

vi.mock('../lib/libraryApi', () => ({ listProjectLibrary: servesLibrary(() => FILES) }))
vi.mock('../api/teamDocuments', () => ({
  listTeamDocuments: async () => ({ data: DOCS.map(listedDocument), next: null }),
}))
vi.mock('../api/projectDocuments', () => ({ listProjectDocuments: async () => ({ data: [], next: null }) }))

import { useLibraryPages } from './useLibraryPages'

function names(rows: { file?: { path: string }; doc?: { title: string | null } }[]) {
  return rows.map((row) => row.file?.path ?? row.doc?.title)
}

function mountPages(q: string) {
  let pages!: ReturnType<typeof useLibraryPages>
  render(
    defineComponent({
      setup() {
        pages = useLibraryPages(
          () => ({ team: 3 }),
          () => ({ dir: '', q, kind: 'all' }),
          ref(null)
        )
        return () => h('div')
      },
    })
  )
  return () => pages
}

afterEach(cleanup)

describe('a team library', () => {
  it('lists the team documents with its files, the latest changed first', async () => {
    const pages = mountPages('')
    await waitFor(() => expect(names(pages().rows.value)).toEqual(['组会记录', '课程大纲.pdf', '读书笔记', '成绩.csv']))
  })

  it('finds documents by title when searching', async () => {
    const pages = mountPages('笔记')
    await waitFor(() => expect(pages().exhausted.value).toBe(true))
    expect(names(pages().rows.value)).toEqual(['读书笔记'])
  })
})
