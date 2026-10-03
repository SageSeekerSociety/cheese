<script setup lang="ts">
// 发题页「从 PDF 生成」那条路：上传一份 PDF，逐条读出草稿，改完、勾好再一起发出去。
// 确认之后**不跳走**，就地给回执。
import type { PdfPublishAttachmentsData, PdfTaskDraftData } from '@/network/api/tasks/types'
import type { SpaceTeaching } from '@/types'

import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'

import { MAX_DRAFTS, MAX_PDF_BYTES, TASK_SUBMISSION_SCHEMA } from './publishLimits'

import PanelCard from '@/components/spaces/PanelCard.vue'
import { publishDoneRoute, TASK_ROUTE_NAMES } from '@/lib/spaceRouteNames'
import { TasksApi } from '@/network/api/tasks'
import { useSpaceStore } from '@/stores/space'

const props = defineProps<{
  /** 用哪份题目模板读 PDF；-1 是空白模板。 */
  pdfTemplateIndex: number
  /**
   * 发题页这道题自己的「给 AI 队友的指导」覆盖（#944）。与手写那道共用同一份
   * 页面状态：写了就在这一批每一道上整份落下去，六格全空（`undefined`）就不带这项，
   * 让空间（与项目集）的默认生效。
   */
  teaching?: SpaceTeaching
}>()

const route = useRoute()
const { t, locale } = useI18n()
const spaceStore = useSpaceStore()
const spaceId = computed(() => Number(route.params.spaceId))

const fileInput = ref<File | File[] | null>(null)
const parsing = ref(false)
const confirming = ref(false)
const pdfError = ref('')

/** `v-file-input` 单文件/多文件两种返回形状都出现过，统一成一份。 */
const selectedPdf = computed<File | null>(() => {
  if (Array.isArray(fileInput.value)) return fileInput.value[0] ?? null
  return fileInput.value
})

/** 一条待确认的草稿。字段就是真接口给的那几个，外加两样界面要用的：勾选与出处。 */
interface PdfDraft {
  key: string
  picked: boolean
  name: string
  intro: string
  description: string
  categoryId?: number
  /** 出处页。见 `draftPage`。 */
  page: number
  /** 这份草稿正文里带几张图（后端抽出来嵌进正文的那些）。 */
  images: number
}

const drafts = ref<PdfDraft[]>([])
/** 预览回来的另外两样：用了哪份模板、烧了多少 token。 */
const parsedMeta = ref<{ template: string; images: number; tokens: number } | null>(null)
/** 确认发布之后的回执。**不跳走**：就地告诉人刚发了什么、下一步去哪儿看。 */
const receipt = ref<{ count: number; tasks: { id: number; name: string }[] } | null>(null)

/**
 * 预览时服务端**已经落成文件行**、因而可以勾来当附件的那些东西。
 *
 * 这是预览那一步的副作用（`POST /tasks/publish/from-pdf/preview` 把原 PDF 上传、
 * 把抽出的插图登记成 `Attachment`，都挂在调用者名下），id 从响应里的 `attachments`
 * 报回来。**老后端不回这一项**时这里是 `null` —— 那就一颗勾都不画，见下面那个
 * `v-if`，不画一颗点了没用的假勾。
 */
const pdfAttachments = ref<PdfPublishAttachmentsData | null>(null)
/** 两颗勾：默认都勾着（原型也是），但**只有接口真给了东西的那一颗才画**。 */
const attachPdf = ref(true)
const attachImages = ref(true)

const pdfImages = computed(() => pdfAttachments.value?.images ?? [])

/**
 * 勾中的附件 id —— 原 PDF 在前、插图在后，**整份清单会挂到这批题的每一道上**
 * （后端的 `attach_uploaded_to_tasks`）。一样都没勾就是空数组，这时确认请求里
 * **不带 `attachmentIds` 这一项**，行为跟这条路今天完全一样。
 */
const attachmentIdsForPdf = computed<number[]>(() => {
  const a = pdfAttachments.value
  if (!a) return []
  const ids: number[] = []
  if (attachPdf.value && a.pdf) ids.push(a.pdf.id)
  if (attachImages.value) ids.push(...a.images.map((img) => img.id))
  return ids
})

const pickedDrafts = computed(() => drafts.value.filter((d) => d.picked))

