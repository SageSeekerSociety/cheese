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
