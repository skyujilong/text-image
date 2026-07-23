import { useState } from 'react'
import { ImageOff, Loader, MapPin } from 'lucide-react'
import { cn } from '@/lib/utils'
import { api, fileUrl, type SceneInfo } from '@/api/client'
import { useRunResource } from '@/hooks/useRunResource'

const EMPTY: SceneInfo[] = []

/** 场景阅读（抽屉内「场景」Tab）：左地点册 + 右档案，主从并置。只读展示收敛后的地点清单 + 空景背景板。 */
export default function SceneReader({ runId }: { runId: string }) {
  const { data: scenes, loading, error } = useRunResource(runId, api.getRunScenes, EMPTY)
  const [selected, setSelected] = useState<SceneInfo | null>(null)

  return (
    <div className="flex-1 min-h-0 flex">
      {/* 地点册 */}
      <div className="w-52 shrink-0 border-r border-border overflow-y-auto p-2 flex flex-col gap-1">
        {loading && (
          <div className="flex items-center gap-2 text-xs text-muted-foreground p-2">
            <Loader className="size-3.5 animate-spin" />
            加载场景
          </div>
        )}
        {error && <div className="text-xs text-destructive p-2">{error}</div>}
        {!loading && !error && scenes.length === 0 && (
          <div className="text-xs text-muted-foreground p-2">暂无场景资产</div>
        )}
        {scenes.map((s) => (
          <button
            key={s.name}
            onClick={() => setSelected(s)}
            className={cn(
              'flex items-center gap-2.5 px-2 py-2 rounded-md text-left transition-colors',
              s.name === selected?.name ? 'bg-accent' : 'hover:bg-accent',
            )}
          >
            {s.plate_path ? (
              <img
                src={fileUrl(s.plate_path)}
                alt={s.name}
                className="size-10 rounded object-cover shrink-0 border border-border"
              />
            ) : (
              <div className="size-10 rounded bg-muted flex items-center justify-center shrink-0">
                <MapPin className="size-4 text-muted-foreground" />
              </div>
            )}
            <div className="flex-1 min-w-0">
              <div className="text-sm font-medium truncate">{s.name}</div>
              <div className="text-[10px] text-muted-foreground">
                {s.build_asset ? '参考背景图' : '文本背景'}
              </div>
            </div>
          </button>
        ))}
      </div>
      {/* 档案 */}
      <div className="flex-1 min-w-0 overflow-y-auto px-6 py-5">
        {selected ? (
          <div className="max-w-3xl space-y-4">
            {selected.plate_path ? (
              <img
                src={fileUrl(selected.plate_path)}
                alt={selected.name}
                className="max-h-96 w-auto rounded-md border border-border object-contain"
              />
            ) : selected.build_asset ? (
              <div className="flex items-center gap-2 text-sm text-muted-foreground border border-dashed border-border rounded-md px-4 py-6">
                <ImageOff className="size-4" />
                空景背景板尚未生成（渲染时按该地点首次出现自动生成）
              </div>
            ) : null}
            <h3 className="text-lg font-semibold text-foreground">{selected.name}</h3>
            <Field label="地点描述" value={selected.description} />
            <Field label="别名" value={selected.aliases.join('、')} />
            <Field
              label="参考背景"
              value={selected.build_asset ? '复现地点，已建参考背景图（跨镜风格一致）' : '一次性地点，走文本背景'}
            />
          </div>
        ) : (
          <div className="h-full flex items-center justify-center text-sm text-muted-foreground">
            选择左侧场景查看档案
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