const MARKDOWN_IMAGE = /!\[[^\]]*\]\([^)]*\)/g

/** 正文里那张图片标记有几个 —— 后端把抽出的插图换成链接后就长这样。 */
function countImages(markdown: string): number {
  return markdown.match(MARKDOWN_IMAGE)?.length ?? 0
}

/**
 * 草稿的出处页。
 *
 * **真接口没有回传页码**（`PdfTaskDraftData` 只有 name/intro/description/space/
 * categoryId），后端也不说某条草稿来自哪一页。它是**一页一页读的、按页序把草稿
 * 攒起来**（`TaskPdfDraftService.generate_task_payloads_from_pdf` 顺着
 * `page_data` 走），所以「这份预览里的第 N 条」就是后端读的第 N 页 —— 某页解析
 * 失败被跳过时这个号会偏小，那是这条推导唯一会失准的地方，写在题目简介里的标记
 * 也是同一个号。
 */
function draftPage(index: number): number {
  return index + 1
}

/** `templateUsed` 是后端 `pick_template` 的原样返回：没配模板、或索引越界时是
 *  一个 `{}`，所以这里要能落回一句人话。模板元素本身有 `title`/`name` 两种写法
 *  （老页读的是 `title`，`_extract_template_defaults` 还认 `task` 那一层）。 */
function templateLabel(used: unknown): string {
  const tpl = used as { title?: unknown; name?: unknown; task?: { title?: unknown; name?: unknown } } | null
  const title = tpl?.title ?? tpl?.name ?? tpl?.task?.title ?? tpl?.task?.name
  return typeof title === 'string' && title.trim() ? title.trim() : t('spaces.detail.pdfGenerate.blankTemplate')
}

/** 分类名。草稿带回的是 `categoryId`，名字要自己从这块板的分类里换。 */
function categoryLabel(id?: number): string {
  if (id === undefined) return t('spaces.detail.pdfGenerate.noCategory')
  return spaceStore.categories.find((c) => c.id === id)?.name ?? t('spaces.detail.pdfGenerate.categoryFallback', { id })
}

/** 失败时给人看的话，尽量用后端自己的措辞（业务错误都在 `response.data.message`）。 */
function failureText(error: unknown): string {
  const e = error as { response?: { data?: { message?: string } }; message?: string }
  return e?.response?.data?.message || e?.message || t('spaces.detail.pdfGenerate.unknownError')
}

function isPdf(file: File): boolean {
  return file.type === 'application/pdf' || /\.pdf$/i.test(file.name)
}

function resetPdf() {
  fileInput.value = null
  drafts.value = []
  parsedMeta.value = null
  pdfAttachments.value = null
  attachPdf.value = true
  attachImages.value = true
  receipt.value = null
  pdfError.value = ''
}

async function parsePdf() {
  const id = spaceStore.currentSpaceId
  const file = selectedPdf.value
  pdfError.value = ''
  receipt.value = null

  if (!id) {
    pdfError.value = t('spaces.detail.pdfGenerate.error.spaceNotReady')
    return
  }
  if (!file) {
    pdfError.value = t('spaces.detail.pdfGenerate.error.noFile')
    return
  }
  if (!isPdf(file)) {
    pdfError.value = t('spaces.detail.pdfGenerate.error.notPdf')
    return
  }
  if (file.size > MAX_PDF_BYTES) {
    pdfError.value = t('spaces.detail.pdfGenerate.error.tooLarge', { size: Math.round(file.size / 1024 / 1024) })
    return
  }

  parsing.value = true
  drafts.value = []
  parsedMeta.value = null
  pdfAttachments.value = null
  attachPdf.value = true
  attachImages.value = true
  try {
    const { data } = await TasksApi.previewFromPdf({
      spaceId: id,
      file,
      templateIndex: props.pdfTemplateIndex,
      maxTasks: MAX_DRAFTS,
    })

    if (!data.drafts?.length) {
      pdfError.value = t('spaces.detail.pdfGenerate.error.noDrafts', { tokens: data.tokenUsed ?? 0 })
      return
    }

    drafts.value = data.drafts.map((d, index) => ({
      key: `${index}-${d.name || 'draft'}`,
      picked: true,
      name: d.name,
      intro: d.intro,
      description: d.description,
      categoryId: d.categoryId,
      page: draftPage(index),
      images: countImages(d.description),
    }))
    parsedMeta.value = {
      template: templateLabel(data.templateUsed),
      images: drafts.value.reduce((n, d) => n + d.images, 0),
      tokens: data.tokenUsed ?? 0,
    }
    // 服务端顺带落好的附件行。老后端没有这一项时留 null —— 那时页面上不画勾，
    // 并且说明是哪一样带不了。
    pdfAttachments.value = data.attachments ?? null
  } catch (error) {
    pdfError.value = t('spaces.detail.pdfGenerate.error.parseFailed', { reason: failureText(error) })
  } finally {
    parsing.value = false
  }
}

