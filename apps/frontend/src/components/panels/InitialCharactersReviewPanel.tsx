import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { api } from '@/api/client'
import { useRunStore } from '@/store/runStore'

/** review_initial_characters interrupt 的角色（与后端 CharacterProfile 对齐）。 */
interface InitialCharacter {
  name?: string
  appearance?: string
  character_trait?: string
  visual_trait?: string
  tri_view_prompt?: string
  tri_view_prompt_cn?: string
}
interface Props {
  runId: string
  characters: InitialCharacter[]
}

/**
 * 初始角色审阅面板：展示 LLM 解析出的主要角色
 * （name/appearance/character_trait/visual_trait/tri_view_prompt/tri_view_prompt_cn）。
 * resume "pass" → 角色进 setup_queue 逐个上传三视图；"revise" → 回 parse_characters_llm 重解析。
 * 由右侧常驻区渲染（body-only，无 Sheet 包装）。
 */
export default function InitialCharactersReviewPanel({ runId, characters }: Props) {
  const { setActiveInteraction, activeInteraction } = useRunStore()
  const [feedback, setFeedback] = useState('')

  const handle = async (decision: 'pass' | 'revise') => {
    if (!activeInteraction) return
    try {
      // resume 值为对象 {decision, feedback}：打回时带修改意见供 parse_characters_llm 重解析参考；
      // 通过不需要意见。与后端 review_initial_characters 节点解析对齐。
      await api.resumeRun(runId, activeInteraction.scope, activeInteraction.thread_id, decision === 'revise'
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
        <h2 className="text-lg font-semibold text-foreground">初始角色审核（review_initial_characters）</h2>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-4 flex flex-col gap-3">
        {characters.length === 0 && (
          <p className="text-sm text-muted-foreground">未解析到初始主要角色；通过后将跳过初始角色设定。</p>
        )}
        {characters.map((c, i) => (
          <div key={i} className="border border-border rounded p-3 bg-accent/40 text-xs">
            <div className="font-medium text-sm text-foreground">{c.name ?? '未命名'}</div>
            {c.appearance && <div className="text-muted-foreground mt-1">外观：{c.appearance}</div>}
            {c.character_trait && (
              <div className="text-muted-foreground mt-1">人物特征：{c.character_trait}</div>
            )}
            {c.visual_trait && (
              <div className="text-muted-foreground mt-1">特征（英）：{c.visual_trait}</div>
            )}
            {c.tri_view_prompt_cn && (
              <div className="text-muted-foreground mt-1">三视图参考：{c.tri_view_prompt_cn}</div>
            )}
            {c.tri_view_prompt && (
              <div className="text-muted-foreground/70 mt-1">三视图参考（英）：{c.tri_view_prompt}</div>
            )}
          </div>
        ))}
        <p className="text-xs text-muted-foreground mt-1">通过后将逐个进入上传三视图 + 音色设定。</p>

        <section>
          <h3 className="text-sm font-semibold mb-2 text-foreground">修改意见（打回时填写）</h3>
          <textarea
            value={feedback}
            onChange={(e) => setFeedback(e.target.value)}
            placeholder="打回重解析时填写修改意见，如「漏了重要角色、外观描述太简略」，留空则盲重解析"
            className="w-full min-h-[80px] text-xs border border-input rounded p-2 resize-y bg-background"
          />
        </section>
      </div>

      <div className="flex flex-col-reverse sm:flex-row sm:justify-end sm:space-x-2 px-6 pb-6 gap-2">
        <Button variant="outline" onClick={() => handle('revise')}>
          打回重解析
        </Button>
        <Button onClick={() => handle('pass')}>
          审核通过
        </Button>
      </div>
    </div>
  )
}
