/** 角色重要度 → 中文标签。main（含缺省）=主要角色；minor=龙套。 */
export function roleLabel(role: string): string {
  return role === 'minor' ? '龙套' : '主要角色'
}
