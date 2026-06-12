import type { ReactNode } from 'react'
import FlowCanvas from '@/components/flow/FlowCanvas'
import { useRunStore } from '@/store/runStore'
import { useRunStream } from '@/hooks/useRunStream'

interface MainContentProps {
  showNewRunForm: boolean
  newRunFormSlot: ReactNode
}

export default function MainContent({ showNewRunForm, newRunFormSlot }: MainContentProps) {
  const { currentRunId } = useRunStore()
  useRunStream(currentRunId)

  if (showNewRunForm) {
    return (
      <main className="flex-1 flex items-center justify-center bg-gray-50">
        {newRunFormSlot}
      </main>
    )
  }

  if (!currentRunId) {
    return (
      <main className="flex-1 flex items-center justify-center bg-gray-50 text-gray-400">
        请从左侧选择一个 Run，或新建 Run
      </main>
    )
  }

  return (
    <main className="flex-1 relative">
      <FlowCanvas />
    </main>
  )
}
