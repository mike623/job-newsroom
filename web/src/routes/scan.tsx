import { useEffect, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Link, useParams } from "react-router-dom"
import { ArrowLeft, Loader2 } from "lucide-react"

import { Failed, Loading } from "@/components/page"
import { Badge } from "@/components/ui/badge"
import { get, type ScanRun } from "@/lib/api"
import { outcomeTone } from "@/lib/format"

export default function ScanPage() {
  const { runId = "" } = useParams()
  const [lines, setLines] = useState<string[]>([])
  const [status, setStatus] = useState("")
  const stuckToBottom = useRef(true)

  const { data, error, isPending } = useQuery({
    queryKey: ["scan", runId],
    queryFn: () => get<ScanRun>(`/scan/${runId}`),
  })

  useEffect(() => {
    if (!runId) return
    // The log is a file the runner appends to, streamed as it grows. It survives this page
    // being closed, so re-opening replays it from the start rather than resuming mid-scan.
    const source = new EventSource(`/api/scan/${runId}/log`)
    source.onmessage = (event) => setLines((previous) => [...previous, event.data])
    source.addEventListener("finished", (event) => {
      setStatus((event as MessageEvent).data)
      source.close()
    })
    source.onerror = () => source.close()
    return () => source.close()
  }, [runId])

  useEffect(() => {
    if (stuckToBottom.current) window.scrollTo(0, document.body.scrollHeight)
  }, [lines])

  useEffect(() => {
    const watch = () => {
      stuckToBottom.current =
        window.innerHeight + window.scrollY >= document.body.offsetHeight - 40
    }
    window.addEventListener("scroll", watch)
    return () => window.removeEventListener("scroll", watch)
  }, [])

  if (error) return <Failed error={error} />
  if (isPending || !data) return <Loading rows={3} />

  const outcome = status || data.status

  return (
    <>
      <Link
        to="/"
        className="mb-4 inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-3.5" /> overview
      </Link>

      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold tracking-tight">Scanning {data.board}</h1>
        <Badge variant="outline" className={outcomeTone(outcome)}>
          {outcome === "running" ? <Loader2 className="size-3 animate-spin" /> : null}
          {outcome}
        </Badge>
      </div>
      <p className="mt-1 text-sm text-muted-foreground">started {data.display_started}</p>
      <p className="mt-3 max-w-[70ch] text-xs text-muted-foreground">
        The scan runs in its own process and keeps going if you close this page. Come back to it
        from the overview, or read <code className="rounded bg-muted px-1 py-0.5">{data.log}</code>{" "}
        directly.
      </p>

      <pre className="mt-5 min-h-56 overflow-x-auto rounded-lg border bg-muted/40 p-4 text-xs leading-relaxed break-words whitespace-pre-wrap">
        {lines.length ? lines.join("\n") : "waiting for output…"}
      </pre>
    </>
  )
}
