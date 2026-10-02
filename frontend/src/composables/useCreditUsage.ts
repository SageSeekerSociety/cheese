// 芝士额度页的取数：个人页读自己的，团队页读这个团队的。只有一次读，失败给原话。
import type { CreditUsage } from '@/lib/creditUsage'

import { ref } from 'vue'
import { useI18n } from 'vue-i18n'

export function useCreditUsage(fetch: () => Promise<CreditUsage>) {
  const { t } = useI18n()
  const usage = ref<CreditUsage | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function load() {
    loading.value = true
    error.value = null
    try {
      usage.value = await fetch()
    } catch (e) {
      usage.value = null
      error.value = e instanceof Error && e.message ? e.message : t('usage.loadFailed')
    } finally {
      loading.value = false
    }
  }

  return { usage, loading, error, load }
}
