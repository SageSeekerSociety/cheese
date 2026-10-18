// 「移动到…」那张框的取数和动作（#3774）：打开时问后端能去哪儿，确认了就挪，挪完把
// 页面带到文档的新地址。框本身（`components/library/DocMoveDialog.vue`）只吃 props。
import type { RouteLocationRaw } from 'vue-router'
import type { MovablePlace } from '../api/teamDocuments'
import type { MovePlace } from '../components/library/DocMoveDialog.vue'

import { computed, reactive } from 'vue'

import { getDocumentAbout } from '../api/projectDocuments'
import { getDocumentPlaces, moveDocument } from '../api/teamDocuments'

import { t } from '@/i18n'

export function useDocumentMove(go: (to: RouteLocationRaw) => void) {
  const state = reactive({
    open: false,
    saving: false,
    error: '',
    documentId: '',
    title: '',
    current: { teamId: 0, projectId: null as string | null },
    places: [] as MovablePlace[],
  })

  const places = computed<MovePlace[]>(() =>
    state.places.map((p) => ({ teamId: p.team_id, name: p.name, personal: p.personal, projects: p.projects }))
  )

  /** 打开框：问这份文档现在在哪、能挪到哪。 */
  async function open(documentId: string) {
    Object.assign(state, { open: true, saving: false, error: '', documentId, title: '', places: [] })
    try {
      const [about, found] = await Promise.all([getDocumentAbout(documentId), getDocumentPlaces(documentId)])
      state.title = about.title || t('work.room.doc.untitled')
      state.current = { teamId: about.team_id, projectId: about.project_id }
      state.places = found.places
    } catch (e) {
      state.error = e instanceof Error ? e.message : t('work.room.doc.moveDialog.failed')
    }
  }

  async function move(to: { teamId: number; projectId: string | null }) {
    state.saving = true
    state.error = ''
    try {
      const moved = await moveDocument(state.documentId, { team_id: to.teamId, project_id: to.projectId })
      state.open = false
      const place = state.places.find((p) => p.team_id === to.teamId)
      go(
        moved.project_id
          ? { name: 'project-document', params: { projectId: moved.project_id, docId: moved.id } }
          : { name: 'TeamsDocument', params: { handle: place?.handle ?? '', docId: moved.id } }
      )
    } catch (e) {
      state.error = e instanceof Error ? e.message : t('work.room.doc.moveDialog.failed')
    } finally {
      state.saving = false
    }
  }

  return { state, places, open, move }
}
