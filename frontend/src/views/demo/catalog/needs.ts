/**
 * 一格最常要的那一样环境：Vuetify。它自己在场的那几件（面板页签条、面板那三格）都
 * 写 `needs: UI`，免得同一个数组抄三遍。
 *
 * 别的环境一件就是一个字面量写明（`['vuetify', 'router']`），不从这里取：`needs`
 * 是给人读的，多一层常量的间接只会让读者多跳一次。
 */
import type { CatalogNeed } from './types'

export const UI: CatalogNeed[] = ['vuetify']
