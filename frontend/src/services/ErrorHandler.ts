import type { BusinessError } from '@/network/types/error'

import { toast } from 'vuetify-sonner'

import { ApiError } from '@/api/http'
import { reportError } from '@/errorReporter'
import { t } from '@/i18n'

// 定义错误处理器类型
export type ErrorHandler<T extends Error = Error> = (error: T) => Promise<boolean> | boolean

// 定义不同类型错误的处理映射
interface ErrorHandlers {
  [errorName: string]: ErrorHandler
}

// 默认错误处理配置
interface ErrorHandlerOptions {
  showToast?: boolean
  defaultMessage?: string
}

class ErrorHandlerService {
  private handlers: ErrorHandlers = {}
  private defaultOptions: ErrorHandlerOptions = {
    showToast: true,
  }

  // 注册错误处理器
  register<T extends Error>(errorName: string, handler: ErrorHandler<T>) {
    this.handlers[errorName] = handler as ErrorHandler
    return this
  }

  // 处理错误
  async handle(error: Error, options?: ErrorHandlerOptions): Promise<boolean> {
    console.error('Error caught:', error)

    const opts = { ...this.defaultOptions, ...options }

    // 服务端给这次回绝起的名字：新 ApiError 在 `code`（错误名字符串），老
    // BusinessError 在 `name`（后端错误名）。哪一代落在注册过的那条上，就按那一条办。
    const refusalName = refusalHandlerName(error)
    if (refusalName && this.handlers[refusalName]) {
      // Not reported: everything with a registered handler is a refusal the
      // product means — no permission, a locked team — which is the system
      // working. Recording those would bury the failures that are not.
      return await this.handlers[refusalName](error)
    }

    // Everything else is reported. These are exactly the failures a person SEES
    // as a red toast and the platform keeps nothing about: the page did not
    // crash, so none of the global hooks (window.error, unhandledrejection,
    // Vue's errorHandler) ever fires. The debugger here is usually an agent, and
    // an agent cannot read a user's console — unreported means it never happened.
    //
    // Dedup lives in the reporter: one per fingerprint per 60s, 50 per session,
    // so an impatient click or a failing poll cannot flood the timeline.
    const fallback = opts.defaultMessage || t('shell.errors.actionFailed')
    reportError(error.message || fallback, error.stack, `ui:${error.name || 'Error'}`)

    // 服务端回绝了这次请求、却没有专属处理器：说服务端那句话，别拿通用文案把它盖掉。
    // 两代都算 —— 新 ApiError 的 `code` 是错误名，老 BusinessError 的 `code` 是数字。
    if (hasServerRefusal(error)) {
      if (opts.showToast) {
        toast.error(error.message || fallback)
      }
      return false
    }

    // 未知错误
    if (opts.showToast) {
      toast.error(fallback)
    }

    return false
  }

  // 包装 async 函数，提供自动错误处理
  async withErrorHandling<T>(fn: () => Promise<T>, options?: ErrorHandlerOptions): Promise<T | undefined> {
    try {
      const result = await fn()
      return result
    } catch (error) {
      await this.handle(error as Error, options)
      return undefined
    }
  }
}

// 服务端给这次回绝起的名字：新 `ApiError` 放在 `code`（错误名字符串），老
// `BusinessError` 放在 `name`（后端错误名）。取到一个就按它找注册过的那条。
function refusalHandlerName(error: Error): string | undefined {
  const code = (error as ApiError).code
  if (typeof code === 'string' && code) return code
  return 'name' in error && error.name ? error.name : undefined
}

// 这次失败是服务端有条有理地回绝的（带着业务 code），不是网络断了也不是未知错误。
// 两代都算：新 `ApiError` 的 `code` 是字符串，老 `BusinessError` 的 `code` 是数字。
function hasServerRefusal(error: Error): boolean {
  if (error instanceof ApiError) return typeof error.code === 'string' && error.code.length > 0
  const code = (error as BusinessError).code
  return typeof code === 'number' && code > 0
}

// 创建单例
const errorHandler = new ErrorHandlerService()

errorHandler.register('TeamLockedError', (error) => {
  // 谁锁着：新 ApiError 把回绝数据放在 `data`（= 后端的 error.data），老的放在 `error.data`。
  const data = (error instanceof ApiError ? error.data : (error as { error?: { data?: unknown } }).error?.data) as
    | { lockingTasks?: string }
    | undefined
  if (data?.lockingTasks) {
    toast.warning(t('shell.errors.teamLockedBy', { tasks: data.lockingTasks }))
  } else {
    toast.warning(t('shell.errors.teamLocked'))
  }
  return false
})

errorHandler.register('AccessDeniedError', (error) => {
  toast.error(t('shell.errors.noPermission'))
  return false
})

errorHandler.register('PermissionDeniedError', (error) => {
  toast.error(t('shell.errors.noPermission'))
  return false
})

export default errorHandler
