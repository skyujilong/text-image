import { useMemo, useState } from 'react'
import {
  ChevronDown,
  ChevronRight,
  Layers,
  RotateCcw,
  Save,
  Trash2,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'
import { api } from '@/api/client'
import { useRunStore } from '@/store/runStore'
import { useNarrationPresets } from '@/hooks/useNarrationPresets'

/** 内置解说方案预设（后端 list_scheme_presets 下发，含默认模板正文）。 */
export interface NarrationSchemePreset {
  key: string
  label: string
  description: string
  adapt_script_template: string
  scene_change_template: string
}

interface Props {
  runId: string
  chapterCount?: number
  defaultGroupSize?: number
  maxGroupSize?: number
  schemes?: NarrationSchemePreset[]
  defaultScheme?: string
}

/** useNarrationSelection 暴露的选择态 API（供子组件消费）。 */
interface NarrationSelection {
  schemeKey: string
  preset: NarrationSchemePreset | undefined
  selectScheme: (key: string) => void
  applyPreset: (adapt: string, scene: string, baseKey: string) => void
  adaptTpl: string
  setAdaptTpl: (v: string) => void
  sceneTpl: string
  setSceneTpl: (v: string) => void
  resetTemplates: () => void
  /** 用户是否手改过模板（或应用了「我的预设」）——决定 resume 是否回传 narration_templates 覆盖槽。 */
  userEdited: boolean
}

/**
 * 解说方案选择 + run 内模板自定义的状态逻辑。
 * 切换内置方案或应用「我的预设」时重置编辑区；userEdited 表示用户手改过模板（未改则展示 live 默认、resume 不回传）。
 */
function useNarrationSelection(
  schemes: NarrationSchemePreset[],
  defaultScheme?: string,
): NarrationSelection {
  const [schemeKey, setSchemeKey] = useState(defaultScheme ?? schemes[0]?.key ?? '')
  const preset = useMemo(
    () => schemes.find((s) => s.key === schemeKey),
    [schemes, schemeKey],
  )
  // null = 用户未手改：展示时回落到当前内置方案的 live 模板（随 interrupt payload 刷新，
  // 不会残留上一个 run/上次加载的旧模板）。非 null = 用户显式编辑 / 应用了「我的预设」，
  // 作为该 run 的覆盖槽在 resume 时回传；未手改则不回传，后端按 scheme 现取源码。
  const [adaptEdit, setAdaptEdit] = useState<string | null>(null)
  const [sceneEdit, setSceneEdit] = useState<string | null>(null)

  const adaptTpl = adaptEdit ?? preset?.adapt_script_template ?? ''
  const sceneTpl = sceneEdit ?? preset?.scene_change_template ?? ''

  const selectScheme = (key: string) => {
    setSchemeKey(key)
    // 切内置方案 → 丢弃手改，展示新方案的 live 默认
    setAdaptEdit(null)
    setSceneEdit(null)
  }
  // 应用已保存预设：作为显式编辑载入（会随 resume 回传），schemeKey 置为它的 base_scheme
  // （供描述展示 + resume 的 narration_scheme）。
  const applyPreset = (adapt: string, scene: string, baseKey: string) => {
    setSchemeKey(baseKey)
    setAdaptEdit(adapt)
    setSceneEdit(scene)
  }
  const resetTemplates = () => {
    setAdaptEdit(null)
    setSceneEdit(null)
  }
  const userEdited = adaptEdit !== null || sceneEdit !== null

  return {
    schemeKey,
    preset,
    selectScheme,
    applyPreset,
    adaptTpl,
    setAdaptTpl: (v: string) => setAdaptEdit(v),
    sceneTpl,
    setSceneTpl: (v: string) => setSceneEdit(v),
    resetTemplates,
    userEdited,
  }
}

/** 单个模板编辑框（等宽字体，占位符可见）。 */
function TemplateField({
  label,
  value,
  onChange,
}: {
  label: string
  value: string
  onChange: (v: string) => void
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-xs font-medium text-foreground">{label}</span>
      <Textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="min-h-40 font-mono text-xs"
        spellCheck={false}
      />
    </div>
  )
}

/** 解说方案区：内置方案选择 + 我的预设 + 高级模板编辑 + 另存为预设。 */
function NarrationSchemeSection({
  nar,
  schemes,
}: {
  nar: NarrationSelection
  schemes: NarrationSchemePreset[]
}) {
  const { presets, savePreset, deletePreset } = useNarrationPresets()
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [saving, setSaving] = useState(false)
  const [presetName, setPresetName] = useState('')
  const [savingBusy, setSavingBusy] = useState(false)

  const handleSavePreset = async () => {
    const name = presetName.trim()
    if (!name || savingBusy) return
    setSavingBusy(true)
    try {
      await savePreset({
        name,
        base_scheme: nar.schemeKey,
        adapt_script_template: nar.adaptTpl,
        scene_change_template: nar.sceneTpl,
      })
      setSaving(false)
      setPresetName('')
    } catch (e) {
      console.error('save preset failed', e)
    } finally {
      setSavingBusy(false)
    }
  }

  return (
    <>
      {/* 内置方案 */}
      <div className="flex flex-col gap-2">
        <span className="text-sm font-medium text-foreground">解说方案</span>
        <div className="flex flex-wrap gap-2">
          {schemes.map((s) => (
            <Button
              key={s.key}
              variant={s.key === nar.schemeKey ? 'default' : 'outline'}
              size="sm"
              aria-pressed={s.key === nar.schemeKey}
              onClick={() => nar.selectScheme(s.key)}
            >
              {s.label}
            </Button>
          ))}
        </div>
        {nar.preset && (
          <p className="text-xs text-muted-foreground">{nar.preset.description}</p>
        )}
      </div>

      {/* 我的预设 */}
      {presets.length > 0 && (
        <div className="flex flex-col gap-2">
          <span className="text-sm font-medium text-foreground">我的预设</span>
          <div className="flex flex-wrap gap-2">
            {presets.map((p) => (
              <div
                key={p.id}
                className="group flex items-center rounded-md border border-input"
              >
                <Button
                  variant="ghost"
                  size="sm"
                  className="rounded-r-none"
                  onClick={() =>
                    nar.applyPreset(
                      p.adapt_script_template,
                      p.scene_change_template,
                      p.base_scheme,
                    )
                  }
                >
                  {p.name}
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-8 rounded-l-none text-muted-foreground opacity-0 transition group-hover:opacity-100 hover:text-destructive"
                  aria-label={`删除预设 ${p.name}`}
                  onClick={() => void deletePreset(p.id)}
                >
                  <Trash2 className="size-3.5" />
                </Button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 自定义模板（高级） */}
      <div className="flex flex-col gap-2">
        <Button
          variant="ghost"
          size="sm"
          className="w-fit px-1 text-muted-foreground"
          onClick={() => setShowAdvanced((v) => !v)}
        >
          {showAdvanced ? (
            <ChevronDown className="size-4" />
          ) : (
            <ChevronRight className="size-4" />
          )}
          自定义解说模板（高级，仅本次运行）
          {nar.userEdited && (
            <span className="ml-1 size-2 rounded-full bg-primary" aria-label="已修改" />
          )}
        </Button>
        {showAdvanced && (
          <div className="flex flex-col gap-3 rounded-md border border-border p-3">
            <p className="text-xs text-muted-foreground">
              直接编辑本方案的 prompt 原文。<code>%%CHAPTER_TEXT%%</code>、
              <code>%%SCRIPT_LINES%%</code> 等占位符会在运行时替换，请勿删除。
            </p>
            <TemplateField
              label="口播脚本模板（adapt_script）"
              value={nar.adaptTpl}
              onChange={nar.setAdaptTpl}
            />
            <TemplateField
              label="换图点模板（scene_change）"
              value={nar.sceneTpl}
              onChange={nar.setSceneTpl}
            />
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="ghost"
                size="sm"
                className="text-muted-foreground"
                onClick={nar.resetTemplates}
                disabled={!nar.userEdited}
              >
                <RotateCcw className="size-4" />
                恢复预设
              </Button>
              {saving ? (
                <div className="flex items-center gap-2">
                  <Input
                    value={presetName}
                    onChange={(e) => setPresetName(e.target.value)}
                    placeholder="预设名称"
                    className="h-8 w-44"
                    autoFocus
                  />
                  <Button
                    size="sm"
                    onClick={() => void handleSavePreset()}
                    disabled={!presetName.trim() || savingBusy}
                  >
                    保存
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setSaving(false)
                      setPresetName('')
                    }}
                  >
                    取消
                  </Button>
                </div>
              ) : (
                <Button
                  variant="ghost"
                  size="sm"
                  className="text-muted-foreground"
                  onClick={() => setSaving(true)}
                >
                  <Save className="size-4" />
                  另存为预设
                </Button>
              )}
            </div>
          </div>
        )}
      </div>
    </>
  )
}

/**
 * 剧本化设置面板：剧本化前选择「合并粒度 N」+「解说方案」，可自定义模板并另存为跨 run 预设。
 * 全局固定粒度 N（1..maxGroupSize，默认 defaultGroupSize=1）。
 * resume 值 {group_size, narration_scheme, narration_templates}（后端校验）；成功后 setActiveInteraction(null)。
 * 由右侧常驻区渲染（body-only，无 Sheet 包装），结构对齐 ChapterAdvancePanel。
 */
export default function ChapterGroupingPanel({
  runId,
  chapterCount,
  defaultGroupSize = 1,
  maxGroupSize = 5,
  schemes,
  defaultScheme,
}: Props) {
  const { setActiveInteraction, activeInteraction } = useRunStore()
  const [groupSize, setGroupSize] = useState(defaultGroupSize)
  const [submitting, setSubmitting] = useState(false)

  const schemeList = schemes ?? []
  const hasSchemes = schemeList.length > 0
  const nar = useNarrationSelection(schemeList, defaultScheme)

  const options = Array.from({ length: maxGroupSize }, (_, i) => i + 1)
  const groupCount =
    chapterCount != null ? Math.ceil(chapterCount / groupSize) : null

  const handleSubmit = async () => {
    if (!activeInteraction || submitting) return
    setSubmitting(true)
    try {
      const resumeValue: Record<string, unknown> = { group_size: groupSize }
      if (hasSchemes) {
        resumeValue.narration_scheme = nar.schemeKey
        // 静态默认：用户没手改模板就不回传 narration_templates，让后端按 scheme 现取 live 源码
        // （改 narration_schemes.py 即时生效、不用新开 run）。只有显式编辑 / 应用预设时才回传覆盖槽。
        if (nar.userEdited) {
          resumeValue.narration_templates = {
            adapt_script: nar.adaptTpl,
            scene_change: nar.sceneTpl,
          }
        }
      }
      await api.resumeRun(
        runId,
        activeInteraction.scope,
        activeInteraction.thread_id,
        resumeValue,
      )
      setActiveInteraction(null)
    } catch (e) {
      console.error('resume failed', e)
      setSubmitting(false)
    }
  }

  return (
    <div className="flex flex-col h-full">
      <div className="px-6 pt-6">
        <h2 className="text-lg font-semibold text-foreground">剧本化设置</h2>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-4">
        <div className="flex flex-col gap-5">
          {hasSchemes && <NarrationSchemeSection nar={nar} schemes={schemeList} />}

          {/* 合并粒度 */}
          <div className="flex flex-col gap-2">
            <span className="text-sm font-medium text-foreground">合并粒度</span>
            <p className="text-sm text-muted-foreground">
              选择将连续几个章节合并为一组做剧本化。默认 1（单章，保持现状），末组不足自成一组。
            </p>
            <div className="flex flex-wrap gap-2">
              {options.map((n) => (
                <Button
                  key={n}
                  variant={n === groupSize ? 'default' : 'outline'}
                  size="icon"
                  aria-pressed={n === groupSize}
                  onClick={() => setGroupSize(n)}
                  className={cn(n === groupSize && 'ring-2 ring-ring/40')}
                >
                  {n}
                </Button>
              ))}
            </div>
            {groupCount != null && (
              <p className="text-sm text-muted-foreground">
                共 {chapterCount} 章 → 约 {groupCount} 组（每组最多 {groupSize} 章）
              </p>
            )}
          </div>
        </div>
      </div>

      <div className="flex flex-col-reverse sm:flex-row sm:justify-end px-6 pb-6 gap-2">
        <Button variant="default" onClick={handleSubmit} disabled={submitting}>
          <Layers className="size-4" />
          确认设置
        </Button>
      </div>
    </div>
  )
}
