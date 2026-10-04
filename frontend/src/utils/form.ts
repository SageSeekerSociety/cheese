import { ensureZodErrorMap } from '@/lib/zodErrorMap'

// zod 的校验消息本地化（i18next + zod-i18n-map）按需装入，不再由首屏带进来。
// 每个会跑 zod 校验的表单都先导入这个模块，所以这一次动态导入和表单自己的 chunk
// 一起走，等到用户提交时早就装好了。见 lib/zodErrorMap.ts。
void ensureZodErrorMap().catch(() => {
  // 装不上就退回 zod 自带的英文消息——校验照常能跑，不拦住任何表单。
})

export const vuetifyConfig = (state: { errors: any }) => ({
  props: {
    'error-messages': state.errors,
    error: !!state.errors.length,
  },
})

// The backend applies the same rule to every registration entry point.
export const REGEX_USERNAME = /^[a-zA-Z0-9_-]{4,32}$/

export const REGEX_PASSWORD = /^(?=.*[a-zA-Z])(?=.*\d)(?=.*[\x00-\x2F\x3A-\x40\x5B-\x60\x7B-\x7F]).{8,}$/

export const truncateString = (str: string, maxLength: number) => {
  if (str.length <= maxLength) return str
  return str.slice(0, maxLength - 2) + '……'
}
