/**
 * zod 的校验消息本地化，按需装入。
 *
 * 这一份以前写在 `src/main.ts`：应用一启动就 `z.setErrorMap(zodI18nMap)`，于是
 * i18next（约 90 KB ESM）和两份 zod 词表被钉在首屏静态导入图里——而它们只在一条
 * zod 校验失败、要显示那句消息时才用得上。首屏为此多下了第二个 i18n 运行时
 * （界面文案走的是 `src/i18n` 的 vue-i18n，与它无关）。
 *
 * 现在改成第一次要用时再装：`ensureZodErrorMap()` 只装一次，装在哪个 locale 上按
 * 当时的界面语言取，之后跟着 `i18n.global.locale` 走。`src/utils/form.ts` 在每个
 * 用到 zod 的表单都会先导入，那里触发一次即可（见那个文件的注释）。
 *
 * zod-i18n-map 3 不再随包发词表（它的 README 让用 CLI 拷进项目），两份词表放在
 * `./zod-locales/` 下，升级这个包时按同一个 tag 一起换。
 */
import { watch } from 'vue'
import { z } from 'zod'

import i18n from '@/i18n'

let installed: Promise<void> | null = null

/** 装入 zod 的本地化错误映射；重复调用只装一次。 */
export function ensureZodErrorMap(): Promise<void> {
  if (!installed) installed = install()
  return installed
}

async function install(): Promise<void> {
  const [i18next, { zodI18nMap }, en, zh] = await Promise.all([
    import('i18next').then((m) => m.default),
    import('zod-i18n-map'),
    import('./zod-locales/en.json'),
    import('./zod-locales/zh-CN.json'),
  ])
  await i18next.init({
    lng: i18n.global.locale.value,
    resources: {
      'zh-CN': { zod: zh.default },
      en: { zod: en.default },
    },
  })
  // zod 4 的全局入口是 `z.config({ customError })`；`setErrorMap` 还留着但不在这里用。
  z.config({ customError: zodI18nMap })
  // 界面切语言时词表也要切——原来这段 watch 在 main.ts，和 i18next 一起搬到这里。
  watch(i18n.global.locale, (locale) => {
    void i18next.changeLanguage(locale)
  })
}
