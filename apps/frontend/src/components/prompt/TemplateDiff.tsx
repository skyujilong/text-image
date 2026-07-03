import { diffLines } from 'diff'
import { cn } from '@/lib/utils'

// diff 增/删是通用语义色：集中在此定义（符合 CLAUDE.md「业务语义色集中常量」carve-out），
// 不散落到各处硬编码 green/red。
const DIFF_STYLE = {
  added: 'bg-emerald-500/10 text-emerald-700',
  removed: 'bg-destructive/10 text-destructive',
  common: 'text-muted-foreground',
} as const

interface Props {
  /** 对比基准（内置预设原文）。 */
  before: string
  /** 本 run 实际生效模板。 */
  after: string
  className?: string
}

/** 模板行级 diff：预设原文 vs 本 run 实际模板。相同则显示「未改动」。 */
export default function TemplateDiff({ before, after, className }: Props) {
  if ((before ?? '') === (after ?? '')) {
    return (
      <p className={cn('text-xs text-muted-foreground italic px-2 py-1.5', className)}>
        未改动（使用预设原文）
      </p>
    )
  }

  const parts = diffLines(before ?? '', after ?? '')
  return (
    <div
      className={cn(
        'text-xs font-mono rounded-md border border-border overflow-x-auto',
        className,
      )}
    >
      {parts.flatMap((part, i) => {
        const style = part.added
          ? DIFF_STYLE.added
          : part.removed
            ? DIFF_STYLE.removed
            : DIFF_STYLE.common
        const sign = part.added ? '+' : part.removed ? '-' : ' '
        // 拆行（去掉块尾多余空行），逐行渲染并带前缀符号
        const lines = part.value.replace(/\n$/, '').split('\n')
        return lines.map((line, j) => (
          <div
            key={`${i}-${j}`}
            className={cn('px-2 py-0.5 whitespace-pre-wrap break-words', style)}
          >
            <span className="select-none opacity-50 mr-2">{sign}</span>
            {line || ' '}
          </div>
        ))
      })}
    </div>
  )
}
