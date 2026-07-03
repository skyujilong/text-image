import { BrowserRouter, Routes, Route } from 'react-router-dom'
import RunPage from '@/pages/RunPage'
import RenderWorkbenchPage from '@/pages/RenderWorkbenchPage'
import PromptInspectPage from '@/pages/PromptInspectPage'
import PromptEvolutionPage from '@/pages/PromptEvolutionPage'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<RunPage />} />
        <Route path="/runs/:runId" element={<RunPage />} />
        <Route path="/runs/:runId/render" element={<RenderWorkbenchPage />} />
        <Route path="/runs/:runId/prompts" element={<PromptInspectPage />} />
        <Route path="/prompt-evolution" element={<PromptEvolutionPage />} />
      </Routes>
    </BrowserRouter>
  )
}
