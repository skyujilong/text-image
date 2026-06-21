import { useState } from 'react'
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
}

/**
 * 章节审核面板：展示剧本/分镜/新角色，用户 pass/revise。
 * resume 值 "pass" → 标 planned + 新角色进 setup_queue；"revise" → 回 adapt_script 重写。
 * 由右侧常驻区渲染（body-only，无 Sheet 包装）。
 */
export default function ChapterReviewPanel({
  runId, chapterId, script, storyboard, newCharacters,
}: Props) {
  const { setActiveInteraction } = useRunStore()
  const [feedback, setFeedback] = useState('')

  const handle = async (decision: 'pass' | 'revise') => {
    try {
      // resume 值为对象 {decision, feedback}：打回时带修改意见供 adapt_script 重写参考；
      // 通过不需要意见。与后端 review_chapter 节点解析对齐。
      await api.resumeRun(runId, decision === 'revise'
        ? { decision: 'revise', feedback }
        : { decision: 'pass' })
      setActiveInteraction(null)
      setFeedback('')
    } catch (e) {
      console.error('resume failed', e)
    }
  }

  return (
    <div className="flex flex-col h-full">
      <div className="px-6 pt-6">
        <h2 className="text-lg font-semibold text-foreground">章节审核（review_chapter{chapterId ? ` · ${chapterId}` : ''}）</h2>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-4 flex flex-col gap-4">
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

        <section>
          <h3 className="text-sm font-semibold mb-2">修改意见（打回时填写）</h3>
          <textarea
            value={feedback}
            onChange={(e) => setFeedback(e.target.value)}
            placeholder="打回重写时填写修改意见，如「对白太书面、节奏太快」，留空则盲重写"
            className="w-full min-h-[80px] text-xs border rounded p-2 resize-y"
          />
        </section>
      </div>

      <div className="flex flex-col-reverse sm:flex-row sm:justify-end sm:space-x-2 px-6 pb-6 gap-2">
        <Button variant="outline" onClick={() => handle('revise')}>
          打回重写
        </Button>
        <Button onClick={() => handle('pass')}>
          审核通过
        </Button>
      </div>
    </div>
  )
}
