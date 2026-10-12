import type { AcceptedDocuments, LegalDocumentFull, LegalDocumentKey, LegalDocumentSummary } from '@/types/apiLegal'

import { axiosRequest } from './axiosHttp'

/** Legal documents ride the primary (legacy) API origin; calls expose only the
 *  payload. This module deals in document keys, versions and consent records —
 *  never in the document text itself. */
export namespace LegalApi {
  export const listDocuments = () =>
    axiosRequest<{ documents: LegalDocumentSummary[] }>({ url: '/legal/documents', method: 'GET' })

  export const getDocument = (document: LegalDocumentKey, version?: string) =>
    axiosRequest<LegalDocumentFull>({
      url: version ? `/legal/documents/${document}/versions/${version}` : `/legal/documents/${document}`,
      method: 'GET',
    })

  export const getPendingConsents = () =>
    axiosRequest<{ pending: LegalDocumentSummary[] }>({ url: '/users/me/consents', method: 'GET' })

  export const acceptDocuments = (documents: AcceptedDocuments) =>
    axiosRequest<{ pending: LegalDocumentSummary[] }>({
      url: '/users/me/consents',
      method: 'POST',
      data: { documents },
    })
}
