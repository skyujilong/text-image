import type { UseFormReturn } from 'react-hook-form'
import { FormControl, FormField, FormItem, FormLabel, FormMessage } from '@/components/ui/form'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import type { StartRunFormValues } from '@/components/forms/startRunSchema'

interface Props {
  form: UseFormReturn<StartRunFormValues>
}

/** 建 run 的参数字段（两列布局）：左侧基础配置 + 右侧长文本配置。 */
export default function NovelParamsFields({ form }: Props) {
  return (
    <div className="grid grid-cols-2 gap-6">
      {/* 左侧：核心配置 + 基础信息 */}
      <div className="space-y-4">
        <FormField
          control={form.control}
          name="novel_title"
          render={({ field }) => (
            <FormItem>
              <FormLabel>小说标题</FormLabel>
              <FormControl>
                <Input {...field} />
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
                  <Input type="number" min={1} {...field} onChange={(e) => field.onChange(e.target.valueAsNumber)} />
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
                <FormLabel>结束章节</FormLabel>
                <FormControl>
                  <Input
                    type="number"
                    min={1}
                    value={field.value ?? ''}
                    onChange={(e) => field.onChange(e.target.value ? e.target.valueAsNumber : null)}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
        </div>

        <FormField
          control={form.control}
          name="genre"
          render={({ field }) => (
            <FormItem>
              <FormLabel>题材类型</FormLabel>
              <FormControl>
                <Input {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField
          control={form.control}
          name="writing_style"
          render={({ field }) => (
            <FormItem>
              <FormLabel>写作风格</FormLabel>
              <FormControl>
                <Input {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField
          control={form.control}
          name="target_audience"
          render={({ field }) => (
            <FormItem>
              <FormLabel>目标受众</FormLabel>
              <FormControl>
                <Input {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField
          control={form.control}
          name="core_tone"
          render={({ field }) => (
            <FormItem>
              <FormLabel>核心基调</FormLabel>
              <FormControl>
                <Input {...field} />
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
                  <Input {...field} />
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
                  <Input {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
        </div>
      </div>

      {/* 右侧：长文本配置 */}
      <div className="space-y-4">
        <FormField
          control={form.control}
          name="core_theme"
          render={({ field }) => (
            <FormItem>
              <FormLabel>核心主题</FormLabel>
              <FormControl>
                <Textarea rows={3} {...field} />
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
                <Textarea rows={4} {...field} />
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
                <Textarea rows={3} {...field} />
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
                <Textarea rows={4} {...field} />
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
                <Textarea rows={4} {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
      </div>
    </div>
  )
}
