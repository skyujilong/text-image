import { useEffect, useMemo, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { ArrowLeft, ChevronDown, ChevronRight, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  api,
  type GenerationEvent,
  type EvolutionStage,
  type PromptConfig,
} from '@/api/client'
import { useRunStore } from '@/store/runStore'
import { cn } from '@/lib/utils'
import TemplateDiff from '@/components/prompt/TemplateDiff'
import MergedRunRules from '@/components/prompt/MergedRunRules'

// 阶段（模块）中文标签。
const STAGE_LABEL: Record<EvolutionStage, string> = {
  adapt_script: '剧本改编',
  storyboard: '分镜稿',
  initial_characters: '角色检测',
}

// 决策语义色（与 Sidebar.STATUS_META 同风格：圆点 + 徽标，集中常量避免各处配色不一致）。
const DECISION_META: Record<'pass' | 'revise', { label: string; dot: string; badge: string }> = {
  pass: { label: '通过', dot: 'bg-green-500', badge: 'bg-green-100 text-green-700' },
  revise: { label: '打回', dot: 'bg-orange-500', badge: 'bg-orange-100 text-orange-700' },
}

// 提示词对比的两个模板键 → 标签。
const TEMPLATE_LABEL: Record<'adapt_script' | 'scene_change', string> = {
  adapt_script: '剧本改编（口播）模板',
  scene_change: '分镜换图点模板',
}

export default function PromptInspectPage() {
  const { runId } = useParams<{ runId: string }>()
  const navigate = useNavigate()
  const { runs, setCurrentRunId } = useRunStore()

  const [config, setConfig] = useState<PromptConfig | null>(null)
  const [events, setEvents] = useState<GenerationEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Set<number>>(new Set())

  useEffect(() => {
    if (runId) setCurrentRunId(runId)
  }, [runId, setCurrentRunId])

  useEffect(() => {
    if (!runId) return
    let cancelled = false
    // setState 只在异步回调里发生（避免 react-hooks/set-state-in-effect）；
    // cancelled 兜住 runId 快速切换导致的过期响应。
    Promise.all([api.getPromptConfig(runId), api.getGenerationEvents(runId)])
      .then(([cfg, evs]) => {
        if (cancelled) return
        setConfig(cfg)
        setEvents(evs)
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
  }, [runId])

  const run = runId ? runs[runId] : undefined
  const runTitle = run?.novel_title || runId?.slice(0, 8) || '未知'

  // 审阅记录按章节分组（chapter_id 为 null 的归入「初始/全局」）。
  const groups = useMemo(() => {
    const map = new Map<string, GenerationEvent[]>()
    for (const ev of events) {
      const key = ev.chapter_id ?? '__global__'
      const list = map.get(key) ?? []
      list.push(ev)
      map.set(key, list)
    }
    return Array.from(map.entries())
  }, [events])

  function toggle(id: number) {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  return (
    <div className="flex h-screen overflow-hidden flex-col">
      {/* Top bar */}
      <div className="flex items-center gap-3 px-4 h-12 border-b border-border bg-background shrink-0">
        <Button variant="ghost" size="sm" onClick={() => navigate(`/runs/${runId}`)}>
          <ArrowLeft className="size-4" />
          返回规划
        </Button>
        <span className="text-sm font-medium">{runTitle}</span>
        <span className="text-xs text-muted-foreground">提示词检视</span>
        {config && (
          <span className="ml-auto text-xs text-muted-foreground">
            题材方案：<span className="text-foreground">{config.scheme_label}</span>
          </span>
        )}
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto px-6 py-6 space-y-8 max-w-5xl w-full mx-auto">
        {loading && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" />
            加载中…
          </div>
        )}
        {error && <div className="text-sm text-destructive">加载失败：{error}</div>}

        {!loading && !error && config && (
          <>
            {/* 提示词对比 */}
            <section className="space-y-4">
              <div>
                <h2 className="text-base font-semibold text-foreground">提示词对比</h2>
                <p className="text-xs text-muted-foreground mt-0.5">
                  本 run 实际生效模板 vs「{config.scheme_label}」内置预设原文（
                  <span className="text-emerald-700">绿=新增</span> /{' '}
                  <span className="text-destructive">红=删除</span>）
                </p>
              </div>
              {(['adapt_script', 'scene_change'] as const).map((key) => (
                <div key={key} className="space-y-1.5">
                  <h3 className="text-sm font-medium text-foreground">{TEMPLATE_LABEL[key]}</h3>
                  <TemplateDiff before={config.defaults[key]} after={config.templates[key]} />
                </div>
              ))}
            </section>

            {/* 本 run 已合并的校正规则（可还原） */}
            <section className="space-y-4">
              <div>
                <h2 className="text-base font-semibold text-foreground">本 run 已合并的校正规则</h2>
                <p className="text-xs text-muted-foreground mt-0.5">
                  审阅面板归纳合并进本 run 提示词的校正规则（注入 %%LEARNED_RULES%% 槽，上方对比不含）。逐条移除或一键清空即可还原。
                </p>
              </div>
              {(['adapt_script', 'scene_change'] as const).map((key) =>
                runId ? (
                  <MergedRunRules
                    key={key}
                    runId={runId}
                    ruleStage={key}
                    label={TEMPLATE_LABEL[key]}
                  />
                ) : null,
              )}
            </section>

            {/* 审阅记录 */}
            <section className="space-y-4">
              <div>
                <h2 className="text-base font-semibold text-foreground">审阅记录</h2>
                <p className="text-xs text-muted-foreground mt-0.5">
                  每次「人类审阅一版生成物」的决策与修改意见（打回→重生成→通过 自然成链）
                </p>
              </div>

              {groups.length === 0 && (
                <p className="text-sm text-muted-foreground">暂无审阅记录（本 run 尚未产生人工审阅事件）。</p>
              )}

              {groups.map(([chapterKey, evs]) => (
                <div key={chapterKey} className="space-y-2">
                  <div className="text-xs font-semibold text-muted-foreground">
                    {chapterKey === '__global__' ? '初始 / 全局' : `章节 ${chapterKey}`}
                  </div>
                  <div className="space-y-1.5">
                    {evs.map((ev) => {
                      const dm = DECISION_META[ev.decision]
                      const isOpen = expanded.has(ev.id)
                      return (
                        <div key={ev.id} className="rounded-md border border-border">
                          <div className="flex items-center gap-2 px-3 py-2">
                            <span className={cn('size-2 rounded-full shrink-0', dm.dot)} />
                            <span className="text-sm text-foreground">{STAGE_LABEL[ev.stage]}</span>
                            <span className="text-xs text-muted-foreground">第 {ev.attempt} 版</span>
                            <span
                              className={cn(
                                'text-xs px-1.5 py-0.5 rounded font-medium',
                                dm.badge,
                              )}
                            >
                              {dm.label}
                            </span>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="ml-auto h-7 px-2 text-xs text-muted-foreground"
                              onClick={() => toggle(ev.id)}
                            >
                              {isOpen ? (
                                <ChevronDown className="size-3.5" />
                              ) : (
                                <ChevronRight className="size-3.5" />
                              )}
                              查看输出
                            </Button>
                          </div>
                          {ev.feedback && (
                            <div className="px-3 pb-2 text-xs text-foreground">
                              <span className="text-muted-foreground">修改意见：</span>
                              {ev.feedback}
                            </div>
                          )}
                          {isOpen && (
                            <pre className="mx-3 mb-3 max-h-96 overflow-auto rounded-md border border-border bg-muted/30 p-2 text-xs whitespace-pre-wrap break-words">
                              {JSON.stringify(ev.output, null, 2)}
                            </pre>
                          )}
                        </div>
                      )
                    })}
                  </div>
                </div>
              ))}
            </section>
          </>
        )}
      </div>
    </div>
  )
}
