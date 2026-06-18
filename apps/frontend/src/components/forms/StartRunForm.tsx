import { useEffect, useState } from 'react'
import { useForm } from 'react-hook-form'
import { z } from 'zod'
import { zodResolver } from '@hookform/resolvers/zod'
import {
  Form, FormControl, FormField, FormItem, FormLabel, FormMessage,
} from '@/components/ui/form'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Button } from '@/components/ui/button'
import { api } from '@/api/client'
import { useRunStore } from '@/store/runStore'

const schema = z.object({
  novel_dir: z.string().min(1, '请输入小说目录').refine(
    async (dir) => {
      if (!dir) return false
      const res = await api.validatePath(dir)
      return res.exists
    },
    { message: '目录不存在' }
  ),
  novel_title: z.string(),
  genre: z.string(),
  writing_style: z.string(),
  target_audience: z.string(),
  core_tone: z.string(),
  chapter_word_count: z.string(),
  total_word_count: z.string(),
  core_theme: z.string(),
  world_building: z.string(),
  core_conflicts: z.string(),
  overall_outline: z.string(),
  character_profiles: z.string(),
  start_chapter: z.number().int().min(1),
  end_chapter: z.number().int().min(1).optional().nullable(),
})

type FormValues = z.infer<typeof schema>

interface Props {
  onStarted: (runId: string) => void
  onCancel: () => void
  initialValues?: Record<string, unknown>
}

