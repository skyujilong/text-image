import { useState } from 'react'
import { Check, CheckSquare, Loader2, Sparkles, Square, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { api, type RuleStage } from '@/api/client'
import { cn } from '@/lib/utils'
import { useRunStore } from '@/store/runStore'
import MergedRunRules from '@/components/prompt/MergedRunRules'

/** 细分审阅 payload 的 type，与后端 _make_review_node 传入的 payload_type 对齐。 */
type ReviewType = 'script_review' | 'storyboard_review'

/** 审阅 type → 规则 stage（与后端 _PANEL_TYPE_TO_RULE_STAGE 对齐）：分镜换图点信号来自分镜审阅。 */
const RULE_STAGE_BY_TYPE: Record<ReviewType, RuleStage> = {
  script_review: 'adapt_script',
  storyboard_review: 'scene_change',
}

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
}

interface ScriptLine {
  text?: string
  action?: string
}
interface StoryboardShot {
  storyboard_id?: string | number
  scene_change?: boolean
  text?: string
  speaker?: string
  subjects?: string[]
  scene_prompt?: string
}
interface Props {
  runId: string
  type: ReviewType
  chapterId?: string
  script?: ScriptLine[]
  storyboard?: StoryboardShot[]
}

/**
 * 通用细分审阅面板：按 type 只渲染对应产物（剧本/分镜），用户 pass/revise。
 * resume 值 {decision, feedback}：打回时带修改意见供对应生成节点重做参考；通过不需要意见。
 * 由右侧常驻区渲染（body-only，无 Sheet 包装）。
 */
