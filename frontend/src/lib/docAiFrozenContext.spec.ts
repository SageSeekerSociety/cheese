import type { DocAiProposal, DocAiRequest } from './docAiTypes'

import { describe, expect, it } from 'vitest'

import { verifyDocAiFrozenContext, verifyDocAiPreparedContext } from './docAiFrozenContext'

const source = '\uFEFF# 标题\r\n😀重复\r\n😀重复 **原文**\r\n'
function request(): DocAiRequest {
  return {
    request_id: 'r',
    kind: 'propose',
    state: 'succeeded',
    generation: 1,
    proposal_id: 'p',
    answer: 'done',
    error: null,
    frozen_context: {
      question: '  只改第二处\r\n',
      document_id: 'doc',
      base_version: 4,
      source,
      source_hash: '588722fe54d408282f21644ccea050e47e30c24e91bf60e26de199cfe2704d11',
      offset_unit: 'utf8-bytes',
      selection: {
        node_id: 'n',
        start: 25,
        end: 46,
        exact_hash: '9478bcae29f99d669ca5752ffb2bcc8b4cdc875ec24b62caa23327393225bea5',
      },
    },
  }
}
function proposal(): DocAiProposal {
  return {
    proposal_id: 'p',
    request_id: 'r',
    revision: 1,
    state: 'accepted',
    document_id: 'doc',
    base_version: 4,
    selection: {
      node_id: 'n',
      start: 25,
      end: 46,
      exact_hash: '9478bcae29f99d669ca5752ffb2bcc8b4cdc875ec24b62caa23327393225bea5',
    },
    replacement: '',
    answer: 'done',
    accepted_by: 'human',
    accepted_version: 5,
  }
}

describe('frozen document AI context', () => {
  it('keeps the submitted question and the exact second raw selection after acceptance', async () => {
    expect(await verifyDocAiFrozenContext(request(), proposal())).toEqual({
      state: 'verified',
      question: '  只改第二处\r\n',
      original: '😀重复 **原文**',
      scope: 'selection',
      baseVersion: 4,
    })
  })
  it('keeps CRLF and BOM in whole-document asks without inventing a selection', async () => {
    const row = request()
    row.kind = 'ask'
    row.proposal_id = null
    row.frozen_context!.selection = null
    expect(await verifyDocAiFrozenContext(row)).toMatchObject({
      state: 'verified',
      original: source,
      scope: 'document',
    })
  })
  it('preserves a real BOM at the start of the selected bytes', async () => {
    const row = request()
    row.kind = 'ask'
    row.proposal_id = null
    row.frozen_context!.selection = {
      node_id: 'n',
      start: 0,
      end: 3,
      exact_hash: 'f1945cd6c19e56b3c1c78943ef5ec18116907a4ca1efc40a57d48ab1db7adfc5',
    }
    expect(await verifyDocAiFrozenContext(row)).toMatchObject({ state: 'verified', original: '\uFEFF' })
  })
  it('rejects a correctly hashed byte slice whose endpoint is inside an emoji', async () => {
    const row = request()
    row.frozen_context!.selection!.start = 26
    row.frozen_context!.selection!.exact_hash = 'c44561c605b9df2f10fd83cd5fdc3f7086daf92bae004d5af1c69c1df4b5497d'
    expect(await verifyDocAiFrozenContext(row)).toMatchObject({ state: 'invalid' })
  })
  it('marks old responses unavailable instead of filling history from the live document', async () => {
    const row = request()
    delete row.frozen_context
    expect(await verifyDocAiFrozenContext(row, proposal())).toEqual({ state: 'unavailable' })
  })
  it.each([
    [
      'changed full source',
      (r: DocAiRequest) => {
        r.frozen_context!.source += 'changed'
      },
    ],
    [
      'wrong full source hash',
      (r: DocAiRequest) => {
        r.frozen_context!.source_hash = '0'.repeat(64)
      },
    ],
    [
      'wrong selected hash',
      (r: DocAiRequest) => {
        r.frozen_context!.selection!.exact_hash = '0'.repeat(64)
      },
    ],
    [
      'middle of emoji',
      (r: DocAiRequest) => {
        r.frozen_context!.selection!.start = 26
      },
    ],
    [
      'fractional offset',
      (r: DocAiRequest) => {
        r.frozen_context!.selection!.start = 25.5
      },
    ],
    [
      'negative offset',
      (r: DocAiRequest) => {
        r.frozen_context!.selection!.start = -1
      },
    ],
    [
      'out of bounds',
      (r: DocAiRequest) => {
        r.frozen_context!.selection!.end = 999
      },
    ],
    [
      'empty span',
      (r: DocAiRequest) => {
        r.frozen_context!.selection!.end = 25
      },
    ],
    [
      'unsupported offset unit',
      (r: DocAiRequest) => {
        Object.assign(r.frozen_context!, { offset_unit: 'utf16' })
      },
    ],
    [
      'missing proposal selection',
      (r: DocAiRequest) => {
        r.frozen_context!.selection = null
      },
    ],
  ] as const)('does not claim verified original for %s', async (_, mutate) => {
    const row = request()
    mutate(row)
    expect(await verifyDocAiFrozenContext(row)).toEqual({ state: 'invalid', question: '  只改第二处\r\n' })
  })
  it.each([
    [
      'request ID',
      (p: DocAiProposal) => {
        p.request_id = 'other'
      },
    ],
    [
      'proposal ID',
      (p: DocAiProposal) => {
        p.proposal_id = 'other'
      },
    ],
    [
      'document ID',
      (p: DocAiProposal) => {
        p.document_id = 'other'
      },
    ],
    [
      'base version',
      (p: DocAiProposal) => {
        p.base_version = 5
      },
    ],
    [
      'node ID',
      (p: DocAiProposal) => {
        p.selection.node_id = 'other'
      },
    ],
    [
      'selection',
      (p: DocAiProposal) => {
        p.selection.start = 0
      },
    ],
    [
      'selection hash',
      (p: DocAiProposal) => {
        p.selection.exact_hash = '0'.repeat(64)
      },
    ],
  ] as const)('does not pair before and after with a different %s', async (_, mutate) => {
    const p = proposal()
    mutate(p)
    expect(await verifyDocAiFrozenContext(request(), p)).toMatchObject({ state: 'invalid' })
  })
  it('builds the current quote from canonical bytes and the already prepared selection', async () => {
    expect(
      await verifyDocAiPreparedContext(
        { document_id: 'doc', base_version: 4, source, offset_unit: 'utf8-bytes', nodes: [] },
        request().frozen_context!.selection
      )
    ).toEqual({
      state: 'verified',
      original: '😀重复 **原文**',
      scope: 'selection',
      baseVersion: 4,
    })
  })
})
