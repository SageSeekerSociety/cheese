// 复制到剪贴板，并说一声成没成。浏览器不让写（没有权限、不是安全上下文）时说「无法复制」。
import type { RouteLocationRaw, Router } from 'vue-router'

import { toast } from 'vuetify-sonner'

import { t } from '@/i18n'

export async function copyText(text: string, done: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    toast(done)
    return true
  } catch {
    toast.error(t('navigation.copy.failed'))
    return false
  }
}

/** 一个站内地址的完整链接：贴到别处、别人点开能到同一个地方。 */
export function linkOf(router: Router, to: RouteLocationRaw): string {
  return new URL(router.resolve(to).href, window.location.origin).href
}

export function copyLink(link: string): Promise<boolean> {
  return copyText(link, t('navigation.copy.linkCopied'))
}
