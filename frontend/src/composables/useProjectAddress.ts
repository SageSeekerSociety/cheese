// 项目设置里「项目地址」那一栏：改短名。成了就把地址栏里的旧短名换掉，刷新、复制都是
// 新地址；旧地址照样能打开（`lib/addresses`）。
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'

import { setProjectSlug } from '@/api/addresses'
import { t } from '@/i18n'
import { projectSlug, rememberProject } from '@/lib/addresses'

export function useProjectAddress(projectId: () => string) {
  const router = useRouter()
  const slug = ref(projectSlug(projectId()) ?? '')
  const saving = ref(false)
  const error = ref('')

  async function save(next: string) {
    saving.value = true
    error.value = ''
    try {
      const saved = await setProjectSlug(projectId(), next)
      rememberProject(projectId(), saved.slug)
      slug.value = saved.slug
      const here = router.currentRoute.value
      void router.replace({
        name: here.name ?? undefined,
        params: { ...here.params, projectId: saved.slug },
        query: here.query,
      })
      toast(t('work.projectSettings.address.saved'))
    } catch (e) {
      error.value = e instanceof Error ? e.message : t('work.projectSettings.address.failed')
    } finally {
      saving.value = false
    }
  }

  return { slug, saving, error, prefix: `${window.location.host}/projects/`, save }
}
