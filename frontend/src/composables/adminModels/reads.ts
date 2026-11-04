// 模型管理页三段**各自的读**：三条接口、三段各自的加载与失败，以及它们共用的那一个
// 窗口。还有审计那一段自己的一格状态（展开了哪几行的「查看改动」）。
//
// 这三条读各拉各的、各失败各的（`load` 只负责模型那一段）不是随手：额度那一段挂了
// 不该把模型表也一起清空，审计读不到不该显示「暂无操作」—— 三种失败在这一页是三句话，
// 由三段各自说，页顶那条横条只说**写**失败。
import type { AuditItem, ModelsListing, ProjectsPayload } from '@/lib/adminModels'

import { ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { AUDIT_LIMIT, message } from './shared'

import { getGatewayAudit, getGatewayModels, getGatewayProjects } from '@/api'

export function useAdminModelsReads() {
  const { t } = useI18n()

  /** 窗口。三段共用。 */
  const days = ref(7)

  const models = ref<ModelsListing | null>(null)
  const projects = ref<ProjectsPayload | null>(null)
  const audit = ref<AuditItem[]>([])

  const loading = ref(true)
  const projectsLoading = ref(false)
  const auditLoading = ref(false)

  /** 主列表（模型）加载失败的原话。页面直接显示它，不另写一句「加载失败」。 */
  const loadError = ref<string | null>(null)
  /** 额度段读失败的原话。和主列表分开：这一段自己拉、也自己失败，读不到时**不能**退化成
   *  一张空表 —— 空表说的是「还没有项目」，而这里发生的是「没读到」。 */
  const projectsError = ref<string | null>(null)
  /** 最近操作段读失败的原话。同上：读不到时不能显示「暂无操作」。 */
  const auditError = ref<string | null>(null)

  /** 审计区展开「查看改动」的行（按下标记）。diff 在子组件 `AdminAuditDiff` 里画。 */
  const auditExpanded = ref<Set<number>>(new Set())

  async function load() {
    loading.value = true
    loadError.value = null
    // 三段同时发、各拉各的、各画各的失败：额度那一段挂了不该把模型表也一起清空，
    // 也不该等模型表读完才开始读。
    void loadProjects()
    void loadAudit()
    try {
      models.value = (await getGatewayModels(days.value)) as unknown as ModelsListing
    } catch (e) {
      models.value = null
      loadError.value = message(e, t('models.page.loadFailed'))
    } finally {
      loading.value = false
    }
  }

  async function loadProjects() {
    projectsLoading.value = true
    projectsError.value = null
    try {
      projects.value = (await getGatewayProjects(days.value)) as unknown as ProjectsPayload
    } catch (e) {
      // 失败**不能**退化成一张空表：空表说的是「还没有项目」，而这里发生的是「没读到」。
      projects.value = null
      projectsError.value = message(e, t('models.page.loadFailed'))
    } finally {
      projectsLoading.value = false
    }
  }

  async function loadAudit() {
    auditLoading.value = true
    auditError.value = null
    try {
      audit.value = ((await getGatewayAudit(AUDIT_LIMIT)) as unknown as { items: AuditItem[] }).items ?? []
    } catch (e) {
      // 同上：读不到时不能显示「暂无操作」，那是把「没读到」说成了「没有」。
      audit.value = []
      auditError.value = message(e, t('models.page.loadFailed'))
    } finally {
      auditLoading.value = false
    }
  }

  function changeWindow(value: number) {
    if (days.value === value) return
    days.value = value
    models.value = null
    projects.value = null
    void load()
  }

  function toggleAuditDiff(index: number) {
    const next = new Set(auditExpanded.value)
    if (next.has(index)) next.delete(index)
    else next.add(index)
    auditExpanded.value = next
  }

  return {
    days,
    models,
    projects,
    audit,
    loading,
    projectsLoading,
    auditLoading,
    loadError,
    projectsError,
    auditError,
    auditExpanded,
    load,
    loadProjects,
    loadAudit,
    changeWindow,
    toggleAuditDiff,
  }
}