/** 出处标记。题目模型里没有「来源」这一列，也不给它加 —— 标记写进**简介**：
 *  简介会跟着这道题一路走，审核队列那一行显示的就是它。 */
const ORIGIN_EXAMPLE = '【PDF · 第 N 页】' // i18n-data: 写进题目简介的固定标记，界面上原样展示它长什么样

function originTag(page: number): string {
  return `【PDF · 第 ${page} 页】` // i18n-data: 写进题目简介的固定标记，model.ts 的 ORIGIN_PREFIX 按它剥离
}

function toDraftPayload(draft: PdfDraft): PdfTaskDraftData {
  const payload: PdfTaskDraftData = {
    name: draft.name.trim(),
    intro: `${originTag(draft.page)}${draft.intro.trim()}`,
    description: draft.description.trim(),
    space: spaceId.value,
  }
  if (draft.categoryId !== undefined) payload.categoryId = draft.categoryId
  return payload
}

async function confirmPdf() {
  const picked = pickedDrafts.value
  const id = spaceStore.currentSpaceId
  if (!picked.length || !id) return

  confirming.value = true
  pdfError.value = ''
  // 勾中的那些文件。一样都没勾就是空数组 —— 那时**这一项不发给后端**，跑的还是这条
  // 路今天那份参数（后端读到没有 `attachmentIds` 就一道题都不挂材料）。
  const ids = attachmentIdsForPdf.value
  try {
    const { data } = await TasksApi.confirmFromPdf({
      drafts: picked.map(toDraftPayload),
      taskOptions: {
        // 这是**每道题共用**的那一半参数：后端把它和每条草稿合起来
        // （`_apply_pdf_task_options`），name/intro/description 三件逐条被草稿里那份
        // 覆盖（出处标记就在各自那份 intro 里），其余字段原样落下去。
        // 老页那条路（`buildTaskOptions`）报的也是这一份形状，这里跟它同一口径。
        name: picked[0].name.trim(),
        description: picked[0].description,
        submitterType: 'USER',
        resubmittable: true,
        editable: true,
        defaultDeadline: 30,
        deadline: null,
        space: id,
        // 提交表单这一项**是真会落库的**，不是占位：`_create_task_entity` 的两种
        // 入参形状（Pydantic 与这里的 dict）都在读它，最后一起走 `replace_schema`
        // 写进 `task_submission_schema`（`routes/tasks.py` 那行注释：「发布页总会
        // 带上这张表（至少一个『提交文件』项）。建题时不写，题目的提交页就一个
        // 输入项都没有」）。详情接口把它读回来给 `Submit.vue` 出表单。老页那条路
        // （`buildTaskOptions`）报的也是同一份。
        submissionSchema: TASK_SUBMISSION_SCHEMA,
        // 上面那两颗勾勾中的文件：**每一道**都挂同一份（后端
        // `attach_uploaded_to_tasks`）。没勾就整项不出现，这条路的形状与从前一致。
        ...(ids.length ? { attachmentIds: ids } : {}),
        // 发题页那道题的「给 AI 队友的指导」覆盖（#944）：写了就整份落到这一批
        // 每一道上；六格全空就不带这项，让空间（与项目集）的默认生效 —— 与手写那道
        // 同一个判据（`teachingPayload`）。
        ...(props.teaching ? { teaching: props.teaching } : {}),
      },
    })

    receipt.value = {
      count: data.count || data.tasks.length,
      tasks: data.tasks.map((t) => ({ id: t.id, name: t.name })),
    }
    drafts.value = []
    parsedMeta.value = null
  } catch (error) {
    pdfError.value = t('spaces.detail.pdfGenerate.error.publishFailed', { reason: failureText(error) })
  } finally {
    confirming.value = false
  }
}
</script>

