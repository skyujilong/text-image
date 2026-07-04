import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { ArrowLeft, BookText } from 'lucide-react'
import { Form } from '@/components/ui/form'
import { Button } from '@/components/ui/button'
import { api, type NovelEntry } from '@/api/client'
import { useRunStore } from '@/store/runStore'
import NovelPicker from '@/components/pickers/NovelPicker'
import NovelParamsFields from '@/components/forms/NovelParamsFields'
import { startRunSchema, sourceDirFromInitial, type StartRunFormValues } from '@/components/forms/startRunSchema'

interface Props {
  onStarted: (runId: string) => void
  onCancel: () => void
  initialValues?: Record<string, unknown>
}

export default function StartRunForm({ onStarted, onCancel, initialValues }: Props) {
  const { setRuns, setCurrentRunId, resetNodeStatuses, resetDrill } = useRunStore()
  const initialSourceDir = sourceDirFromInitial(initialValues)
  // 改参重跑（initialValues 带 source_dir）→ 小说已知，直接进配参；否则先选书。
  const [novelChosen, setNovelChosen] = useState(!!initialSourceDir)

  const form = useForm<StartRunFormValues>({
    resolver: zodResolver(startRunSchema),
    mode: 'onBlur',
    defaultValues: {
      source_dir: initialSourceDir,
      novel_title: (initialValues?.novel_title as string) ?? '',
      genre: (initialValues?.genre as string) ?? '',
      writing_style: (initialValues?.writing_style as string) ?? '',
      target_audience: (initialValues?.target_audience as string) ?? '',
      core_tone: (initialValues?.core_tone as string) ?? '',
      chapter_word_count: (initialValues?.chapter_word_count as string) ?? '',
      total_word_count: (initialValues?.total_word_count as string) ?? '',
      core_theme: (initialValues?.core_theme as string) ?? '',
      world_building: (initialValues?.world_building as string) ?? '',
      core_conflicts: (initialValues?.core_conflicts as string) ?? '',
      overall_outline: (initialValues?.overall_outline as string) ?? '',
      character_profiles: (initialValues?.character_profiles as string) ?? '',
      start_chapter: (initialValues?.start_chapter as number) ?? 1,
      end_chapter: (initialValues?.end_chapter as number | null) ?? null,
    },
  })

  const fillFromConfig = (cfg: Record<string, unknown>) => {
    form.setValue('novel_title', (cfg.novel_title ?? cfg.novel_name ?? '') as string)
    form.setValue('genre', (cfg.genre ?? '') as string)
    form.setValue('writing_style', (cfg.writing_style ?? '') as string)
    form.setValue('target_audience', (cfg.target_audience ?? '') as string)
    form.setValue('core_tone', (cfg.core_tone ?? '') as string)
    form.setValue('chapter_word_count', (cfg.chapter_word_count ?? '') as string)
    form.setValue('total_word_count', (cfg.total_word_count ?? '') as string)
    form.setValue('core_theme', (cfg.core_theme ?? '') as string)
    form.setValue('world_building', (cfg.world_building ?? '') as string)
    form.setValue('core_conflicts', (cfg.core_conflicts ?? '') as string)
    form.setValue('overall_outline', (cfg.overall_outline ?? '') as string)
    form.setValue('character_profiles', (cfg.character_profiles ?? '') as string)
  }

  // 选中一本小说：写入 source_dir + 用其 config 预填表单（无 config 也照样进入配参）
  const handlePickNovel = async (novel: NovelEntry) => {
    form.setValue('source_dir', novel.path)
    form.setValue('novel_title', novel.title || novel.name)
    try {
      const cfg = await api.getNovelConfig(novel.path)
      fillFromConfig(cfg)
    } catch {
      // 无 config.json：保留 novel 名作标题，其余留空由用户填
    }
    setNovelChosen(true)
  }

  const backToPicker = () => {
    setNovelChosen(false)
    form.setValue('source_dir', '')
  }

  const onSubmit = async (values: StartRunFormValues) => {
    const { run_id } = await api.startRun({
      source_dir: values.source_dir,
      novel_title: values.novel_title,
      genre: values.genre,
      writing_style: values.writing_style,
      target_audience: values.target_audience,
      core_tone: values.core_tone,
      chapter_word_count: values.chapter_word_count,
      total_word_count: values.total_word_count,
      core_theme: values.core_theme,
      world_building: values.world_building,
      core_conflicts: values.core_conflicts,
      overall_outline: values.overall_outline,
      character_profiles: values.character_profiles,
      start_chapter: values.start_chapter,
      end_chapter: values.end_chapter ?? undefined,
    })
    // 拉取权威 run 列表（含隔离 novel_dir / source_dir），避免手拼不完整 RunMeta
    setRuns(await api.listRuns())
    setCurrentRunId(run_id)
    resetNodeStatuses()
    resetDrill()
    onStarted(run_id)
  }

  const chosenTitle = form.watch('novel_title')
  const chosenPath = form.watch('source_dir')

  return (
    <div className="w-full max-w-5xl p-6 bg-card text-card-foreground border border-border rounded-xl shadow max-h-[90vh] flex flex-col">
      <h2 className="text-lg font-semibold mb-4 shrink-0">{initialValues ? '修改参数重跑' : '新建 Run'}</h2>
      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)} className="flex-1 flex flex-col overflow-hidden">
          {!novelChosen ? (
            <div className="flex-1 overflow-auto pr-2 -mr-2">
              <NovelPicker onPick={handlePickNovel} />
            </div>
          ) : (
            <div className="flex-1 overflow-auto pr-2 -mr-2 space-y-4">
              {/* 已选小说横幅 */}
              <div className="flex items-center gap-2 rounded-lg border border-border bg-accent/40 px-3 py-2">
                <BookText className="size-4 text-muted-foreground shrink-0" />
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium truncate">{chosenTitle || '未命名'}</div>
                  <div className="text-xs text-muted-foreground truncate" title={chosenPath}>{chosenPath}</div>
                </div>
                {!initialValues && (
                  <Button type="button" variant="ghost" size="sm" onClick={backToPicker}>
                    <ArrowLeft className="size-4" />
                    重新选择
                  </Button>
                )}
              </div>

              <NovelParamsFields form={form} />
            </div>
          )}

          {/* 按钮区域 */}
          <div className="flex justify-end gap-3 pt-4 mt-4 border-t border-border shrink-0">
            <Button type="button" variant="outline" onClick={onCancel}>
              取消
            </Button>
            {novelChosen && (
              <Button type="submit" disabled={form.formState.isSubmitting}>
                {form.formState.isSubmitting ? '启动中...' : '开始运行 →'}
              </Button>
            )}
          </div>
        </form>
      </Form>
    </div>
  )
}
