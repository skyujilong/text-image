import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { api } from '@/api/client'
import { useRunStore } from '@/store/runStore'

/** 细分审阅 payload 的 type，与后端 _make_review_node 传入的 payload_type 对齐。 */
type ReviewType = 'script_review' | 'storyboard_review' | 'new_characters_review'

/** 各审阅类型的展示元信息（标题 / 产物名 / 打回说明）。 */
const META: Record<ReviewType, { title: string; artifactLabel: string; reviseHint: string }> = {
  script_review: {
    title: '口播脚本审阅',
    artifactLabel: '口播脚本',
    reviseHint: '打回将回到 adapt_script 重写口播脚本，并据此意见调整',
  },
  storyboard_review: {
    title: '分镜审阅',
    artifactLabel: '分镜',
    reviseHint: '打回将回到 generate_storyboard 重生成分镜，并据此意见调整',
  },
  new_characters_review: {
    title: '新角色审阅',
    artifactLabel: '新角色候选',
    reviseHint: '打回将回到 detect_new_characters_llm 重新检测角色，并据此意见调整',
  },
}

interface ScriptLine {
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
  character_trait?: string
  visual_trait?: string
  tri_view_prompt?: string
  tri_view_prompt_cn?: string
}
interface Props {
  runId: string
  type: ReviewType
  chapterId?: string
  script?: ScriptLine[]
  storyboard?: StoryboardShot[]
  newCharacters?: NewCharacter[]
}

/**
 * 通用细分审阅面板：按 type 只渲染对应产物（剧本/分镜/新角色），用户 pass/revise。
 * resume 值 {decision, feedback}：打回时带修改意见供对应生成节点重做参考；通过不需要意见。
 * 由右侧常驻区渲染（body-only，无 Sheet 包装）。
 */
export default function GenericReviewPanel({
  runId, type, chapterId, script = [], storyboard = [], newCharacters = [],
}: Props) {
  const { setActiveInteraction } = useRunStore()
  const [feedback, setFeedback] = useState('')
  const meta = META[type]

  const handle = async (decision: 'pass' | 'revise') => {
    try {
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
        <h2 className="text-lg font-semibold text-foreground">
          {meta.title}{chapterId ? ` · ${chapterId}` : ''}
        </h2>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-4 flex flex-col gap-4">
        {type === 'script_review' && <ScriptSection script={script} />}
        {type === 'storyboard_review' && <StoryboardSection storyboard={storyboard} />}
        {type === 'new_characters_review' && <NewCharactersSection characters={newCharacters} />}

        <section>
          <h3 className="text-sm font-semibold mb-2 text-foreground">修改意见（打回时填写）</h3>
          <textarea
            value={feedback}
            onChange={(e) => setFeedback(e.target.value)}
            placeholder="打回重做时填写修改意见，留空则盲重做"
            className="w-full min-h-[80px] text-xs border border-input rounded p-2 resize-y bg-background text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
          />
          <p className="text-xs text-muted-foreground mt-1">{meta.reviseHint}</p>
        </section>
      </div>

      <div className="flex flex-col-reverse sm:flex-row sm:justify-end sm:space-x-2 px-6 pb-6 gap-2">
        <Button variant="ghost" className="text-destructive hover:bg-destructive/10" onClick={() => handle('revise')}>
          打回重做
        </Button>
        <Button onClick={() => handle('pass')}>
          审核通过
        </Button>
      </div>
    </div>
  )
}

/** 口播脚本区块渲染：画面行（action，含角色名）+ 口播行（text）。 */
function ScriptSection({ script }: { script: ScriptLine[] }) {
  return (
    <section>
      <h3 className="text-sm font-semibold mb-2 text-foreground">口播脚本（{script.length} 条）</h3>
      <div className="flex flex-col gap-1 text-xs">
        {script.map((line, i) => (
          <div key={i} className="border border-border rounded p-2 bg-accent/40">
            <div className="text-foreground">{line.text}</div>
            {line.action && (
              <div className="text-muted-foreground mt-0.5">画面：{line.action}</div>
            )}
          </div>
        ))}
        {script.length === 0 && <p className="text-muted-foreground">无口播脚本</p>}
      </div>
    </section>
  )
}

/** 分镜区块渲染。 */
function StoryboardSection({ storyboard }: { storyboard: StoryboardShot[] }) {
  return (
    <section>
      <h3 className="text-sm font-semibold mb-2 text-foreground">分镜（{storyboard.length} 条）</h3>
      <div className="flex flex-col gap-1 text-xs">
        {storyboard.map((shot, i) => (
          <div key={i} className="border border-border rounded p-2 bg-accent/40">
            <div className="flex items-center gap-2">
              <span className="font-mono text-muted-foreground">{shot.storyboard_id ?? i}</span>
              {shot.scene_change && (
                <span className="px-1 rounded bg-orange-100 text-orange-700">场景切换</span>
              )}
            </div>
            <div className="text-foreground">{shot.speaker ?? ''}：{shot.text}</div>
            {shot.scene_prompt && (
              <div className="text-muted-foreground mt-1">画面：{shot.scene_prompt}</div>
            )}
          </div>
        ))}
        {storyboard.length === 0 && <p className="text-muted-foreground">无分镜</p>}
      </div>
    </section>
  )
}

/** 新角色候选区块渲染。 */
function NewCharactersSection({ characters }: { characters: NewCharacter[] }) {
  return (
    <section>
      <h3 className="text-sm font-semibold mb-2 text-foreground">新角色候选（{characters.length} 个）</h3>
      <div className="flex flex-col gap-1 text-xs">
        {characters.map((c, i) => (
          <div key={i} className="border border-border rounded p-2 bg-accent/40">
            <span className="font-medium text-foreground">{c.name ?? '未命名'}</span>
            {c.appearance && <span className="text-muted-foreground">：{c.appearance}</span>}
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
        {characters.length === 0 && <p className="text-muted-foreground">本章无新角色</p>}
      </div>
      <p className="text-xs text-muted-foreground mt-1">通过后由 commit_chapter 提交：新角色进 setup_queue 上传三视图</p>
    </section>
  )
}