<template>
  <div class="pub__pdf">
    <!-- 回执：确认之后就地给，不跳走 —— 队列是「先审自己的、再按提交时间」排的，
         刚发的落在靠后，跳过去反而看不见自己刚做了什么。 -->
    <PanelCard v-if="receipt" :title="t('spaces.detail.pdfGenerate.receipt.title')" data-testid="pdf-receipt">
      <div class="pdf__done">
        <v-icon icon="mdi-check-circle-outline" size="22" color="success" />
        <div>
          <i18n-t scope="global" keypath="spaces.detail.pdfGenerate.receipt.body" tag="span">
            <template #count
              ><b>{{ receipt.count }}</b></template
            >
            <template #queue
              ><b>{{ t('spaces.detail.publishTask.pendingQueue') }}</b></template
            >
          </i18n-t>
          <i18n-t scope="global" keypath="spaces.detail.pdfGenerate.receipt.hint" tag="div" class="pdf__done-hint">
            <template #marker
              ><code>{{ ORIGIN_EXAMPLE }}</code></template
            >
          </i18n-t>
        </div>
      </div>
      <ul class="pdf__made">
        <li v-for="task in receipt.tasks" :key="task.id">
          <router-link :to="{ name: TASK_ROUTE_NAMES.detail, params: { spaceId, taskId: String(task.id) } }">
            {{ task.name }}
          </router-link>
        </li>
      </ul>
      <div class="pdf__actions">
        <v-btn variant="text" @click="resetPdf">{{ t('spaces.detail.pdfGenerate.receipt.again') }}</v-btn>
        <v-spacer />
        <v-btn variant="tonal" :to="publishDoneRoute(spaceId)">{{
          t('spaces.detail.pdfGenerate.receipt.viewMine')
        }}</v-btn>
        <v-btn color="primary" variant="flat" :to="{ name: 'SpacesDetailAuditTasks', params: { spaceId } }">
          {{ t('spaces.detail.pdfGenerate.receipt.toQueue') }}
        </v-btn>
      </div>
    </PanelCard>

    <template v-else>
      <PanelCard
        :title="t('spaces.detail.pdfGenerate.upload.title')"
        :subtitle="t('spaces.detail.pdfGenerate.upload.subtitle', { max: MAX_DRAFTS })"
      >
        <v-file-input
          v-model="fileInput"
          accept=".pdf,application/pdf"
          :label="t('spaces.detail.publishTask.quick.uploadLabel')"
          variant="outlined"
          density="comfortable"
          clearable
          prepend-icon=""
          hide-details="auto"
          :disabled="parsing || confirming"
          data-testid="pdf-file"
        >
          <template #prepend>
            <v-icon color="primary" class="mr-2">mdi-upload</v-icon>
          </template>
        </v-file-input>

        <i18n-t scope="global" keypath="spaces.detail.pdfGenerate.upload.hint" tag="p" class="pdf__hint">
          <template #notPublished
            ><b>{{ t('spaces.detail.pdfGenerate.upload.notPublished') }}</b></template
          >
        </i18n-t>

        <!-- 结果区：真接口真的会报回来的三件事，外加一句「草稿还不是题目」。 -->
        <div v-if="parsedMeta" class="pdf__meta" data-testid="pdf-meta">
          <i18n-t
            scope="global"
            keypath="spaces.detail.pdfGenerate.meta.template"
            tag="span"
            data-testid="pdf-template"
          >
            <template #name
              ><b>{{ parsedMeta.template }}</b></template
            >
          </i18n-t>
          <i18n-t scope="global" keypath="spaces.detail.pdfGenerate.meta.images" tag="span" data-testid="pdf-images">
            <template #n
              ><b>{{ parsedMeta.images }}</b></template
            >
          </i18n-t>
          <i18n-t scope="global" keypath="spaces.detail.pdfGenerate.meta.tokens" tag="span" data-testid="pdf-tokens">
            <template #n
              ><b>{{ parsedMeta.tokens.toLocaleString(locale) }}</b></template
            >
          </i18n-t>
          <i18n-t scope="global" keypath="spaces.detail.pdfGenerate.meta.warn" tag="span" class="pdf__meta-warn">
            <template #notYet
              ><b>{{ t('spaces.detail.pdfGenerate.meta.notYet') }}</b></template
            >
          </i18n-t>
        </div>

        <div class="pdf__actions">
          <v-btn
            color="primary"
            variant="flat"
            :loading="parsing"
            :disabled="!selectedPdf || confirming"
            @click="parsePdf"
          >
            {{ t('spaces.detail.pdfGenerate.parse') }}
          </v-btn>
          <i18n-t scope="global" keypath="spaces.detail.pdfGenerate.parseNote" tag="span" class="pdf__actions-note">
            <template #param><code>?templateId=</code></template>
            <template #max>{{ MAX_DRAFTS }}</template>
          </i18n-t>
        </div>
      </PanelCard>

      <v-alert
        v-if="pdfError"
        data-testid="pdf-error"
        type="error"
        variant="tonal"
        density="comfortable"
        class="pdf__error"
      >
        {{ pdfError }}
      </v-alert>

      <PanelCard
        v-if="drafts.length"
        :title="t('spaces.detail.pdfGenerate.drafts.title')"
        :subtitle="
          t('spaces.detail.pdfGenerate.drafts.subtitle', { picked: pickedDrafts.length, total: drafts.length })
        "
      >
        <ul class="pdf__list" data-testid="pdf-drafts">
          <li v-for="d in drafts" :key="d.key" class="pdf__row" :class="{ 'pdf__row--off': !d.picked }">
            <v-checkbox
              v-model="d.picked"
              density="compact"
              hide-details
              class="pdf__pick"
              :aria-label="t('spaces.detail.pdfGenerate.drafts.pick', { name: d.name })"
            />
            <div class="pdf__body">
              <v-text-field
                v-model="d.name"
                autocomplete="off"
                :label="t('spaces.detail.pdfGenerate.drafts.name')"
                density="compact"
                variant="outlined"
                hide-details
                class="pdf__title"
              />
              <v-textarea
                v-model="d.description"
                autocomplete="off"
                :label="t('spaces.detail.pdfGenerate.drafts.description')"
                density="compact"
                variant="outlined"
                hide-details
                rows="3"
                auto-grow
              />
              <!-- 简介不给人改（它在确认时被加上出处标记），但发出去的就是它，
                   所以摆出来让人看得见。 -->
              <p class="pdf__intro">{{ t('spaces.detail.pdfGenerate.drafts.intro', { intro: d.intro }) }}</p>
              <div class="pdf__tags">
                <v-chip size="x-small" label variant="text">{{ categoryLabel(d.categoryId) }}</v-chip>
                <v-chip size="x-small" label variant="text" data-testid="draft-origin">
                  {{ t('spaces.detail.pdfGenerate.drafts.page', { page: d.page }) }}
                </v-chip>
                <v-chip v-if="d.images" size="x-small" label variant="tonal" color="warning">
                  {{ t('spaces.detail.pdfGenerate.drafts.images', { n: d.images }) }}
                </v-chip>
              </div>
            </div>
          </li>
        </ul>

        <p class="pdf__note-line">
          {{ t('spaces.detail.pdfGenerate.drafts.imagesNote') }}
        </p>

        <!-- 附带给领取者：原型那两颗勾。**勾只画接口真落成了文件行的那些**，
             哪一样没有就不画哪一颗、并说明为什么（见 `pdfAttachments` 的注释）。 -->
        <div class="pdf__attach" data-testid="pdf-attachments">
          <div class="pdf__attach-head">
            <b>{{ t('spaces.detail.pdfGenerate.attach.title') }}</b>
            <i18n-t
              scope="global"
              keypath="spaces.detail.pdfGenerate.attach.count"
              tag="span"
              class="pdf__attach-note"
              data-testid="pdf-attach-count"
            >
              <template #n>{{ attachmentIdsForPdf.length }}</template>
              <template #every
                ><b>{{ t('spaces.detail.pdfGenerate.attach.every') }}</b></template
              >
            </i18n-t>
          </div>
          <div class="pdf__attach-row">
            <v-checkbox
              v-if="pdfAttachments?.pdf"
              v-model="attachPdf"
              :label="t('spaces.detail.pdfGenerate.attach.pdf')"
              density="compact"
              hide-details
              :disabled="confirming"
              data-testid="pdf-attach-pdf"
            />
            <v-checkbox
              v-if="pdfImages.length"
              v-model="attachImages"
              :label="t('spaces.detail.pdfGenerate.attach.images', { n: pdfImages.length })"
              density="compact"
              hide-details
              :disabled="confirming"
              data-testid="pdf-attach-images"
            />
          </div>
          <p v-if="pdfAttachments?.pdf" class="pdf__attach-file" data-testid="pdf-attach-pdf-file">
            {{ t('spaces.detail.pdfGenerate.attach.pdfFile', { name: pdfAttachments.pdf.name }) }}
          </p>
          <p v-if="pdfImages.length" class="pdf__attach-file" data-testid="pdf-attach-image-files">
            {{
              t('spaces.detail.pdfGenerate.attach.imageFiles', {
                names: pdfImages.map((i) => i.name).join(t('spaces.detail.pdfGenerate.attach.separator')),
              })
            }}
          </p>
          <p v-if="!pdfAttachments?.pdf" class="pdf__attach-why" data-testid="pdf-attach-pdf-why">
            {{ t('spaces.detail.pdfGenerate.attach.noPdf') }}
          </p>
          <p v-if="!pdfImages.length" class="pdf__attach-why" data-testid="pdf-attach-images-why">
            {{ t('spaces.detail.pdfGenerate.attach.noImages') }}
          </p>
        </div>

        <div class="pdf__actions">
          <i18n-t scope="global" keypath="spaces.detail.pdfGenerate.confirmNote" tag="span" class="pdf__actions-note">
            <template #n>{{ pickedDrafts.length }}</template>
            <template #queue
              ><b>{{ t('spaces.detail.publishTask.pendingQueue') }}</b></template
            >
          </i18n-t>
          <v-spacer />
          <v-btn variant="text" :disabled="confirming" @click="resetPdf">{{
            t('spaces.detail.pdfGenerate.cancel')
          }}</v-btn>
          <v-btn
            color="primary"
            variant="flat"
            :loading="confirming"
            :disabled="!pickedDrafts.length"
            @click="confirmPdf"
          >
            {{ t('spaces.detail.pdfGenerate.confirm', { n: pickedDrafts.length }) }}
          </v-btn>
        </div>
      </PanelCard>
    </template>
  </div>
