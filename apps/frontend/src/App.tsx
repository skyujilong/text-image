import { BrowserRouter, Routes, Route } from 'react-router-dom'
import RunPage from '@/pages/RunPage'
import RenderWorkbenchPage from '@/pages/RenderWorkbenchPage'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<RunPage />} />
        <Route path="/runs/:runId" element={<RunPage />} />
        <Route path="/runs/:runId/render" element={<RenderWorkbenchPage />} />
      </Routes>
    </BrowserRouter>
  )
}
