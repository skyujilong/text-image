/**
 * 单元 id（章节组机器 id / 排序用）→ 人读中文标签。
 *
 * 后端把连续 N 章合并为一组做剧本化，组 id 复用原 chapter_id 的角色（key / 目录名 / 参数），
 * 格式为零填充的 `ch<起>-<止>`（多章）或 `ch<n>`（单章），例：
 *   ch0001-0003 → 「第1-3章」
 *   ch0007      → 「第7章」
 * 解析失败（不符合该约定的历史 id）原样返回，调用方仍以原始 id 作 key/参数。
 */
export function groupLabel(id: string): string {
  const range = id.match(/^ch(\d+)-(\d+)$/)
  if (range) {
    const start = parseInt(range[1], 10)
    const end = parseInt(range[2], 10)
    return `第${start}-${end}章`
  }
  const single = id.match(/^ch(\d+)$/)
  if (single) {
    return `第${parseInt(single[1], 10)}章`
  }
  return id
}
