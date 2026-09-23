import { z } from 'zod'

export const vuetifyConfig = (state: { errors: any }) => ({
  props: {
    'error-messages': state.errors,
    error: !!state.errors.length,
  },
})

// The backend applies the same rule to every registration entry point.
export const REGEX_USERNAME = /^[a-zA-Z0-9_-]{4,32}$/

export const REGEX_PASSWORD = /^(?=.*[a-zA-Z])(?=.*\d)(?=.*[\x00-\x2F\x3A-\x40\x5B-\x60\x7B-\x7F]).{8,}$/

export const RULE_PASSWORD = z.string().min(8).regex(REGEX_PASSWORD, { message: '密码必须包含字母、数字、特殊字符' })

export const truncateString = (str: string, maxLength: number) => {
  if (str.length <= maxLength) return str
  return str.slice(0, maxLength - 2) + '……'
}
