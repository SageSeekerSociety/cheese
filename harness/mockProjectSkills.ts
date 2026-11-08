export async function listSkillCatalog() {
  return {
    data: [
      { name: 'wolfram', description: '可以算出来的问题交给它' },
      { name: 'documents', description: '读写办公文档' },
    ],
    total: 2,
  }
}
export async function listProjectSkills() {
  return { data: [], total: 0 }
}
export async function getSkillHealth() {
  return { status: 'ok', detail: '' }
}