export default function StartRunForm({ onStarted, onCancel, initialValues }: Props) {
  const { upsertRun, setCurrentRunId, resetNodeStatuses, resetDrill } = useRunStore()
  const [browsing, setBrowsing] = useState(false)
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    mode: 'onBlur',
    defaultValues: {
      novel_dir: (initialValues?.novel_dir as string) ?? '',
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

  const novelDir = form.watch('novel_dir')
  const dirValid = form.formState.dirtyFields.novel_dir && !form.formState.errors.novel_dir

  useEffect(() => {
    if (!dirValid || !novelDir) return
    api.getNovelConfig(novelDir)
      .then((cfg) => {
        // 支持两种字段命名映射
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
      })
      .catch(() => { /* 目录存在但无配置文件，忽略 */ })
  }, [dirValid, novelDir])

  const onSubmit = async (values: FormValues) => {
    const { run_id } = await api.startRun({
      novel_dir: values.novel_dir,
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
    upsertRun({
      run_id,
      novel_dir: values.novel_dir,
      novel_title: values.novel_title || run_id.slice(0, 8),
      status: 'pending',
      created_at: new Date().toISOString(),
    })
    setCurrentRunId(run_id)
    resetNodeStatuses()
    resetDrill()
    onStarted(run_id)
  }

  const configDisabled = !dirValid

  const handleBrowse = async () => {
    setBrowsing(true)
    try {
      const { path } = await api.browseFolder()
      form.setValue('novel_dir', path, { shouldValidate: true, shouldDirty: true })
    } catch {
      // 用户取消或后端不支持，忽略
    } finally {
      setBrowsing(false)
    }
  }

  return (
    <div className="w-full max-w-lg p-6 bg-white rounded-xl shadow">
      <h2 className="text-lg font-semibold mb-4">{initialValues ? '修改参数重跑' : '新建 Run'}</h2>
      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
          <FormField
            control={form.control}
            name="novel_dir"
            render={({ field }) => (
              <FormItem>
                <FormLabel>小说目录</FormLabel>
                <div className="flex gap-2">
                  <FormControl>
                    <Input placeholder="/path/to/your/novel" {...field} />
                  </FormControl>
                  <Button
                    type="button"
                    variant="outline"
                    disabled={browsing}
                    onClick={handleBrowse}
                    className="shrink-0"
                  >
                    {browsing ? '选择中…' : '浏览…'}
                  </Button>
                </div>
                <FormMessage />
              </FormItem>
            )}
          />

          <FormField
            control={form.control}
            name="novel_title"
            render={({ field }) => (
              <FormItem>
                <FormLabel>小说标题</FormLabel>
                <FormControl>
                  <Input disabled={configDisabled} placeholder="（选择目录后自动填充）" {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />

          <FormField
            control={form.control}
            name="genre"
            render={({ field }) => (
              <FormItem>
                <FormLabel>题材类型</FormLabel>
                <FormControl>
                  <Input disabled={configDisabled} placeholder="（选择目录后自动填充）" {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />

          <div className="flex gap-4">
            <FormField
              control={form.control}
              name="writing_style"
              render={({ field }) => (
                <FormItem className="flex-1">
                  <FormLabel>写作风格</FormLabel>
                  <FormControl>
                    <Input disabled={configDisabled} placeholder="（选择目录后自动填充）" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="target_audience"
              render={({ field }) => (
                <FormItem className="flex-1">
                  <FormLabel>目标受众</FormLabel>
                  <FormControl>
                    <Input disabled={configDisabled} placeholder="（选择目录后自动填充）" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          </div>

          <FormField
            control={form.control}
            name="core_tone"
            render={({ field }) => (
              <FormItem>
                <FormLabel>核心基调</FormLabel>
                <FormControl>
                  <Input disabled={configDisabled} placeholder="（选择目录后自动填充）" {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />

          <div className="flex gap-4">
            <FormField
              control={form.control}
              name="chapter_word_count"
              render={({ field }) => (
                <FormItem className="flex-1">
                  <FormLabel>单章字数</FormLabel>
                  <FormControl>
                    <Input disabled={configDisabled} placeholder="（选择目录后自动填充）" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="total_word_count"
              render={({ field }) => (
                <FormItem className="flex-1">
                  <FormLabel>总字数</FormLabel>
                  <FormControl>
                    <Input disabled={configDisabled} placeholder="（选择目录后自动填充）" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          </div>

          <FormField
            control={form.control}
            name="core_theme"
            render={({ field }) => (
              <FormItem>
                <FormLabel>核心主题</FormLabel>
                <FormControl>
                  <Textarea
                    disabled={configDisabled}
                    placeholder="（选择目录后自动填充）"
                    rows={3}
                    {...field}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />

          <FormField
            control={form.control}
            name="world_building"
            render={({ field }) => (
              <FormItem>
                <FormLabel>世界观设定</FormLabel>
                <FormControl>
                  <Textarea
                    disabled={configDisabled}
                    placeholder="（选择目录后自动填充）"
                    rows={4}
                    {...field}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />

          <FormField
            control={form.control}
            name="core_conflicts"
            render={({ field }) => (
              <FormItem>
                <FormLabel>核心冲突</FormLabel>
                <FormControl>
                  <Textarea
                    disabled={configDisabled}
                    placeholder="（选择目录后自动填充）"
                    rows={3}
                    {...field}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />

          <FormField
            control={form.control}
            name="overall_outline"
            render={({ field }) => (
              <FormItem>
                <FormLabel>整体大纲</FormLabel>
                <FormControl>
                  <Textarea
                    disabled={configDisabled}
                    placeholder="（选择目录后自动填充）"
                    rows={5}
                    {...field}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />

          <FormField
            control={form.control}
            name="character_profiles"
            render={({ field }) => (
              <FormItem>
                <FormLabel>人物设定</FormLabel>
                <FormControl>
                  <Textarea
                    disabled={configDisabled}
                    placeholder="（选择目录后自动填充）"
                    rows={5}
                    {...field}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />

          <div className="flex gap-4">
            <FormField
              control={form.control}
              name="start_chapter"
              render={({ field }) => (
                <FormItem className="flex-1">
                  <FormLabel>起始章节</FormLabel>
                  <FormControl>
                    <Input
                      type="number"
                      min={1}
                      disabled={configDisabled}
                      {...field}
                      onChange={(e) => field.onChange(e.target.valueAsNumber)}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="end_chapter"
              render={({ field }) => (
                <FormItem className="flex-1">
                  <FormLabel>结束章节（留空=全部）</FormLabel>
                  <FormControl>
                    <Input
                      type="number"
                      min={1}
                      disabled={configDisabled}
                      value={field.value ?? ''}
                      onChange={(e) => field.onChange(e.target.value ? e.target.valueAsNumber : null)}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          </div>

          <div className="flex justify-end gap-3 pt-2">
            <Button type="button" variant="outline" onClick={onCancel}>
              取消
            </Button>
            <Button type="submit" disabled={form.formState.isSubmitting || configDisabled}>
              {form.formState.isSubmitting ? '启动中...' : '开始运行 →'}
            </Button>
          </div>
        </form>
      </Form>
    </div>
  )
}
