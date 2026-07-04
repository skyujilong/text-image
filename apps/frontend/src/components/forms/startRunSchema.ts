import { z } from 'zod'

// 建 run 表单校验。source_dir=用户选中的源小说目录（picker 选定后写入）。
export const startRunSchema = z.object({
  source_dir: z.string().min(1, '请选择一本小说'),
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

export type StartRunFormValues = z.infer<typeof startRunSchema>

// 从 initialValues（改参重跑：run.params）取源目录，兼容 legacy run 仅有 novel_dir 的情况。
export function sourceDirFromInitial(initial?: Record<string, unknown>): string {
  if (!initial) return ''
  return (initial.source_dir as string) || (initial.novel_dir as string) || ''
}
