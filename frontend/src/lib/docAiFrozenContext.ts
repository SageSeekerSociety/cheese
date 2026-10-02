import type { DocAiDisplayContext, DocAiProposal, DocAiRequest, DocAiSelection, DocAiSource } from './docAiTypes'

const hashPattern = /^[0-9a-f]{64}$/
async function sha256(raw: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new Uint8Array(raw).buffer)
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('')
}
function sameSelection(a: DocAiSelection | null, b: DocAiSelection): boolean {
  return (
    !!a && !!b && a.node_id === b.node_id && a.start === b.start && a.end === b.end && a.exact_hash === b.exact_hash
  )
}

/** Display evidence only: neither this helper nor its output authorizes a write. */
async function original(
  source: Pick<DocAiSource, 'source' | 'document_id' | 'base_version' | 'offset_unit'>,
  selection: DocAiSelection | null,
  sourceHash?: string
): Promise<DocAiDisplayContext> {
  if (
    typeof source.source !== 'string' ||
    typeof source.document_id !== 'string' ||
    !source.document_id ||
    !Number.isSafeInteger(source.base_version) ||
    source.base_version < 1 ||
    source.offset_unit !== 'utf8-bytes'
  )
    return { state: 'invalid' }
  try {
    const raw = new TextEncoder().encode(source.source)
    if (sourceHash !== undefined && (!hashPattern.test(sourceHash) || (await sha256(raw)) !== sourceHash))
      return { state: 'invalid' }
    if (selection === null) {
      return { state: 'verified', original: source.source, scope: 'document', baseVersion: source.base_version }
    }
    if (
      !selection ||
      typeof selection.node_id !== 'string' ||
      !selection.node_id ||
      !Number.isSafeInteger(selection.start) ||
      !Number.isSafeInteger(selection.end) ||
      selection.start < 0 ||
      selection.start >= selection.end ||
      selection.end > raw.length ||
      typeof selection.exact_hash !== 'string' ||
      !hashPattern.test(selection.exact_hash)
    )
      return { state: 'invalid' }
    // Preserve an actual BOM just as Python's utf-8 decode does, and reject
    // either endpoint inside a code point instead of silently replacing it.
    const decode = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true })
    decode.decode(raw.slice(0, selection.start))
    const selected = raw.slice(selection.start, selection.end)
    const text = decode.decode(selected)
    decode.decode(raw.slice(selection.end))
    if ((await sha256(selected)) !== selection.exact_hash) return { state: 'invalid' }
    return { state: 'verified', original: text, scope: 'selection', baseVersion: source.base_version }
  } catch {
    return { state: 'invalid' }
  }
}

export async function verifyDocAiFrozenContext(
  request: DocAiRequest,
  proposal?: DocAiProposal
): Promise<DocAiDisplayContext> {
  const context = request.frozen_context
  if (context === undefined) return { state: 'unavailable' }
  const question = context && typeof context.question === 'string' ? context.question : undefined
  const invalid: DocAiDisplayContext = question === undefined ? { state: 'invalid' } : { state: 'invalid', question }
  if (
    !context ||
    question === undefined ||
    typeof context.source_hash !== 'string' ||
    (request.kind === 'propose' && !context.selection)
  )
    return invalid
  if (
    proposal &&
    (request.kind !== 'propose' ||
      request.request_id !== proposal.request_id ||
      request.proposal_id !== proposal.proposal_id ||
      context.document_id !== proposal.document_id ||
      context.base_version !== proposal.base_version ||
      !sameSelection(context.selection, proposal.selection))
  )
    return invalid
  const verified = await original(context, context.selection, context.source_hash)
  return verified.state === 'verified' ? { ...verified, question } : invalid
}

/** Caller supplies a canonical source and selection that passed prepare's validation. */
export function verifyDocAiPreparedContext(
  source: DocAiSource,
  selection: DocAiSelection | null
): Promise<DocAiDisplayContext> {
  return original(source, selection)
}
