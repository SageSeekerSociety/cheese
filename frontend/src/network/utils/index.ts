import type { AxiosResponse } from 'axios'
import type { ResponseDataType } from '../types'

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL
export const NEW_API_BASE_URL = import.meta.env.VITE_NEW_API_BASE_URL
export const AI_API_BASE_URL = import.meta.env.VITE_AI_API_BASE_URL
// Optional origin for the connector screen WebSocket (the agent 现场 viewer). Empty
// (the default) → the socket uses the page/API origin, unchanged. Set it (build-time)
// to a WebSocket-capable origin, e.g. "https://119pve.ghg.org.cn", when the page origin
// sits behind an edge that strips the WS Upgrade; only its origin is used (the
// /connector path is absolute). Mirrors the backend's CONNECTOR_BASE_OVERRIDES for cli.
export const CONNECTOR_WS_BASE = import.meta.env.VITE_CONNECTOR_WS_BASE

export function isAxiosResponse<T>(res: AxiosResponse<T> | ResponseDataType<T>): res is AxiosResponse<T> {
  return res != null && 'status' in res && typeof res.status !== 'undefined'
}
