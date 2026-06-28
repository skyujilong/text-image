import { useEffect, useState, useRef } from 'react'
import { AudioLines, Loader2, Play, Mic } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import { api, fileUrl, type VoicePreset } from '@/api/client'

const DEFAULT_VOICE = '__default__'

const LANGUAGE_OPTIONS = [
  { value: 'zh', label: '中文' },
  { value: 'en', label: '英文' },
  { value: 'ja', label: '日文' },
  { value: 'auto_detect', label: '自动判定' },
]

interface Props {
  runId: string
  chapterId: string
}

/**
 * 音频合成控制面板：音色配置 + 提交合成 + 状态轮询 + 播放试听。
 */
export default function AudioSynthesisPanel({ runId, chapterId }: Props) {
  const [language, setLanguage] = useState('zh')
  const [guidanceScale, setGuidanceScale] = useState(1.2)
  const [speakerScale, setSpeakerScale] = useState(1.5)
  const [selectedVoice, setSelectedVoice] = useState<string>(DEFAULT_VOICE)
  const [voices, setVoices] = useState<VoicePreset[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [audioStatus, setAudioStatus] = useState<string>('pending')
  const [audioUrl, setAudioUrl] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined)

  useEffect(() => {
    api.listVoices().then(setVoices).catch(() => {})
  }, [])

  useEffect(() => {
    api.getAudioStatus(runId, chapterId)
      .then((res) => {
        setAudioStatus(res.status)
        if (res.status === 'done' && res.audio_path) {
          // 加时间戳破浏览器缓存，确保重新合成后播放最新音频
          setAudioUrl(`${fileUrl(res.audio_path)}?t=${Date.now()}`)
        }
      })
      .catch(() => {})
  }, [runId, chapterId])

  const handleSubmit = async () => {
    setSubmitting(true)
    setError(null)
    try {
      const config: Record<string, unknown> = {
        language,
        guidance_scale: guidanceScale,
        speaker_scale: speakerScale,
      }
      if (selectedVoice !== DEFAULT_VOICE) config.voice_name = selectedVoice
      await api.synthesizeAudio(runId, chapterId, config)
      setAudioStatus('synthesizing')
      pollRef.current = setInterval(async () => {
        try {
          const res = await api.getAudioStatus(runId, chapterId)
          setAudioStatus(res.status)
          if (res.status === 'done' && res.audio_path) {
            // 加时间戳破浏览器缓存，确保重新合成后播放最新音频
            setAudioUrl(`${fileUrl(res.audio_path)}?t=${Date.now()}`)
            if (pollRef.current) clearInterval(pollRef.current)
          } else if (res.status === 'error') {
            if (pollRef.current) clearInterval(pollRef.current)
          }
        } catch {
          if (pollRef.current) clearInterval(pollRef.current)
        }
      }, 3000)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSubmitting(false)
    }
  }

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [])

  const isSynthesizing = audioStatus === 'synthesizing' || submitting

  return (
    <div className="flex flex-col h-full p-4 gap-4 overflow-y-auto">
      <div className="flex items-center gap-2">
        <AudioLines className="size-4" />
        <span className="text-sm font-medium">音频合成 · {chapterId}</span>
      </div>

      <div className="space-y-2 rounded border border-border p-3">
        <div className="flex items-center gap-2 text-sm font-medium">
          <Mic className="size-4" />
          音色
        </div>
        <Select value={selectedVoice} onValueChange={setSelectedVoice}>
          <SelectTrigger className="w-full">
            <SelectValue placeholder="默认声音（不指定）" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={DEFAULT_VOICE}>默认声音（不指定）</SelectItem>
            {voices.map((v) => (
              <SelectItem key={v.name} value={v.name}>{v.name}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1">
          <Label className="text-xs">语言</Label>
          <Select value={language} onValueChange={setLanguage}>
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {LANGUAGE_OPTIONS.map((opt) => (
                <SelectItem key={opt.value} value={opt.value}>{opt.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <Label className="text-xs">引导强度</Label>
          <Input
            type="number"
            step="0.1"
            min="0"
            max="5"
            value={guidanceScale}
            onChange={(e) => setGuidanceScale(e.target.valueAsNumber)}
          />
        </div>
        <div className="space-y-1">
          <Label className="text-xs">音色强度</Label>
          <Input
            type="number"
            step="0.1"
            min="0"
            max="5"
            value={speakerScale}
            onChange={(e) => setSpeakerScale(e.target.valueAsNumber)}
          />
        </div>
      </div>

      <Button onClick={handleSubmit} disabled={isSynthesizing}>
        {isSynthesizing ? (
          <Loader2 className="size-4 animate-spin" />
        ) : (
          <Play className="size-4" />
        )}
        {isSynthesizing ? '合成中...' : '提交合成'}
      </Button>

      {error && <p className="text-xs text-destructive">{error}</p>}

      {audioUrl && audioStatus === 'done' && (
        <div className="space-y-2 rounded border border-border p-3">
          <Label className="text-xs">合成完成</Label>
          <audio controls src={audioUrl} className="w-full" />
        </div>
      )}

      {audioStatus === 'synthesizing' && !audioUrl && (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="size-4 animate-spin" />
          正在合成音频，请稍候...
        </div>
      )}
    </div>
  )
}
