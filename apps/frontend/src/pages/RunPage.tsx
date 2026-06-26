import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import Sidebar from '@/components/layout/Sidebar'
import MainContent from '@/components/layout/MainContent'
import InteractionDispatcher from '@/components/panels/InteractionDispatcher'
import StateInspector from '@/components/panels/StateInspector'
import StartRunForm from '@/components/forms/StartRunForm'
import { useRunStore } from '@/store/runStore'
import type { RunMeta } from '@/api/client'

export default function RunPage() {
  const { runId } = useParams<{ runId: string }>()
  const [showNewRunForm, setShowNewRunForm] = useState(false)
  const [cloneParams, setCloneParams] = useState<Record<string, unknown> | null>(null)
  const { currentRunId, setCurrentRunId, inspectingNode, setInspectingNode } = useRunStore()

  // URL param → store sync：路由变化时更新 currentRunId
  useEffect(() => {
    if (runId && runId !== currentRunId) {
      setCurrentRunId(runId)
    }
  }, [runId, currentRunId, setCurrentRunId])

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
