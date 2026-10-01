export interface ResponseDataType<T = unknown> {
  code: number
  message: string
  data: T
  error?: {
    name: string
    message: string
    data?: any
    /** The catalog key of `message`, when the server said it from the catalog. */
    i18n?: { key: string; params?: Record<string, unknown> }
  }
}
