// A document's canvases: read one, change it, make or copy one for a document.
import type { CanvasBoard, CanvasChange } from '../types/canvas'

import { request } from './http'

const canvas = (id: string) => `/canvases/${encodeURIComponent(id)}`
const send = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) })

export function getCanvas(id: string): Promise<CanvasBoard> {
  return request(canvas(id))
}

/** Made in order, all or none; answers the canvas as it now is. */
export function changeCanvas(id: string, changes: CanvasChange[]): Promise<CanvasBoard> {
  return request(`${canvas(id)}/changes`, send('POST', { changes }))
}

/** How tall it is drawn in its document; null is the default height. */
export function setCanvasHeight(id: string, height: number | null): Promise<CanvasBoard> {
  return request(canvas(id), send('PATCH', { height }))
}

export function createCanvas(document: string): Promise<CanvasBoard> {
  return request(`/documents/${encodeURIComponent(document)}/canvases`, { method: 'POST' })
}

/** A copy of the canvas as it is now, belonging to `document`. */
export function copyCanvas(id: string, document: string): Promise<CanvasBoard> {
  return request(`${canvas(id)}/copy`, send('POST', { document_id: document }))
}
