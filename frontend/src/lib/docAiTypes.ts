export interface DocAiSelection {
  node_id: string
  start: number
  end: number
  exact_hash: string
}

export interface DocAiSource {
  document_id: string
  base_version: number
  source: string
  offset_unit: 'utf8-bytes'
  nodes: { node_id: string; start: number; end: number }[]
}

export interface DocAiInput {
  operation_id: string
  kind: 'ask' | 'propose'
  question: string
  document_id: string
  base_version: number
  selection?: DocAiSelection
}

export type DocAiState = 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled'
export interface DocAiReceipt {
  request_id: string
  state: DocAiState
}
export interface DocAiRequest extends DocAiReceipt {
  kind: 'ask' | 'propose'
  generation: number
  proposal_id: string | null
  answer: string | null
  error: string | null
}
export interface DocAiProposal {
  proposal_id: string
  request_id: string
  revision: number
  state: 'pending' | 'accepted'
  document_id: string
  base_version: number
  selection: DocAiSelection
  replacement: string
  answer: string
  accepted_by: string | null
  accepted_version: number | null
}
export interface DocAiAccept {
  operation_id: string
  expected_version: number
  revision: number
}
export interface DocAiAccepted {
  proposal_id: string
  revision: number
  document_id: string
  content: string
  content_hash: string
  doc_version: number
  operation_id: string
  accepted_by: string
}

export interface DocAiCard {
  request: DocAiRequest
  proposal?: DocAiProposal
}
