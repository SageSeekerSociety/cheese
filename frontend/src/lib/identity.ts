// 登录的人换了一次就 +1。`myHandle()` 读的是存储，不会通知谁；要跟着登录的人变的
// 计算（按人建键的未读），读一下它就会在换人时重算。
import { ref } from 'vue'

export const identityChanges = ref(0)
