import { useEffect } from 'react'
import { ArrowUp, Folder, FolderOpen, Loader2, Plus } from 'lucide-react'
import {
  Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle,
} from '@/components/ui/sheet'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import { useDirBrowser } from '@/hooks/useDirBrowser'

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** 把当前浏览到的目录添加为工作目录。 */
  onAdd: (path: string) => Promise<void>
}

/** 服务器端目录浏览器：逐层点进文件夹，选中一个父目录作为「工作目录」。 */
export default function DirBrowserSheet({ open, onOpenChange, onAdd }: Props) {
  const { listing, loading, error, atRoot, go, up } = useDirBrowser()

  // 打开时定位到 home（仅首次；已有 listing 时保留浏览位置）
  useEffect(() => {
    if (open && !listing) void go()
  }, [open, listing, go])

  const handleAdd = async () => {
    if (!listing) return
    await onAdd(listing.path)
    onOpenChange(false)
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-[420px] sm:max-w-[420px] flex flex-col p-0 gap-0">
        <SheetHeader className="p-4 border-b border-border">
          <SheetTitle>选择工作目录</SheetTitle>
          <SheetDescription>
            逐层进入服务器文件夹，选一个存放小说的父目录；其下所有小说会被列出供挑选。
          </SheetDescription>
        </SheetHeader>

        {/* 当前路径 + 上一级 */}
        <div className="flex items-center gap-2 px-4 py-2 border-b border-border">
          <Button
            variant="ghost" size="icon" className="size-8 shrink-0"
            onClick={up} disabled={atRoot || loading} title="上一级"
          >
            <ArrowUp className="size-4" />
          </Button>
          <span className="text-xs text-muted-foreground truncate flex-1" title={listing?.path}>
            {listing?.path ?? '…'}
          </span>
        </div>

        {/* 子目录列表 */}
        <div className="flex-1 overflow-y-auto">
          {loading && (
            <div className="flex items-center justify-center py-8 text-muted-foreground">
              <Loader2 className="size-4 animate-spin" />
            </div>
          )}
          {error && <div className="px-4 py-3 text-sm text-destructive">{error}</div>}
          {!loading && listing?.entries.length === 0 && (
            <div className="px-4 py-6 text-sm text-muted-foreground text-center">该目录下没有子文件夹</div>
          )}
          {!loading &&
            listing?.entries.map((entry) => (
              <button
                key={entry.path}
                type="button"
                className={cn(
                  'w-full flex items-center gap-2 px-4 py-2 text-left text-sm hover:bg-accent transition-colors',
                  entry.hidden && 'text-muted-foreground',
                )}
                onClick={() => go(entry.path)}
              >
                {entry.is_novel ? (
                  <FolderOpen className="size-4 shrink-0 text-foreground" />
                ) : (
                  <Folder className="size-4 shrink-0 text-muted-foreground" />
                )}
                <span className="truncate flex-1">{entry.name}</span>
                {entry.is_novel && <Badge variant="secondary" className="shrink-0">小说</Badge>}
              </button>
            ))}
        </div>

        <SheetFooter className="p-4 border-t border-border">
          <Button className="w-full" onClick={handleAdd} disabled={!listing || loading}>
            <Plus className="size-4" />
            添加当前目录为工作目录
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}
