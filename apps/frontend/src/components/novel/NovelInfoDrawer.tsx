import { useState } from 'react'
import { BookOpen, Globe, Users } from 'lucide-react'
import { cn } from '@/lib/utils'
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import ChapterReader from './ChapterReader'
import WorldviewPanel from './WorldviewPanel'
import CharacterReader from './CharacterReader'

type NovelTab = 'chapters' | 'worldview' | 'characters'

interface NovelInfoDrawerProps {
  runId: string
  open: boolean
  onOpenChange: (open: boolean) => void
}

const TABS = [
  { key: 'chapters' as const, label: '章节', Icon: BookOpen },
  { key: 'worldview' as const, label: '世界观', Icon: Globe },
  { key: 'characters' as const, label: '人物', Icon: Users },
]

/** 「小说信息」左抽屉：章节原文 / 世界观 / 人物，Tab 常驻切换。 */
export default function NovelInfoDrawer({ runId, open, onOpenChange }: NovelInfoDrawerProps) {
  const [tab, setTab] = useState<NovelTab>('chapters')

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="left" className="w-[92vw] sm:max-w-4xl flex flex-col gap-0 p-0">
        <SheetHeader className="px-6 py-3 border-b border-border text-left">
          <SheetTitle>小说信息</SheetTitle>
        </SheetHeader>

        <div className="flex items-center gap-1 px-4 border-b border-border shrink-0">
          {TABS.map(({ key, label, Icon }) => (
            <button
              key={key}
              onClick={() => setTab(key)}
              className={cn(
                'flex items-center gap-1.5 px-3 py-2 text-sm border-b-2 transition-colors',
                tab === key
                  ? 'border-primary text-foreground'
                  : 'border-transparent text-muted-foreground hover:text-foreground',
              )}
            >
              <Icon className="size-4" />
              {label}
            </button>
          ))}
        </div>

        <div className="flex-1 min-h-0 flex flex-col">
          {tab === 'chapters' && <ChapterReader runId={runId} />}
          {tab === 'worldview' && <WorldviewPanel runId={runId} />}
          {tab === 'characters' && <CharacterReader runId={runId} />}
        </div>
      </SheetContent>
    </Sheet>
  )
}
