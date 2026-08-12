/**
 * window.localStorage 浏览器永久缓存
 * @method set 设置永久缓存
 * @method get 获取永久缓存
 * @method remove 移除永久缓存
 * @method clear 移除全部永久缓存
 */
export const Local = {
  // 设置永久缓存
  set(key: string, val: any) {
    window.localStorage.setItem(key, JSON.stringify(val))
  },
  // 获取永久缓存
  get(key: string) {
    const json: any = window.localStorage.getItem(key)
    return JSON.parse(json)
  },
  // 移除永久缓存
  remove(key: string) {
    window.localStorage.removeItem(key)
  },
  // 移除全部永久缓存
  clear() {
    window.localStorage.clear()
  },
}
