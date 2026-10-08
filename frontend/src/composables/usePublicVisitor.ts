// 外壳给公共页注入的真东西（接口见 lib/publicVisitor.ts）：登录状态取
// services/account 里那一份。只在 App.vue 调一次。
import { computed, provide } from 'vue'

import { PUBLIC_VISITOR } from '@/lib/publicVisitor'
import AccountService from '@/services/account'

export function providePublicVisitor(): void {
  provide(PUBLIC_VISITOR, { signedIn: computed(() => AccountService.loggedIn) })
}
