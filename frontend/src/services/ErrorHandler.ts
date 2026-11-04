import { ApiError } from '@/api/http'
import { reportError } from '@/errorReporter'
import { t } from '@/i18n'
import { toast } from '@/lib/toast'

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

    // 服务端给这次回绝起的名字：ApiError 把它放在 `code`（后端错误名）。落在一个
    // 注册过的那条上，就按那一条办。
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

    if (opts.showToast) {
      toast.error(this.sentence(error, fallback))
    }

    return false
  }

  /** 这次失败对人说的那句话：服务端回绝了（又没有专属处理器）就说服务端那句话，别拿
   *  通用文案把它盖掉；网络断了、未知错误说 `fallback`。 */
  sentence(error: Error, fallback: string): string {
    return hasServerRefusal(error) ? error.message || fallback : fallback
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

// 服务端给这次回绝起的名字：`ApiError` 把它放在 `code`（后端错误名）。取到就按它
// 找注册过的那条。
function refusalHandlerName(error: Error): string | undefined {
  const code = (error as ApiError).code
  return typeof code === 'string' && code ? code : undefined
}

// 这次失败是服务端有条有理地回绝的（带着业务 code），不是网络断了也不是未知错误。
function hasServerRefusal(error: Error): boolean {
  return error instanceof ApiError && typeof error.code === 'string' && error.code.length > 0
}

// 创建单例
const errorHandler = new ErrorHandlerService()

errorHandler.register('TeamLockedError', (error) => {
  // 谁锁着：`ApiError` 把回绝数据放在 `data`（= 后端的 error.data）。
  const data = (error as ApiError).data as { lockingTasks?: string } | undefined
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
