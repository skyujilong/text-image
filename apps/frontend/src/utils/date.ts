/**
 * 格式化相对时间
 *
 * - < 1 分钟：显示 "刚刚"
 * - < 1 小时：显示 "X 分钟前"
 * - < 24 小时：显示 "X 小时前"
 * - >= 24 小时：显示 "MM-DD HH:MM"
 *
 * @param isoString ISO 格式时间字符串
 * @returns 格式化后的相对时间字符串
 */
export function formatRelativeTime(isoString: string | null | undefined): string {
  if (!isoString)
    return '—'

  const date = new Date(isoString)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMins = Math.floor(diffMs / 60000)
  const diffHours = Math.floor(diffMs / 3600000)

  if (diffMins < 1)
    return '刚刚'
  if (diffMins < 60)
    return `${diffMins} 分钟前`
  if (diffHours < 24)
    return `${diffHours} 小时前`

  // 超过 24 小时显示 MM-DD HH:MM
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  const hours = String(date.getHours()).padStart(2, '0')
  const minutes = String(date.getMinutes()).padStart(2, '0')
  return `${month}-${day} ${hours}:${minutes}`
}
