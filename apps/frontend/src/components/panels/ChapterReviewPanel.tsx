import {
  Sheet, SheetContent, SheetHeader, SheetTitle, SheetFooter,
} from '@/components/ui/sheet'
import { Button } from '@/components/ui/button'
import { api } from '@/api/client'
import { useRunStore } from '@/store/runStore'

/** review_chapter interrupt 的 payload（与后端 chapter_nodes.review_chapter 对齐）。 */
interface ScriptLine {
  speaker?: string
  text?: string
  action?: string
}
interface StoryboardShot {
  storyboard_id?: string
  scene_change?: boolean
  text?: string
  speaker?: string
  scene_prompt?: string
}
interface NewCharacter {
  name?: string
  appearance?: string
  tri_view_prompt?: string
}
interface Props {
  runId: string
  chapterId?: string
  script: ScriptLine[]
  storyboard: StoryboardShot[]
  newCharacters: NewCharacter[]
  open: boolean
  onClose: () => void
}

/**
 * 章节审核面板：展示剧本/分镜/新角色，用户 pass/revise。
 * resume 值 "pass" → 标 planned + 新角色进 setup_queue；"revise" → 回 adapt_script 重写。
 */
export default function ChapterReviewPanel({
  runId, chapterId, script, storyboard, newCharacters, open, onClose,
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
          <SheetTitle>章节审核（review_chapter{chapterId ? ` · ${chapterId}` : ''}）</SheetTitle>
        </SheetHeader>

        <div className="flex flex-col gap-4 py-4 overflow-y-auto max-h-[70vh]">
          <section>
            <h3 className="text-sm font-semibold mb-2">剧本（{script.length} 条）</h3>
            <div className="flex flex-col gap-1 text-xs">
              {script.map((line, i) => (
                <div key={i} className="border rounded p-2 bg-gray-50">
                  <span className="font-medium text-blue-600">{line.speaker ?? '?'}</span>
                  ：{line.text}
                  {line.action && <span className="text-gray-400"> （{line.action}）</span>}
                </div>
              ))}
              {script.length === 0 && <p className="text-gray-400">无剧本</p>}
            </div>
          </section>

          <section>
            <h3 className="text-sm font-semibold mb-2">分镜（{storyboard.length} 条）</h3>
            <div className="flex flex-col gap-1 text-xs">
              {storyboard.map((shot, i) => (
                <div key={i} className="border rounded p-2 bg-gray-50">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-gray-500">{shot.storyboard_id ?? i}</span>
                    {shot.scene_change && (
                      <span className="px-1 bg-orange-100 text-orange-700 rounded">场景切换</span>
                    )}
                  </div>
                  <div>{shot.speaker ?? ''}：{shot.text}</div>
                  {shot.scene_prompt && (
                    <div className="text-gray-400 mt-1">画面：{shot.scene_prompt}</div>
                  )}
                </div>
              ))}
              {storyboard.length === 0 && <p className="text-gray-400">无分镜</p>}
            </div>
          </section>

          <section>
            <h3 className="text-sm font-semibold mb-2">新角色候选（{newCharacters.length} 个）</h3>
            <div className="flex flex-col gap-1 text-xs">
              {newCharacters.map((c, i) => (
                <div key={i} className="border rounded p-2 bg-gray-50">
                  <span className="font-medium">{c.name ?? '未命名'}</span>
                  {c.appearance && <span className="text-gray-500">：{c.appearance}</span>}
                  {c.tri_view_prompt && (
                    <div className="text-gray-400 mt-1">三视图参考：{c.tri_view_prompt}</div>
                  )}
                </div>
              ))}
              {newCharacters.length === 0 && <p className="text-gray-400">本章无新角色</p>}
            </div>
            <p className="text-xs text-gray-400 mt-1">通过后将进入角色设定（上传三视图 + 音色）</p>
          </section>
        </div>

        <SheetFooter>
          <Button variant="outline" onClick={() => handle('revise')} disabled={false}>
            打回重写
          </Button>
          <Button onClick={() => handle('pass')}>
            审核通过
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}
