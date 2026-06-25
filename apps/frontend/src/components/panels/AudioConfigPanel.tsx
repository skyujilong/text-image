import { useEffect, useState } from 'react'
import { useForm } from 'react-hook-form'
import { z } from 'zod'
import { zodResolver } from '@hookform/resolvers/zod'
import { AudioLines, Mic, Upload } from 'lucide-react'
import {
  Form, FormControl, FormField, FormItem, FormLabel, FormMessage,
} from '@/components/ui/form'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'
import { api, type VoicePreset } from '@/api/client'
import { useRunStore } from '@/store/runStore'

const schema = z.object({
  language: z.string(),
  guidance_scale: z.number().min(0).max(5),
  speaker_scale: z.number().min(0).max(5),
})

type FormValues = z.infer<typeof schema>

// 「默认声音」哨兵值：Select 不允许空字符串 value，用它表示不指定 voice_name（走 dots 默认声音）。
const DEFAULT_VOICE = '__default__'

// 语言下拉选项（value 为 dots.tts 接受的合法值：语言代码或 auto_detect）。
// 中文小说项目，默认中文 zh；需自动判定时选「自动判定」。
const LANGUAGE_OPTIONS = [
  { value: 'zh', label: '中文' },
  { value: 'en', label: '英文' },
  { value: 'ja', label: '日文' },
  { value: 'auto_detect', label: '自动判定' },
]

interface Props {
  runId: string
  current?: Partial<FormValues> & { voice_name?: string }
}

/**
 * 全局合成参数配置面板（dots.tts 单播，整本书一份）。
 * 仅在 audio_config 为空时由 configure_audio 节点 interrupt 弹出；已配则节点跳过、不再弹。
 * resume {language, guidance_scale, speaker_scale, voice_name?} → 写回 MainGraphState.audio_config。
 *
 * 音色两种用法并存：
 * - 选择已有音色：挂载时拉 dots 已保存的音色预设，下拉直接选。
 * - 上传参考音色：上传音频经后端代理存为 dots 预设，成功后刷新列表并自动选中。
 * 选「默认声音」则不传 voice_name，用 dots 默认声音。language 为下拉选择（默认中文 zh）。
 * 由右侧常驻区渲染（body-only，无 Sheet 包装）。
 */
export default function AudioConfigPanel({ runId, current }: Props) {
  const { setActiveInteraction, activeInteraction } = useRunStore()
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      language: current?.language ?? 'zh',
      guidance_scale: current?.guidance_scale ?? 1.2,
      speaker_scale: current?.speaker_scale ?? 1.5,
    },
  })

  // 音色列表 + 当前选中
  const [voices, setVoices] = useState<VoicePreset[]>([])
  const [selectedVoice, setSelectedVoice] = useState<string>(current?.voice_name ?? DEFAULT_VOICE)
  const [voicesError, setVoicesError] = useState<string | null>(null)

  // 上传参考音色子表单状态
  const [uploadName, setUploadName] = useState('')
  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [uploadPromptText, setUploadPromptText] = useState('')
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)

  // 挂载时拉取已有音色预设；失败暴露错误而非静默空列表
  useEffect(() => {
    let alive = true
    api
      .listVoices()
      .then((list) => {
        if (alive) setVoices(list)
      })
      .catch((e) => {
        if (alive) setVoicesError(e instanceof Error ? e.message : String(e))
      })
    return () => {
      alive = false
    }
  }, [])

  // 上传参考音色 → 存为 dots 预设 → 刷新列表并自动选中（参考三视图面板「先上传拿结果再用」）
  const handleUpload = async () => {
    if (!uploadName.trim() || !uploadFile) return
    setUploading(true)
    setUploadError(null)
    try {
      const created = await api.createVoice(uploadName.trim(), uploadFile, uploadPromptText.trim() || undefined)
      const list = await api.listVoices()
      setVoices(list)
      setSelectedVoice(created.name)
      // 清空上传子表单
      setUploadName('')
      setUploadFile(null)
      setUploadPromptText('')
    } catch (e) {
      setUploadError(e instanceof Error ? e.message : String(e))
    } finally {
      setUploading(false)
    }
  }

  const onSubmit = async (values: FormValues) => {
    if (!activeInteraction) return
    try {
      // language 为下拉选定的合法值（zh/en/ja/auto_detect），直接透传给 dots
      const payload: Record<string, unknown> = {
        language: values.language,
        guidance_scale: values.guidance_scale,
        speaker_scale: values.speaker_scale,
      }
      // 选了具体音色才传 voice_name；默认声音则不传（避免覆盖 dots 默认）
      if (selectedVoice && selectedVoice !== DEFAULT_VOICE) payload.voice_name = selectedVoice
      await api.resumeRun(runId, activeInteraction.scope, activeInteraction.thread_id, payload)
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

      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-6">
        {/* 音色选择 + 上传 */}
        <div className="space-y-3 rounded border border-border p-3">
          <div className="flex items-center gap-2 text-sm font-medium text-foreground">
            <Mic className="size-4" />
            音色
          </div>
          <div className="space-y-2">
            <Label>选择音色</Label>
            <Select value={selectedVoice} onValueChange={setSelectedVoice}>
              <SelectTrigger className="w-full">
                <SelectValue placeholder="默认声音（不指定）" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={DEFAULT_VOICE}>默认声音（不指定）</SelectItem>
                {voices.map((v) => (
                  <SelectItem key={v.name} value={v.name}>
                    {v.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {voicesError && (
              <p className="text-xs text-destructive">音色列表加载失败：{voicesError}</p>
            )}
          </div>

          {/* 上传参考音色：新建 dots 预设 */}
          <div className="space-y-2 border-t border-border pt-3">
            <Label className="text-muted-foreground">上传参考音色（新建预设）</Label>
            <Input
              placeholder="音色名（中文/字母/数字/_/-）"
              value={uploadName}
              onChange={(e) => setUploadName(e.target.value)}
              disabled={uploading}
            />
            <Input
              type="file"
              accept="audio/*"
              disabled={uploading}
              onChange={(e) => setUploadFile(e.target.files?.[0] ?? null)}
            />
            <Input
              placeholder="参考文本（可选，与音频内容匹配）"
              value={uploadPromptText}
              onChange={(e) => setUploadPromptText(e.target.value)}
              disabled={uploading}
            />
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={handleUpload}
              disabled={!uploadName.trim() || !uploadFile || uploading}
            >
              <Upload className="size-4" />
              {uploading ? '上传中...' : '上传并选用'}
            </Button>
            {uploadError && <p className="text-xs text-destructive">上传失败：{uploadError}</p>}
          </div>
        </div>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
            <FormField
              control={form.control}
              name="language"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>语言</FormLabel>
                  <Select value={field.value} onValueChange={field.onChange}>
                    <FormControl>
                      <SelectTrigger className="w-full">
                        <SelectValue placeholder="选择语言" />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {LANGUAGE_OPTIONS.map((opt) => (
                        <SelectItem key={opt.value} value={opt.value}>
                          {opt.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
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
    </div>
  )
}
