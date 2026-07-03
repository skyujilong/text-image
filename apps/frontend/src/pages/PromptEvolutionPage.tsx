import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft, Check, Loader2, Sparkles, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  api,
  type FrictionStat,
  type LearnedRule,
  type RuleStage,
  type SchemeOption,
} from '@/api/client'
import { cn } from '@/lib/utils'

// 事件 stage（review 语汇）→ 中文，用于摩擦度排行。
const EVENT_STAGE_LABEL: Record<string, string> = {
  adapt_script: '剧本改编',
  storyboard: '分镜稿',
  initial_characters: '角色检测',
}

// 规则 stage（模板语汇）→ 中文 + 对应可归纳的信号来源说明。
const RULE_STAGES: { key: RuleStage; label: string }[] = [
  { key: 'adapt_script', label: '剧本改编' },
  { key: 'scene_change', label: '分镜换图点' },
]

export default function PromptEvolutionPage() {
  const navigate = useNavigate()

  const [schemes, setSchemes] = useState<SchemeOption[]>([])
  const [friction, setFriction] = useState<FrictionStat[]>([])
  const [rules, setRules] = useState<LearnedRule[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [scheme, setScheme] = useState<string>('')
  const [stage, setStage] = useState<RuleStage>('scene_change')
  const [proposing, setProposing] = useState(false)
  const [proposeMsg, setProposeMsg] = useState<string | null>(null)
  const [busyRule, setBusyRule] = useState<number | null>(null)

  useEffect(() => {
    let cancelled = false
    Promise.all([api.getEvolutionSchemes(), api.getFriction(), api.listRules()])
      .then(([sc, fr, rl]) => {
        if (cancelled) return
        setSchemes(sc)
        setFriction(fr)
        setRules(rl)
        if (sc.length > 0) setScheme((prev) => prev || sc[0].key)
        setError(null)
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const schemeLabel = useMemo(() => {
    const m = new Map(schemes.map((s) => [s.key, s.label]))
    return (key: string | null) => (key ? m.get(key) ?? key : '—')
  }, [schemes])

  async function refreshRules() {
    const rl = await api.listRules()
    setRules(rl)
  }

  async function handlePropose() {
    if (!scheme) return
    setProposing(true)
    setProposeMsg(null)
    try {
      const res = await api.proposeRules(scheme, stage)
      setProposeMsg(res.message)
      await refreshRules()
    } catch (e) {
      setProposeMsg(e instanceof Error ? e.message : String(e))
    } finally {
      setProposing(false)
    }
  }

  async function ruleAction(id: number, action: 'adopt' | 'reject' | 'retire') {
    setBusyRule(id)
    try {
      if (action === 'adopt') await api.adoptRule(id)
      else if (action === 'reject') await api.rejectRule(id)
      else await api.retireRule(id)
      await refreshRules()
    } finally {
      setBusyRule(null)
    }
  }

  const candidates = rules.filter((r) => r.status === 'candidate')
  const active = rules.filter((r) => r.status === 'active')

  return (
    <div className="flex h-screen overflow-hidden flex-col">
      {/* Top bar */}
      <div className="flex items-center gap-3 px-4 h-12 border-b border-border bg-background shrink-0">
        <Button variant="ghost" size="sm" onClick={() => navigate('/')}>
          <ArrowLeft className="size-4" />
          返回
        </Button>
        <Sparkles className="size-4 text-muted-foreground" />
        <span className="text-sm font-medium">提示词进化台</span>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-6 space-y-8 max-w-5xl w-full mx-auto">
        {loading && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" />
            加载中…
          </div>
        )}
        {error && <div className="text-sm text-destructive">加载失败：{error}</div>}

        {!loading && !error && (
          <>
            {/* 摩擦度排行 */}
            <section className="space-y-3">
              <div>
                <h2 className="text-base font-semibold text-foreground">摩擦度排行</h2>
                <p className="text-xs text-muted-foreground mt-0.5">
                  每 阶段×题材「通过前平均打回次数」——越高说明该提示词越该改（= 归纳优先级）
                </p>
              </div>
              {friction.length === 0 ? (
                <p className="text-sm text-muted-foreground">暂无审阅数据。</p>
              ) : (
                <div className="rounded-md border border-border overflow-hidden">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-border bg-muted/30 text-xs text-muted-foreground">
                        <th className="text-left font-medium px-3 py-2">阶段</th>
                        <th className="text-left font-medium px-3 py-2">题材</th>
                        <th className="text-right font-medium px-3 py-2">打回</th>
                        <th className="text-right font-medium px-3 py-2">通过</th>
                        <th className="text-right font-medium px-3 py-2">平均打回</th>
                      </tr>
                    </thead>
                    <tbody>
                      {friction.map((f, i) => {
                        const avg = f.revise_count / Math.max(f.pass_count, 1)
                        return (
                          <tr key={i} className="border-b border-border last:border-0">
                            <td className="px-3 py-2">{EVENT_STAGE_LABEL[f.stage] ?? f.stage}</td>
                            <td className="px-3 py-2 text-muted-foreground">{schemeLabel(f.scheme_key)}</td>
                            <td className="px-3 py-2 text-right">{f.revise_count}</td>
                            <td className="px-3 py-2 text-right">{f.pass_count}</td>
                            <td
                              className={cn(
                                'px-3 py-2 text-right font-medium',
                                avg >= 1 ? 'text-orange-600' : 'text-foreground',
                              )}
                            >
                              {avg.toFixed(1)}
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            {/* 归纳候选规则 */}
            <section className="space-y-3">
              <div>
                <h2 className="text-base font-semibold text-foreground">归纳候选规则</h2>
                <p className="text-xs text-muted-foreground mt-0.5">
                  对选定 题材×阶段 的历次打回意见跑一次归纳，产出候选校正规则（需人审采纳后才生效）
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs text-muted-foreground">题材</span>
                {schemes.map((s) => (
                  <Button
                    key={s.key}
                    variant={scheme === s.key ? 'default' : 'outline'}
                    size="sm"
                    className="h-7"
                    onClick={() => setScheme(s.key)}
                  >
                    {s.label}
                  </Button>
                ))}
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs text-muted-foreground">阶段</span>
                {RULE_STAGES.map((s) => (
                  <Button
                    key={s.key}
                    variant={stage === s.key ? 'default' : 'outline'}
                    size="sm"
                    className="h-7"
                    onClick={() => setStage(s.key)}
                  >
                    {s.label}
                  </Button>
                ))}
                <Button size="sm" className="h-7 ml-auto" disabled={proposing || !scheme} onClick={handlePropose}>
                  {proposing ? <Loader2 className="size-3.5 animate-spin" /> : <Sparkles className="size-3.5" />}
                  归纳候选规则
                </Button>
              </div>
              {proposeMsg && <p className="text-xs text-muted-foreground">{proposeMsg}</p>}
            </section>

            {/* 候选待审 */}
            <section className="space-y-3">
              <h2 className="text-base font-semibold text-foreground">
                候选规则待审{candidates.length > 0 && ` (${candidates.length})`}
              </h2>
              {candidates.length === 0 ? (
                <p className="text-sm text-muted-foreground">暂无待审候选规则。</p>
              ) : (
                <div className="space-y-1.5">
                  {candidates.map((r) => (
                    <div key={r.id} className="rounded-md border border-border px-3 py-2">
                      <div className="flex items-start gap-2">
                        <div className="flex-1 min-w-0">
                          <div className="text-sm text-foreground">{r.rule_text}</div>
                          <div className="text-xs text-muted-foreground mt-0.5">
                            {schemeLabel(r.scheme_key)} · {RULE_STAGES.find((s) => s.key === r.stage)?.label ?? r.stage}
                            {r.source_feedback_sample && <> · 源：{r.source_feedback_sample}</>}
                          </div>
                        </div>
                        <div className="flex items-center gap-1 shrink-0">
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-7 px-2 text-xs"
                            disabled={busyRule === r.id}
                            onClick={() => ruleAction(r.id, 'adopt')}
                          >
                            <Check className="size-3.5" />
                            采纳
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-7 px-2 text-xs text-destructive"
                            disabled={busyRule === r.id}
                            onClick={() => ruleAction(r.id, 'reject')}
                          >
                            <X className="size-3.5" />
                            驳回
                          </Button>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </section>

            {/* 生效台账 */}
            <section className="space-y-3">
              <h2 className="text-base font-semibold text-foreground">
                生效规则台账{active.length > 0 && ` (${active.length})`}
              </h2>
              <p className="text-xs text-muted-foreground -mt-1">
                这些规则会在下一次开新 run 选定对应题材时，注入到该阶段提示词的「校正清单」
              </p>
              {active.length === 0 ? (
                <p className="text-sm text-muted-foreground">暂无生效规则。</p>
              ) : (
                <div className="space-y-1.5">
                  {active.map((r) => (
                    <div key={r.id} className="rounded-md border border-border px-3 py-2">
                      <div className="flex items-start gap-2">
                        <div className="flex-1 min-w-0">
                          <div className="text-sm text-foreground">{r.rule_text}</div>
                          <div className="text-xs text-muted-foreground mt-0.5">
                            {schemeLabel(r.scheme_key)} · {RULE_STAGES.find((s) => s.key === r.stage)?.label ?? r.stage}
                          </div>
                        </div>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 px-2 text-xs text-muted-foreground shrink-0"
                          disabled={busyRule === r.id}
                          onClick={() => ruleAction(r.id, 'retire')}
                        >
                          退役
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </section>
          </>
        )}
      </div>
    </div>
  )
}
