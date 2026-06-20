import {
  Sheet, SheetContent, SheetHeader, SheetTitle, SheetFooter,
} from '@/components/ui/sheet'
import { Button } from '@/components/ui/button'
import { api } from '@/api/client'
import { useRunStore } from '@/store/runStore'

/** review_initial_characters interrupt 的角色（与后端 CharacterProfile 对齐）。 */
interface InitialCharacter {
  name?: string
  appearance?: string
  tri_view_prompt?: string
}
interface Props {
  runId: string
  characters: InitialCharacter[]
  open: boolean
  onClose: () => void
}

/**
 * 初始角色审阅面板：展示 LLM 解析出的主要角色（name/appearance/tri_view_prompt）。
 * resume "pass" → 角色进 setup_queue 逐个上传三视图；"revise" → 回 parse_characters_llm 重解析。
 */
export default function InitialCharactersReviewPanel({
  runId, characters, open, onClose,
}: Props) {
  const { setActiveInteraction } = useRunStore()

  const handle = async (decision: 'pass' | 'revise') => {
    try {
      await api.resumeRun(runId, decision)
      setActiveInteraction(null)
      onClose()
    } catch (e) {
      console.error('resume failed', e)
    }
  }

  return (
    <Sheet open={open} onOpenChange={(o: boolean) => !o && onClose()}>
      <SheetContent side="right" className="w-[520px] sm:max-w-[520px]">
        <SheetHeader>
          <SheetTitle>初始角色审核（review_initial_characters）</SheetTitle>
        </SheetHeader>

        <div className="flex flex-col gap-3 py-4 overflow-y-auto max-h-[70vh]">
          {characters.length === 0 && (
            <p className="text-sm text-gray-400">未解析到初始主要角色；通过后将跳过初始角色设定。</p>
          )}
          {characters.map((c, i) => (
            <div key={i} className="border rounded p-3 bg-gray-50 text-xs">
              <div className="font-medium text-sm text-blue-600">{c.name ?? '未命名'}</div>
              {c.appearance && <div className="text-gray-600 mt-1">外观：{c.appearance}</div>}
              {c.tri_view_prompt && (
                <div className="text-gray-400 mt-1">三视图参考：{c.tri_view_prompt}</div>
              )}
            </div>
          ))}
          <p className="text-xs text-gray-400 mt-1">通过后将逐个进入上传三视图 + 音色设定。</p>
        </div>

        <SheetFooter>
          <Button variant="outline" onClick={() => handle('revise')}>
            打回重解析
          </Button>
          <Button onClick={() => handle('pass')}>
            审核通过
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}
