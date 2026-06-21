import { useForm } from 'react-hook-form'
import { z } from 'zod'
import { zodResolver } from '@hookform/resolvers/zod'
import {
  Form, FormControl, FormField, FormItem, FormLabel, FormMessage,
} from '@/components/ui/form'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { api } from '@/api/client'
import { useRunStore } from '@/store/runStore'

const schema = z.object({
  voice_type: z.string().min(1, '必填'),
  speed: z.number().min(0.5).max(2.0),
  pitch: z.number().min(-12).max(12),
  volume: z.number().min(0).max(100),
})

type FormValues = z.infer<typeof schema>

interface Props {
  runId: string
  current?: Partial<FormValues>
}

/**
 * 全局音色配置面板（单播，整本书一份）。
 * 仅在 audio_config 为空时由 configure_audio 节点 interrupt 弹出；已配则节点跳过、不再弹。
 * resume {voice_type, speed, pitch, volume} → 写回 MainGraphState.audio_config。
 * 由右侧常驻区渲染（body-only，无 Sheet 包装）。
 */
export default function AudioConfigPanel({ runId, current }: Props) {
  const { setActiveInteraction } = useRunStore()
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      voice_type: current?.voice_type ?? '',
      speed: current?.speed ?? 1.0,
      pitch: current?.pitch ?? 0,
      volume: current?.volume ?? 100,
    },
  })

  const onSubmit = async (values: FormValues) => {
    try {
      await api.resumeRun(runId, values)
      setActiveInteraction(null)
    } catch (e) {
      console.error('resume failed', e)
    }
  }

  return (
    <div className="flex flex-col h-full">
      <div className="px-6 pt-6">
        <h2 className="text-lg font-semibold text-foreground">配置音色（configure_audio · 全局单播）</h2>
      </div>

      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)} className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
          <FormField
            control={form.control}
            name="voice_type"
            render={({ field }) => (
              <FormItem>
                <FormLabel>音色 ID（voice_type）</FormLabel>
                <FormControl>
                  <Input {...field} placeholder="如 zh_female_xxx" />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="speed"
            render={({ field }) => (
              <FormItem>
                <FormLabel>语速（0.5 ~ 2.0）</FormLabel>
                <FormControl>
                  <Input
                    type="number"
                    step="0.1"
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
            name="pitch"
            render={({ field }) => (
              <FormItem>
                <FormLabel>音调（-12 ~ 12）</FormLabel>
                <FormControl>
                  <Input
                    type="number"
                    step="1"
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
            name="volume"
            render={({ field }) => (
              <FormItem>
                <FormLabel>音量（0 ~ 100）</FormLabel>
                <FormControl>
                  <Input
                    type="number"
                    step="1"
                    {...field}
                    onChange={(e) => field.onChange(e.target.valueAsNumber)}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <div className="flex flex-col-reverse sm:flex-row sm:justify-end sm:space-x-2 pt-4 gap-2">
            <Button type="submit" disabled={form.formState.isSubmitting}>
              {form.formState.isSubmitting ? '提交中...' : '确认'}
            </Button>
          </div>
        </form>
      </Form>
    </div>
  )
}
