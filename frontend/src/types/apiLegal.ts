// 法律文档接口的形状。原先是 `@/network/api/legal/types`，迁移后集中在这里，
// 供 Consent 那一串消费者与 `@/api/legal` 共用。

export type LegalDocumentKey = 'terms' | 'privacy'

export interface LegalDocumentSummary {
  document: LegalDocumentKey
  title: string
  version: string
  effectiveDate: string
}

export interface LegalDocumentFull extends LegalDocumentSummary {
  current: boolean
  content: string
  sha256: string
}

/** document key → the version the person was shown. */
export type AcceptedDocuments = Partial<Record<LegalDocumentKey, string>>

/**
 * How the consent was given: `checkbox` = ticked before submitting; `dialog` =
 * clicked 同意 in the prompt that appears when submitting without the tick.
 */
export type ConsentMethod = 'checkbox' | 'dialog'