export default function GenericReviewPanel({
  runId, type, chapterId, script = [], storyboard = [],
}: Props) {
  const { setActiveInteraction, activeInteraction } = useRunStore()
  const [feedback, setFeedback] = useState('')
  const meta = META[type]

  const handle = async (decision: 'pass' | 'revise') => {
    if (!activeInteraction) return
    try {
      await api.resumeRun(
        runId,
        activeInteraction.scope,
        activeInteraction.thread_id,
        decision === 'revise'
          ? { decision: 'revise', feedback }
          : { decision: 'pass' }
      )
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

        <RunRuleRefineSection runId={runId} type={type} />
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
            {shot.subjects && shot.subjects.length > 0 && (
              <div className="text-muted-foreground mt-1">主体：{shot.subjects.join('、')}</div>
            )}
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

/** 归纳预览中的一条候选规则（本地态：可编辑 rule + 是否纳入合并）。 */
interface ProposedRule {
  rule: string
  source: string
  accepted: boolean
}

/**
 * 提示词自进化 · 环②③ run 内版：一键把本 run 该阶段的历次打回意见用 LLM 归纳成校正规则，
 * 逐条编辑/取舍后合并进本 run 的提示词（%%LEARNED_RULES%% 槽），后续该阶段生成即时遵守；
 * 可选同时写一份全局候选，供日后在进化台采纳给未来 run。与 pass/revise 解耦，不触发 resume。
 */
function RunRuleRefineSection({ runId, type }: { runId: string; type: ReviewType }) {
  const [analyzing, setAnalyzing] = useState(false)
  const [proposed, setProposed] = useState<ProposedRule[] | null>(null)
  const [alsoGlobal, setAlsoGlobal] = useState(true)
  const [merging, setMerging] = useState(false)
  const [msg, setMsg] = useState('')
  const [mergedReload, setMergedReload] = useState(0) // bump 触发下方「已合并规则」重取

  const acceptedCount = proposed?.filter((p) => p.accepted).length ?? 0

  const updateRule = (i: number, patch: Partial<ProposedRule>) =>
    setProposed((prev) => (prev ? prev.map((p, j) => (j === i ? { ...p, ...patch } : p)) : prev))

  const handleAnalyze = async () => {
    setAnalyzing(true)
    setMsg('')
    try {
      const res = await api.analyzeRunRules(runId, type)
      setProposed(res.proposed.map((p) => ({ ...p, accepted: true })))
      setMsg(res.message)
    } catch (e) {
      console.error('analyze run rules failed', e)
      setMsg('归纳失败，请重试')
    } finally {
      setAnalyzing(false)
    }
  }

  const handleMerge = async () => {
    if (!proposed) return
    const rules = proposed
      .filter((p) => p.accepted)
      .map((p) => p.rule.trim())
      .filter(Boolean)
    if (rules.length === 0) {
      setMsg('请至少保留一条规则再合并')
      return
    }
    setMerging(true)
    try {
      const res = await api.mergeRunRules(runId, type, rules, alsoGlobal)
      setProposed(null)
      setMergedReload((n) => n + 1) // 刷新下方「已合并规则」清单
      setMsg(
        `已合并 ${res.merged} 条到本 run，后续该阶段生成自动遵守` +
          (res.global_candidates ? `；另写入 ${res.global_candidates} 条全局候选待采纳` : ''),
      )
    } catch (e) {
      console.error('merge run rules failed', e)
      setMsg('合并失败，请重试')
    } finally {
      setMerging(false)
    }
  }

  return (
    <section className="border-t border-border pt-3">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-semibold text-foreground">提示词自进化（本 run）</h3>
        <Button
          variant="outline"
          size="sm"
          className="h-7 ml-auto"
          disabled={analyzing}
          onClick={handleAnalyze}
        >
          {analyzing ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Sparkles className="size-3.5" />
          )}
          从本 run 反馈归纳校正规则
        </Button>
      </div>
      <p className="text-xs text-muted-foreground mt-1">
        归纳本 run 该阶段历次打回意见为校正规则，确认后并入本 run 提示词，后续生成即时遵守。
      </p>

      {proposed && proposed.length > 0 && (
        <div className="mt-3 flex flex-col gap-2">
          {proposed.map((p, i) => (
            <div
              key={i}
              className={cn('rounded-md border border-border p-2', !p.accepted && 'opacity-50')}
            >
              <div className="flex items-start gap-2">
                <Textarea
                  value={p.rule}
                  onChange={(e) => updateRule(i, { rule: e.target.value })}
                  className="min-h-[52px] flex-1 text-xs"
                  spellCheck={false}
                />
                <Button
                  variant="ghost"
                  size="icon"
                  className={cn('size-7 shrink-0', !p.accepted && 'text-destructive')}
                  title={p.accepted ? '点击排除该规则' : '点击恢复该规则'}
                  onClick={() => updateRule(i, { accepted: !p.accepted })}
                >
                  {p.accepted ? <Check className="size-3.5" /> : <X className="size-3.5" />}
                </Button>
              </div>
              {p.source && <p className="text-xs text-muted-foreground mt-1">源：{p.source}</p>}
            </div>
          ))}

          <div className="flex items-center gap-2 pt-1">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-xs text-muted-foreground"
              onClick={() => setAlsoGlobal((v) => !v)}
            >
              {alsoGlobal ? (
                <CheckSquare className="size-3.5" />
              ) : (
                <Square className="size-3.5" />
              )}
              同时写入全局候选
            </Button>
            <Button
              size="sm"
              className="h-7 ml-auto"
              disabled={merging || acceptedCount === 0}
              onClick={handleMerge}
            >
              {merging && <Loader2 className="size-3.5 animate-spin" />}
              合并到本 run（{acceptedCount}）
            </Button>
          </div>
        </div>
      )}

      {msg && <p className="text-xs text-muted-foreground mt-2">{msg}</p>}

      <div className="mt-3 border-t border-border pt-3">
        <MergedRunRules
          runId={runId}
          ruleStage={RULE_STAGE_BY_TYPE[type]}
          label={META[type].artifactLabel}
          reloadSignal={mergedReload}
        />
      </div>
    </section>
  )
}
