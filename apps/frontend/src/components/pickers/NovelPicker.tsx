import { useEffect, useState } from 'react'
import { BookText, FolderPlus, Loader2, X } from 'lucide-react'
import { api, type NovelEntry } from '@/api/client'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { useWorkDirs } from '@/hooks/useWorkDirs'
import DirBrowserSheet from '@/components/pickers/DirBrowserSheet'

interface Props {
  onPick: (novel: NovelEntry) => void
}

function basename(p: string): string {
  const parts = p.replace(/\/+$/, '').split('/')
  return parts[parts.length - 1] || p
}

/** 选书：管理工作目录 + 从选中工作目录里挑一本小说。同名小说按所属工作目录区分。 */
export default function NovelPicker({ onPick }: Props) {
  const { workDirs, ready, add, remove } = useWorkDirs()
  const [browserOpen, setBrowserOpen] = useState(false)
  // 用户显式选择存 selectedId；未选时派生回退到第一个工作目录（免掉「默认选中」的 effect）。
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const effectiveId = selectedId ?? workDirs[0]?.id ?? null
  const [novels, setNovels] = useState<NovelEntry[]>([])
  const [loadedFor, setLoadedFor] = useState<number | null>(null)

  // 选中工作目录 → 扫其下小说。setState 全在 .then（异步回调），不在 effect 体内同步 setState。
  useEffect(() => {
    if (effectiveId === null) return
    let cancelled = false
    api
      .listWorkDirNovels(effectiveId)
      .then((res) => { if (!cancelled) { setNovels(res.novels); setLoadedFor(effectiveId) } })
      .catch(() => { if (!cancelled) { setNovels([]); setLoadedFor(effectiveId) } })
    return () => { cancelled = true }
  }, [effectiveId])

  const novelsLoading = effectiveId !== null && loadedFor !== effectiveId
  const selectedWorkDir = workDirs.find((w) => w.id === effectiveId) ?? null
  const workDirLabel = selectedWorkDir ? selectedWorkDir.label || basename(selectedWorkDir.path) : ''

  const handleRemoveWorkDir = async (e: React.MouseEvent, id: number) => {
    e.stopPropagation()
    await remove(id)
    if (selectedId === id) setSelectedId(null)
  }

  return (
    <div className="space-y-4">
      {/* 工作目录 chips + 添加 */}
      <div className="flex flex-wrap items-center gap-2">
        {workDirs.map((wd) => (
          <div
            key={wd.id}
            className={cn(
              'group flex items-center gap-1.5 pl-3 pr-1.5 py-1.5 rounded-full border text-sm cursor-pointer transition-colors',
              effectiveId === wd.id ? 'border-primary bg-accent' : 'border-border hover:bg-accent',
            )}
            onClick={() => setSelectedId(wd.id)}
            title={wd.path}
          >
            <span className="truncate max-w-[180px]">{wd.label || basename(wd.path)}</span>
            <Button
              variant="ghost" size="icon"
              className="size-5 opacity-0 group-hover:opacity-100 hover:text-destructive"
              title="移除该工作目录（不删磁盘文件）"
              onClick={(e) => handleRemoveWorkDir(e, wd.id)}
            >
              <X className="size-3" />
            </Button>
          </div>
        ))}
        <Button variant="outline" size="sm" onClick={() => setBrowserOpen(true)}>
          <FolderPlus className="size-4" />
          工作目录
        </Button>
      </div>

      {/* 小说网格 */}
      {!ready ? null : workDirs.length === 0 ? (
        <div className="py-10 text-center text-sm text-muted-foreground">
          还没有工作目录。点「工作目录」选择一个存放小说的父文件夹。
        </div>
      ) : novelsLoading ? (
        <div className="flex items-center justify-center py-10 text-muted-foreground">
          <Loader2 className="size-4 animate-spin" />
        </div>
      ) : novels.length === 0 ? (
        <div className="py-10 text-center text-sm text-muted-foreground">
          该工作目录下没有可用小说（需含 chapters/*.txt）。
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          {novels.map((novel) => (
            <button
              key={novel.path}
              type="button"
              className="flex flex-col items-start gap-1 p-3 rounded-lg border border-border hover:border-primary hover:bg-accent text-left transition-colors"
              onClick={() => onPick(novel)}
              title={novel.path}
            >
              <BookText className="size-5 text-muted-foreground" />
              <span className="text-sm font-medium truncate w-full">{novel.title || novel.name}</span>
              <span className="text-xs text-muted-foreground truncate w-full">{novel.name}</span>
              <span className="text-[10px] text-muted-foreground truncate w-full">
                {novel.chapter_count} 章 · {workDirLabel}
              </span>
            </button>
          ))}
        </div>
      )}

      <DirBrowserSheet
        open={browserOpen}
        onOpenChange={setBrowserOpen}
        onAdd={async (path) => {
          const wd = await add(path)
          if (wd) setSelectedId(wd.id)
        }}
      />
    </div>
  )
}
