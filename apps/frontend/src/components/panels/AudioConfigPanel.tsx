import { useForm } from 'react-hook-form'
import { z } from 'zod'
import { zodResolver } from '@hookform/resolvers/zod'
import {
  Sheet, SheetContent, SheetHeader, SheetTitle, SheetFooter,
} from '@/components/ui/sheet'
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
  open: boolean
  onClose: () => void
}

/**
 * 全局音色配置面板（单播，整本书一份）。
 * 仅在 audio_config 为空时由 configure_audio 节点 interrupt 弹出；已配则节点跳过、不再弹。
 * resume {voice_type, speed, pitch, volume} → 写回 MainGraphState.audio_config。
 */
export default function AudioConfigPanel({ runId, current, open, onClose }: Props) {
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
      onClose()
    } catch (e) {
      console.error('resume failed', e)
    }
  }

  return (
    <Sheet open={open} onOpenChange={(o: boolean) => !o && onClose()}>
      <SheetContent side="right" className="w-[400px] sm:max-w-[400px]">
        <SheetHeader>
          <SheetTitle>配置音色（configure_audio · 全局单播）</SheetTitle>
        </SheetHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4 py-4">
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
            <SheetFooter>
              <Button type="button" variant="outline" onClick={onClose}>
                取消
              </Button>
              <Button type="submit" disabled={form.formState.isSubmitting}>
                {form.formState.isSubmitting ? '提交中...' : '确认'}
              </Button>
            </SheetFooter>
          </form>
        </Form>
      </SheetContent>
    </Sheet>
  )
}
