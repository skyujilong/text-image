import { useState } from 'react'
import Sidebar from '@/components/layout/Sidebar'
import MainContent from '@/components/layout/MainContent'
import InteractionDispatcher from '@/components/panels/InteractionDispatcher'
import StartRunForm from '@/components/forms/StartRunForm'
import { useRunStore } from '@/store/runStore'

export default function RunPage() {
  const [showNewRunForm, setShowNewRunForm] = useState(false)
  const { currentRunId } = useRunStore()

  const handleStarted = (_runId: string) => {
    setShowNewRunForm(false)
  }

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar onNewRun={() => setShowNewRunForm(true)} />
      <MainContent
        showNewRunForm={showNewRunForm}
        newRunFormSlot={
          <StartRunForm
            onStarted={handleStarted}
            onCancel={() => setShowNewRunForm(false)}
          />
        }
      />
      {currentRunId && <InteractionDispatcher runId={currentRunId} />}
    </div>
  )
}
