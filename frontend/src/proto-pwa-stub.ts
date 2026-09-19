/**
 * `virtual:pwa-register` 的替身（临时，供反馈原型精简构建用）。
 *
 * 那个虚模块由 vite-plugin-pwa 提供，而精简构建里没有这个插件——产物是挂在
 * 话题预览上的单个 HTML，没有 service worker 可注册，也不该有。这里只把接口
 * 补齐，让 @/pwa.ts 的调用照常走通、什么都不做。
 */
export interface RegisterSWOptions {
  immediate?: boolean
  onNeedRefresh?: () => void
  onOfflineReady?: () => void
  onRegisteredSW?: (swUrl: string, registration?: ServiceWorkerRegistration) => void
  onRegisterError?: (error: unknown) => void
}

export function registerSW(_options?: RegisterSWOptions): (reloadPage?: boolean) => Promise<void> {
  return async () => {}
}