</template>

<style scoped lang="scss">
.pub__pdf {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* ---- 从 PDF 生成 ---- */

.pdf__hint {
  margin: 4px 0 0;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.8rem;
  line-height: 1.7;
}

.pdf__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  align-items: center;
  margin-top: 14px;
  padding-top: 12px;
  color: rgba(var(--v-theme-on-surface), 0.7);
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  font-size: 0.78rem;
}

.pdf__meta-warn {
  color: rgb(var(--v-theme-warning));
}

.pdf__error {
  margin: 0;
}

.pdf__done {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  font-size: 0.86rem;
  line-height: 1.7;
}

.pdf__done-hint {
  margin-top: 4px;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.78rem;
}

.pdf__made {
  padding-left: 18px;
  margin: 12px 0 0;
  font-size: 0.84rem;
  line-height: 1.9;
}

.pdf__list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 0;
  margin: 0;
  list-style: none;
}

.pdf__row {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  padding: 12px;
  background: rgba(var(--v-theme-on-surface), 0.03);
  border-radius: var(--radius-md);
}

.pdf__row--off {
  opacity: 0.5;
}

.pdf__pick {
  flex: 0 0 auto;
  margin-top: 2px;
}

.pdf__body {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.pdf__title :deep(input) {
  font-weight: 600;
}

.pdf__intro {
  margin: 0;
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 0.76rem;
  line-height: 1.6;
}

.pdf__tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}

.pdf__note-line {
  margin: 12px 0 0;
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 0.76rem;
  line-height: 1.7;
}

.pdf__attach {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}

.pdf__attach-head {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: baseline;
  justify-content: space-between;
  font-size: 0.84rem;
}

.pdf__attach-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-top: 4px;
}

.pdf__attach-note {
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.76rem;
}

.pdf__attach-file {
  margin: 0;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.76rem;
  line-height: 1.7;
}

.pdf__attach-why {
  margin: 4px 0 0;
  color: rgba(var(--v-theme-on-surface), 0.72);
  font-size: 0.76rem;
  line-height: 1.7;
}

.pdf__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}

.pdf__actions-note {
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.78rem;
}
</style>
