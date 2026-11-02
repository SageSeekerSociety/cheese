import type { Block } from '../cx_types'

export interface RecordPart {
  kind: 'text' | 'thinking' | 'tool' | 'output' | 'image' | 'status'
  text?: string
  name?: string
  action?: string
  call_id?: string
  input?: Record<string, unknown>
  preview?: string
  failed?: boolean
  label?: string
  source?: { type: string; sha256?: string; bytes?: number; media_type?: string }
}

export function recordParts(block: Block): RecordPart[] {
  return (block.meta?.parts as RecordPart[] | undefined) ?? []
}

export interface RecordNode {
  block: Block
  children: RecordNode[]
}

/** The platform's own retry of a failed turn: scheduled, started, given up. The
 *  line above the composer says a teammate is waiting on it, and the conversation
 *  says when it was given up; the overview does not tell it a third time. */
const RESUME_EVENTS = new Set(['turn_resume_wait', 'turn_resume_started', 'turn_resume_stopped'])

/** A status series has one current reading in the overview, every fact in raw. */
export function overviewRecords(blocks: Block[]): Block[] {
  const latest = new Map<string, string>()
  for (const block of blocks) {
    if ('run_record' in block) latest.set(String(block.meta?.series_id ?? block.id), block.id)
  }
  return blocks.filter(
    (block) =>
      !('run_record' in block) ||
      (latest.get(String(block.meta?.series_id ?? block.id)) === block.id &&
        !RESUME_EVENTS.has(String(block.meta?.event_type ?? '')))
  )
}

/** Join native parent identities without copying or dropping orphaned records. */
export function recordTree(blocks: Block[]): RecordNode[] {
  const nodes: RecordNode[] = blocks.map((block) => ({
    block: { ...block, meta: { ...block.meta, parts: recordParts(block).map((part) => ({ ...part })) } },
    children: [],
  }))
  const owners = new Map<string, RecordNode>()
  const aliases = new Map<string, string>()
  for (const node of nodes) {
    for (const part of recordParts(node.block)) {
      if (part.kind === 'tool' && part.call_id) owners.set(part.call_id, node)
    }
    const raw = node.block.meta?.record as Record<string, unknown> | undefined
    if (typeof raw?.tool_use_id === 'string' && typeof raw.task_id === 'string')
      aliases.set(raw.task_id, raw.tool_use_id)
    if (typeof raw?.agent_id === 'string' && typeof raw.tool_use_id === 'string')
      aliases.set(raw.agent_id, raw.tool_use_id)
  }
  const roots: RecordNode[] = []
  for (const node of nodes) {
    const parent = node.block.meta?.parent as string | undefined
    const call = recordParts(node.block).find((part) => part.kind === 'output')?.call_id
    const owner = (parent && owners.get(aliases.get(parent) ?? parent)) || (call && owners.get(call))
    if (owner && owner !== node) {
      owner.children.push(node)
      if (recordParts(node.block).some((part) => part.kind === 'output' && part.failed)) {
        for (const part of recordParts(owner.block))
          if (part.kind === 'tool' && part.call_id === call) part.failed = true
      }
    } else roots.push(node)
  }
  return roots
}

/** Signatures and encrypted thinking are transport data, never prose or JSON UI. */
export function displayRecord(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(displayRecord)
  if (!value || typeof value !== 'object') return value
  const record = value as Record<string, unknown>
  const thinking = record.type === 'thinking' || record.type === 'redacted_thinking'
  return Object.fromEntries(
    Object.entries(record)
      .filter(([key]) => !(thinking && (key === 'signature' || key === 'encrypted_content' || key === 'data')))
      .map(([key, item]) => [key, displayRecord(item)])
  )
}
