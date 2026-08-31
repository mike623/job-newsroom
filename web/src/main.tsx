import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { BrowserRouter, Route, Routes } from "react-router-dom"

import { Layout } from "@/components/layout"
import { ThemeProvider } from "@/components/theme"
import { Toaster } from "@/components/ui/sonner"
import { TooltipProvider } from "@/components/ui/tooltip"
import Ingest from "@/routes/ingest"
import JobDetail from "@/routes/job-detail"
import Jobs from "@/routes/jobs"
import Overview from "@/routes/overview"
import Runs from "@/routes/runs"
import Scan from "@/routes/scan"
import Schedule from "@/routes/schedule"
import "./index.css"

// Everything is recomputed from the report files per request, and a scan takes minutes: there
// is nothing to gain from a long cache and something to lose from a stale one.
const client = new QueryClient({
  defaultOptions: { queries: { staleTime: 5_000, refetchOnWindowFocus: true, retry: 1 } },
})

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider>
      <QueryClientProvider client={client}>
        <TooltipProvider>
          <BrowserRouter>
            <Routes>
              <Route element={<Layout />}>
                <Route path="/" element={<Overview />} />
                <Route path="/jobs" element={<Jobs />} />
                <Route path="/jobs/:board/:jobId" element={<JobDetail />} />
                <Route path="/runs" element={<Runs />} />
                <Route path="/schedule" element={<Schedule />} />
                <Route path="/ingest" element={<Ingest />} />
                <Route path="/scan/:runId" element={<Scan />} />
                <Route path="*" element={<Overview />} />
              </Route>
            </Routes>
          </BrowserRouter>
        </TooltipProvider>
        <Toaster position="top-right" />
      </QueryClientProvider>
    </ThemeProvider>
  </StrictMode>,
)
