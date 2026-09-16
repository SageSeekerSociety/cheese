import type { BusinessError } from '@/network/types/error'

import { toast } from 'vuetify-sonner'

import { reportError } from '@/errorReporter'

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
    defaultMessage: '操作失败',
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

    // 判断是否为业务错误
    if ('name' in error && error.name && this.handlers[error.name]) {
      // Not reported: everything with a registered handler is a refusal the
      // product means — no permission, a locked team — which is the system
      // working. Recording those would bury the failures that are not.
      return await this.handlers[error.name](error)
    }

    // Everything else is reported. These are exactly the failures a person SEES
    // as a red toast and the platform keeps nothing about: the page did not
    // crash, so none of the global hooks (window.error, unhandledrejection,
    // Vue's errorHandler) ever fires. The debugger here is usually an agent, and
    // an agent cannot read a user's console — unreported means it never happened.
    //
    // Dedup lives in the reporter: one per fingerprint per 60s, 50 per session,
    // so an impatient click or a failing poll cannot flood the timeline.
    reportError(error.message || opts.defaultMessage || '操作失败', error.stack, `ui:${error.name || 'Error'}`)

    // 如果是业务错误但没有特定处理器
    if ('code' in error && (error as BusinessError).code) {
      if (opts.showToast) {
        toast.error(error.message || opts.defaultMessage || '操作失败')
      }
      return false
    }

    // 未知错误
    if (opts.showToast) {
      toast.error(opts.defaultMessage || '操作失败')
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

// 创建单例
const errorHandler = new ErrorHandlerService()

errorHandler.register('TeamLockedError', (error) => {
  if (error instanceof Error && 'error' in error && (error as any).error?.data) {
    const data = (error as any).error.data
    toast.warning(`团队已锁定：参与了任务 ${data.lockingTasks}`)
  } else {
    toast.warning('团队已锁定，无法修改成员')
  }
  return false
})

errorHandler.register('AccessDeniedError', (error) => {
  toast.error('您没有权限执行此操作')
  return false
})

errorHandler.register('PermissionDeniedError', (error) => {
  toast.error('您没有权限执行此操作')
  return false
})

export default errorHandler
