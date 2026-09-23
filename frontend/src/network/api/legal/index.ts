import type { AcceptedDocuments, LegalDocumentFull, LegalDocumentKey, LegalDocumentSummary } from './types'

import ApiInstance from '../index'

export namespace LegalApi {
  export const listDocuments = () =>
    ApiInstance.request<{ documents: LegalDocumentSummary[] }>({
      url: '/legal/documents',
      method: 'GET',
    })

  export const getDocument = (document: LegalDocumentKey, version?: string) =>
    ApiInstance.request<LegalDocumentFull>({
      url: version ? `/legal/documents/${document}/versions/${version}` : `/legal/documents/${document}`,
      method: 'GET',
    })

  export const getPendingConsents = () =>
    ApiInstance.request<{ pending: LegalDocumentSummary[] }>({
      url: '/users/me/consents',
      method: 'GET',
    })

  export const acceptDocuments = (documents: AcceptedDocuments) =>
    ApiInstance.request<{ pending: LegalDocumentSummary[] }>({
      url: '/users/me/consents',
      method: 'POST',
      data: { documents },
    })
}
