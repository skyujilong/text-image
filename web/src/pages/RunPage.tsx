import { useState } from 'react'
import Sidebar from '@/components/layout/Sidebar'
import MainContent from '@/components/layout/MainContent'
import InteractionDispatcher from '@/components/panels/InteractionDispatcher'
import StateInspector from '@/components/panels/StateInspector'
import StartRunForm from '@/components/forms/StartRunForm'
import { useRunStore } from '@/store/runStore'
import type { RunMeta } from '@/api/client'

export default function RunPage() {
  const [showNewRunForm, setShowNewRunForm] = useState(false)
  const [cloneParams, setCloneParams] = useState<Record<string, unknown> | null>(null)
  const { currentRunId, inspectingNode, setInspectingNode } = useRunStore()

  const handleStarted = (_runId: string) => {
    setShowNewRunForm(false)
    setCloneParams(null)
  }

  const handleCloneRun = (run: RunMeta) => {
    setCloneParams(run.params)
    setShowNewRunForm(true)
  }

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar onNewRun={() => setShowNewRunForm(true)} onCloneRun={handleCloneRun} />
      <MainContent
        showNewRunForm={showNewRunForm}
        newRunFormSlot={
          <StartRunForm
            initialValues={cloneParams ?? undefined}
            onStarted={handleStarted}
            onCancel={() => { setShowNewRunForm(false); setCloneParams(null) }}
          />
        }
      />
      {currentRunId && <InteractionDispatcher runId={currentRunId} />}
      <StateInspector
        open={!!inspectingNode}
        nodePath={inspectingNode}
        runId={currentRunId}
        onClose={() => setInspectingNode(null)}
      />
    </div>
  )
}
