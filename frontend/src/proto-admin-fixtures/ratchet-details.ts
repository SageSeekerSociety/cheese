/**
 * proto-admin-fixtures/ratchet-details.ts — 每道检查展开后那几条明细。
 *
 * 明细只留前几条：展开那一块是 `max-height: 320px` 的滚动区，前六条就够看出它滚不滚。
 */
export const RATCHET_DETAILS: Record<string, unknown[]> = {
  'scene-ratchet': [
    {
      file: 'src/components/panels/PanelCard.vue',
      grade: 'C',
      reasons: [
        'imports the API layer (frontend/src/api.ts)',
        'reaches the API layer through frontend/src/components/room/composables/useRoomSocket.ts',
        'reaches the API layer through frontend/src/components/room/RoomNotice.vue',
        'reaches the API layer through frontend/src/components/TopicAcceptCard.vue',
        'reaches the API layer through frontend/src/components/common/UserRefLink.vue',
      ],
    },
    {
      file: 'src/components/panels/PanelChanges.vue',
      grade: 'C',
      reasons: [
        'reaches the API layer through frontend/src/composables/usePanelChanges.ts',
        'reaches the API layer through frontend/src/components/panels/PanelChangesView.vue',
      ],
    },
    {
      file: 'src/components/panels/PanelChangesView.vue',
      grade: 'C',
      reasons: ['reaches the API layer through frontend/src/components/panels/preview/RevisionList.vue'],
    },
    {
      file: 'src/components/panels/PanelDoc.vue',
      grade: 'C',
      reasons: [
        'reaches the API layer through frontend/src/composables/usePanelDoc.ts',
        'reaches the API layer through frontend/src/components/panels/PanelDocView.vue',
      ],
    },
    {
      file: 'src/components/panels/PanelDocView.vue',
      grade: 'C',
      reasons: ['reaches the API layer through frontend/src/components/panels/doc/OverviewAuto.vue'],
    },
    {
      file: 'src/components/panels/PanelOverview.vue',
      grade: 'C',
      reasons: [
        'reaches the API layer through frontend/src/components/panels/PanelCard.vue',
        'reaches the API layer through frontend/src/components/panels/PanelProgress.vue',
        'reaches the API layer through frontend/src/components/panels/TaskProgress.vue',
        'reaches the API layer through frontend/src/components/panels/PanelDoc.vue',
      ],
    },
  ],
  'fe-boundary': [
    { file: 'src/components/ArchiveProjectDialog.vue', count: 2 },
    { file: 'src/components/ArtifactManifest.vue', count: 2 },
    { file: 'src/components/ArtifactVersionPreview.vue', count: 2 },
    { file: 'src/components/AttachmentDocThumb.vue', count: 1 },
    { file: 'src/components/AttachmentImage.vue', count: 1 },
    { file: 'src/components/DeviceLiveViewer.vue', count: 1 },
  ],
  'be-contracts': [
    {
      file: 'api-domain-core',
      kept: true,
      name: 'C1 layers: api -> domain -> core, no imports upward',
      stale: 0,
      actual: 27,
      frozen: 27,
    },
    {
      file: 'routes-touch-no-models',
      kept: true,
      name: "C2 routes do not import a domain's models directly",
      stale: 2,
      actual: 53,
      frozen: 55,
    },
    {
      file: 'domains-acyclic',
      kept: true,
      name: 'C3 sibling domains under app.domain are acyclic',
      stale: 1,
      actual: 173,
      frozen: 174,
    },
  ],
  'domain-import-guard': [],
  'file-sizes': [],
  'vue-tsc': [],
  'stylelint-tokens': [
    { file: 'src/components/DeviceLiveViewer.vue', count: 3 },
    { file: 'src/components/TopicMembers.vue', count: 2 },
    { file: 'src/components/TopicSidebar.vue', count: 1 },
    { file: 'src/components/common/AvatarUploader.vue', count: 2 },
    { file: 'src/components/common/OfflineBanner.vue', count: 1 },
    { file: 'src/components/common/UserAvatar.vue', count: 1 },
  ],
  palette: [
    { file: 'src/components/common/AvatarUploader.vue', count: 3 },
    { file: 'src/views/spaces/Index.vue', count: 1 },
  ],
}
