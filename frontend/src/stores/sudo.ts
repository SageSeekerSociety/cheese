import { ref } from 'vue'
import { defineStore } from 'pinia'

export const useSudoStore = defineStore('sudo', () => {
  // 存储重试前的路由，用于验证后返回
  const returnPath = ref<string | null>(null)
  // 标记是否需要重试
  const needsRetry = ref(false)
  // 存储待重试操作的信息（操作标识和附带数据）
  const retryOperation = ref<{ opKey: string; opData?: any } | null>(null)
  // 新增：标记是否验证成功
  const isVerified = ref(false)
  // 服务端签发的一次性提权票据。isVerified 只是本地的一个旗子，服务端看不到；
  // 真正让敏感操作放行的是这张票，所以它只在内存里待到被那个操作花掉为止。
  const sudoTicket = ref<string | null>(null)

  // 设置重试状态及待重试操作
  // 这里不打印 params：changePassword 的 opData 里装着用户刚输入的新密码，
  // 打出来就等于把明文密码留在浏览器控制台里。
  const setRetryOperation = (params: { opKey: string; opData?: any; returnPath: string }) => {
    retryOperation.value = { opKey: params.opKey, opData: params.opData }
    returnPath.value = params.returnPath
    needsRetry.value = true
    isVerified.value = false // 重置验证状态
    sudoTicket.value = null
  }

  // 清除重试状态
  const clearRetryState = () => {
    returnPath.value = null
    needsRetry.value = false
    retryOperation.value = null
    isVerified.value = false // 清除验证状态
    sudoTicket.value = null
  }

  // 新增：设置验证成功状态
  const setVerified = (ticket?: string) => {
    isVerified.value = true
    sudoTicket.value = ticket ?? null
  }

  // 取走票据：一张票只能用一次，服务端也这么算，留着只会让下一个操作
  // 拿着一张必然被拒的票去撞墙。
  const consumeTicket = () => {
    const ticket = sudoTicket.value
    sudoTicket.value = null
    return ticket
  }

  return {
    returnPath,
    needsRetry,
    retryOperation,
    isVerified,
    setRetryOperation,
    clearRetryState,
    setVerified,
    consumeTicket,
  }
})
