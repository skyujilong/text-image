import { useCallback, useEffect, useState } from 'react'
import { Loader2, RotateCcw, Trash2 } from 'lucide-react'
import { api, type RuleStage } from '@/api/client'
import { Button } from '@/components/ui/button'

/**
 * 加载本 run 某规则阶段已合并的校正规则。
 * reloadSignal 变化或内部 reload() 均触发重取（合并成功 / 移除后刷新用）。
 */
function useRunRules(runId: string, ruleStage: RuleStage, reloadSignal: number) {
  const [rules, setRules] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [tick, setTick] = useState(0)

  useEffect(() => {
    let cancelled = false
    // setState 只在异步回调里发生（避免 react-hooks/set-state-in-effect）；cancelled 兜住
    // 快速切换/卸载的过期响应。首次 loading 由 useState(true) 兜底，重取时数据原地更新不闪。
    api
      .getRunRules(runId, ruleStage)
      .then((res) => {
        if (!cancelled) setRules(res.rules)
      })
      .catch((e) => {
        if (!cancelled) console.error('load run rules failed', e)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [runId, ruleStage, reloadSignal, tick])

  const reload = useCallback(() => setTick((t) => t + 1), [])
  return { rules, loading, reload }
}

interface MergedRunRulesProps {
  runId: string
  ruleStage: RuleStage
  /** 展示用阶段名（如「剧本改编（口播）模板」）。 */
  label: string
  /** 父级 bump 此值即触发重取（如审阅面板合并成功后）。默认 0。 */
  reloadSignal?: number
}

/**
 * 展示并「还原」本 run 某规则阶段已合并进提示词的校正规则：逐条移除 / 一键清空该阶段。
 * 自取数据（按 stage 圈定），移除后即时重取。审阅面板与 /prompts 检视页共用。
 */
export default function MergedRunRules({
  runId,
  ruleStage,
  label,
  reloadSignal = 0,
}: MergedRunRulesProps) {
  const { rules, loading, reload } = useRunRules(runId, ruleStage, reloadSignal)
  const [busy, setBusy] = useState<string | null>(null) // 正在处理的规则文本；'__all__' 表示清空中
  const [confirmClear, setConfirmClear] = useState(false)
  const [msg, setMsg] = useState('')

  const removeOne = async (rule: string) => {
    setBusy(rule)
    setMsg('')
    try {
      await api.removeRunRules(runId, ruleStage, [rule])
      reload()
    } catch (e) {
      console.error('remove run rule failed', e)
      setMsg('移除失败，请重试')
    } finally {
      setBusy(null)
    }
  }

  const clearAll = async () => {
    setBusy('__all__')
    setMsg('')
    try {
      const res = await api.removeRunRules(runId, ruleStage, null)
      setConfirmClear(false)
      reload()
      setMsg(`已清空 ${res.removed} 条`)
    } catch (e) {
      console.error('clear run rules failed', e)
      setMsg('清空失败，请重试')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <h4 className="text-xs font-medium text-foreground">
          {label}已合并规则
          <span className="text-muted-foreground">（{rules.length}）</span>
        </h4>
        {rules.length > 0 &&
          (confirmClear ? (
            <div className="ml-auto flex items-center gap-1">
              <Button
                variant="ghost"
                size="sm"
                className="h-6 px-2 text-xs text-destructive hover:bg-destructive/10"
                disabled={busy !== null}
                onClick={clearAll}
              >
                {busy === '__all__' ? (
                  <Loader2 className="size-3 animate-spin" />
                ) : (
                  <Trash2 className="size-3" />
                )}
                确认清空
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="h-6 px-2 text-xs text-muted-foreground"
                onClick={() => setConfirmClear(false)}
              >
                取消
              </Button>
            </div>
          ) : (
            <Button
              variant="ghost"
              size="sm"
              className="ml-auto h-6 px-2 text-xs text-muted-foreground hover:text-destructive"
              disabled={busy !== null}
              onClick={() => setConfirmClear(true)}
            >
              <Trash2 className="size-3" />
              一键清空
            </Button>
          ))}
      </div>

      {loading ? (
        <p className="flex items-center gap-1 text-xs text-muted-foreground">
          <Loader2 className="size-3 animate-spin" /> 加载中…
        </p>
      ) : rules.length === 0 ? (
        <p className="text-xs text-muted-foreground">本阶段暂无合并规则（提示词未被本 run 追加校正）。</p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {rules.map((rule) => (
            <li
              key={rule}
              className="group flex items-start gap-2 rounded-md border border-border p-2"
            >
              <span className="flex-1 whitespace-pre-wrap break-words text-xs text-foreground">
                {rule}
              </span>
              <Button
                variant="ghost"
                size="icon"
                className="size-6 shrink-0 text-muted-foreground opacity-0 transition hover:text-destructive group-hover:opacity-100"
                title="移除该规则（还原）"
                disabled={busy !== null}
                onClick={() => removeOne(rule)}
              >
                {busy === rule ? (
                  <Loader2 className="size-3 animate-spin" />
                ) : (
                  <RotateCcw className="size-3.5" />
                )}
              </Button>
            </li>
          ))}
        </ul>
      )}

      {msg && <p className="text-xs text-muted-foreground">{msg}</p>}
    </div>
  )
}
