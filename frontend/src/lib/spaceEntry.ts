/** 进一个空间落在哪：它的题目列表。 */
export function spaceEntryRoute(space: { id: number }): {
  name: string
  params: { spaceId: number }
} {
  return { name: 'SpacesDetailTasksList', params: { spaceId: space.id } }
}
