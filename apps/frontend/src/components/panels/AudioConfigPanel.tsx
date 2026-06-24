import { useForm } from 'react-hook-form'
import { z } from 'zod'
import { zodResolver } from '@hookform/resolvers/zod'
import { AudioLines } from 'lucide-react'
import {
  Form, FormControl, FormField, FormItem, FormLabel, FormMessage,
} from '@/components/ui/form'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { api } from '@/api/client'
import { useRunStore } from '@/store/runStore'

const schema = z.object({
  language: z.string().optional(),
  guidance_scale: z.number().min(0).max(5),
  speaker_scale: z.number().min(0).max(5),
})

type FormValues = z.infer<typeof schema>

interface Props {
  runId: string
  current?: Partial<FormValues>
}

/**
 * 全局合成参数配置面板（dots.tts 单播，整本书一份）。
 * 仅在 audio_config 为空时由 configure_audio 节点 interrupt 弹出；已配则节点跳过、不再弹。
 * resume {language, guidance_scale, speaker_scale} → 写回 MainGraphState.audio_config。
 * 本期不收音色（voice_name），用 dots 默认声音。language 留空则由 dots 自动判定。
 * 由右侧常驻区渲染（body-only，无 Sheet 包装）。
 */
export default function AudioConfigPanel({ runId, current }: Props) {
  const { setActiveInteraction } = useRunStore()
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      language: current?.language ?? '',
      guidance_scale: current?.guidance_scale ?? 1.2,
      speaker_scale: current?.speaker_scale ?? 1.5,
    },
  })

  const onSubmit = async (values: FormValues) => {
    try {
      // language 留空交给 dots 自动判定，不传空串覆盖
      const payload: Record<string, unknown> = {
        guidance_scale: values.guidance_scale,
        speaker_scale: values.speaker_scale,
      }
      if (values.language?.trim()) payload.language = values.language.trim()
      await api.resumeRun(runId, payload)
      setActiveInteraction(null)
    } catch (e) {
      console.error('resume failed', e)
    }
  }

  return (
    <div className="flex flex-col h-full">
      <div className="px-6 pt-6">
        <h2 className="flex items-center gap-2 text-lg font-semibold text-foreground">
          <AudioLines className="size-4" />
          配置合成参数（dots.tts · 全局单播）
        </h2>
      </div>

      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)} className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
          <FormField
            control={form.control}
            name="language"
            render={({ field }) => (
              <FormItem>
                <FormLabel>语言（留空自动判定）</FormLabel>
                <FormControl>
                  <Input {...field} placeholder="如 zh / en / ja，留空 = auto" />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="guidance_scale"
            render={({ field }) => (
              <FormItem>
                <FormLabel>引导强度 guidance_scale（0 ~ 5）</FormLabel>
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
            name="speaker_scale"
            render={({ field }) => (
              <FormItem>
                <FormLabel>音色强度 speaker_scale（0 ~ 5）</FormLabel>
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
