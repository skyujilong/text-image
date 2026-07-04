import { useState } from 'react'
import { Loader, User } from 'lucide-react'
import { cn } from '@/lib/utils'
import { roleLabel } from '@/lib/characterLabel'
import { api, fileUrl, type CharacterInfo } from '@/api/client'
import { useRunResource } from '@/hooks/useRunResource'

const EMPTY: CharacterInfo[] = []

/** 人物阅读（抽屉内「人物」Tab）：左名册 + 右档案，主从并置。 */
export default function CharacterReader({ runId }: { runId: string }) {
  const { data: characters, loading, error } = useRunResource(runId, api.getRunCharacters, EMPTY)
  const [selected, setSelected] = useState<CharacterInfo | null>(null)

  return (
    <div className="flex-1 min-h-0 flex">
      {/* 名册 */}
      <div className="w-52 shrink-0 border-r border-border overflow-y-auto p-2 flex flex-col gap-1">
        {loading && (
          <div className="flex items-center gap-2 text-xs text-muted-foreground p-2">
            <Loader className="size-3.5 animate-spin" />
            加载人物
          </div>
        )}
        {error && <div className="text-xs text-destructive p-2">{error}</div>}
        {!loading && !error && characters.length === 0 && (
          <div className="text-xs text-muted-foreground p-2">暂无人物档案</div>
        )}
        {characters.map((c) => (
          <button
            key={c.name}
            onClick={() => setSelected(c)}
            className={cn(
              'flex items-center gap-2.5 px-2 py-2 rounded-md text-left transition-colors',
              c.name === selected?.name ? 'bg-accent' : 'hover:bg-accent',
            )}
          >
            {c.portrait_path ? (
              <img
                src={fileUrl(c.portrait_path)}
                alt={c.name}
                className="size-10 rounded object-cover shrink-0 border border-border"
              />
            ) : (
              <div className="size-10 rounded bg-muted flex items-center justify-center shrink-0">
                <User className="size-4 text-muted-foreground" />
              </div>
            )}
            <div className="flex-1 min-w-0">
              <div className="text-sm font-medium truncate">{c.name}</div>
              <div className="text-[10px] text-muted-foreground">{roleLabel(c.role)}</div>
            </div>
          </button>
        ))}
      </div>
      {/* 档案 */}
      <div className="flex-1 min-w-0 overflow-y-auto px-6 py-5">
        {selected ? (
          <div className="max-w-3xl space-y-4">
            {selected.portrait_path && (
              <img
                src={fileUrl(selected.portrait_path)}
                alt={selected.name}
                className="max-h-96 w-auto rounded-md border border-border object-contain"
              />
            )}
            <h3 className="text-lg font-semibold text-foreground">{selected.name}</h3>
            <Field label="角色定位" value={roleLabel(selected.role)} />
            <Field label="人物特征" value={selected.character_trait} />
            <Field label="外观" value={selected.appearance} />
            <Field label="标志服饰" value={selected.outfit} />
            <Field label="视觉特征（英）" value={selected.visual_trait} />
            <Field label="三视图参考" value={selected.tri_view_prompt_cn} />
            <Field label="三视图参考（英）" value={selected.tri_view_prompt} />
          </div>
        ) : (
          <div className="h-full flex items-center justify-center text-sm text-muted-foreground">
            选择左侧人物查看档案
          </div>
        )}
      </div>
    </div>
  )
}

function Field({ label, value }: { label: string; value: string }) {
  if (!value.trim()) return null
  return (
    <div>
      <div className="text-xs font-semibold text-muted-foreground mb-1">{label}</div>
      <div className="text-sm text-foreground leading-6 whitespace-pre-wrap break-words">{value}</div>
    </div>
  )
}
