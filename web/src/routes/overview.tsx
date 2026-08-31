import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useNavigate } from "react-router-dom"
import { AlertTriangle, Loader2 } from "lucide-react"
import { toast } from "sonner"

import { Empty, Failed, Loading, PageTitle, SectionTitle, Stat } from "@/components/page"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { ApiError, get, post, type Overview } from "@/lib/api"
import { outcomeTone, plural } from "@/lib/format"

export default function OverviewPage() {
  const navigate = useNavigate()
  const queries = useQueryClient()
  const { data, error, isPending } = useQuery({
    queryKey: ["overview"],
    queryFn: () => get<Overview>("/overview"),
    // A scan started here runs for minutes in another process; the page has to notice it end.
    refetchInterval: 15_000,
  })

  const scan = useMutation({
    mutationFn: (board: string) => post<{ run_id: string }>(`/scan/${board}`),
    onSuccess: ({ run_id }) => navigate(`/scan/${run_id}`),
    onError: (failure: ApiError) => toast.error(failure.message),
  })

  const scanAll = useMutation({
    mutationFn: () => post<{ boards: string[] }>("/scan-all"),
    onSuccess: ({ boards }) => {
      toast.success(`Scanning ${boards.join(", ")}`)
      queries.invalidateQueries({ queryKey: ["overview"] })
    },
    onError: (failure: ApiError) => toast.error(failure.message),
  })

  if (error) return <Failed error={error} />
  if (isPending || !data) return <Loading />

  const busy = (board: string) => data.locks[board]

  return (
    <>
      <PageTitle>Overview</PageTitle>

      <div className="flex flex-wrap gap-3">
        <Stat value={data.totals.known} label="jobs known" />
        <Stat value={data.totals.new} label="new in last run" />
        <Stat value={data.totals.runs} label="scans recorded" />
      </div>

      {data.scheduled.length > 0 && !data.timer.loaded ? (
        <Alert variant="destructive" className="mt-5">
          <AlertTriangle />
          <AlertTitle>Nothing will run on its own</AlertTitle>
          <AlertDescription>
            {plural(data.scheduled.length, "board")} scheduled, but the timer is not loaded.{" "}
            <Link to="/schedule" className="underline underline-offset-4">
              Schedule
            </Link>
          </AlertDescription>
        </Alert>
      ) : null}

      <SectionTitle>By board</SectionTitle>
      <div className="max-w-4xl overflow-x-auto rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Board</TableHead>
              <TableHead className="text-right">Known</TableHead>
              <TableHead className="text-right">New</TableHead>
              <TableHead className="text-right">Scans</TableHead>
              <TableHead>Last scan</TableHead>
              <TableHead className="text-right">Found then</TableHead>
              <TableHead>Next</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.summaries.map((summary) => {
              const when = data.timetable[summary.board]
              return (
                <TableRow key={summary.board}>
                  <TableCell className="font-medium">
                    <Link to={`/jobs?board=${summary.board}`} className="hover:underline">
                      {summary.board}
                    </Link>
                  </TableCell>
                  {summary.scanned ? (
                    <>
                      <TableCell className="text-right tabular-nums">{summary.known}</TableCell>
                      <TableCell className="text-right tabular-nums">{summary.new || ""}</TableCell>
                      <TableCell className="text-right tabular-nums">{summary.runs}</TableCell>
                      <TableCell>{summary.last_run_display}</TableCell>
                      <TableCell className="text-right tabular-nums">
                        {summary.last_run_jobs}
                      </TableCell>
                    </>
                  ) : (
                    <TableCell colSpan={5} className="text-muted-foreground italic">
                      never scanned
                    </TableCell>
                  )}
                  <TableCell
                    className={
                      when?.overdue
                        ? "text-xs text-amber-600 dark:text-amber-400"
                        : "text-xs text-muted-foreground"
                    }
                  >
                    {when?.enabled ? (when.overdue ? "due now" : when.next_due) : "—"}
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </div>

      <SectionTitle>Run a scan</SectionTitle>
      <div className="flex flex-wrap items-center gap-2">
        {data.scannable.map((board) => (
          <Button
            key={board}
            variant="outline"
            size="sm"
            disabled={!!busy(board) || scan.isPending}
            onClick={() => scan.mutate(board)}
          >
            {scan.isPending && scan.variables === board ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : null}
            {board}
            {busy(board) ? (
              <span className="text-xs text-muted-foreground">scanning</span>
            ) : null}
          </Button>
        ))}
        <Button size="sm" disabled={scanAll.isPending} onClick={() => scanAll.mutate()}>
          {scanAll.isPending ? <Loader2 className="size-3.5 animate-spin" /> : null}
          All enabled
        </Button>
      </div>
      <p className="mt-3 max-w-[70ch] text-xs text-muted-foreground">
        Scans take minutes and keep running if you navigate away. A board already being scanned
        — by the cron, a terminal, or another trigger — cannot be started again. Scanning all
        boards runs them concurrently, but never more than one crawl per host: they are separate
        sites, so this costs no extra requests to any of them.
      </p>

      <SectionTitle>Recent scans</SectionTitle>
      <div className="max-w-4xl overflow-x-auto rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Started</TableHead>
              <TableHead>Board</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Trigger</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.history.map((run) => (
              <TableRow key={run.id}>
                <TableCell>{run.display_started}</TableCell>
                <TableCell className="text-muted-foreground">{run.board}</TableCell>
                <TableCell className={outcomeTone(run.status)}>
                  {run.status}
                  {run.exit_code ? (
                    <span className="text-muted-foreground"> ({run.exit_code})</span>
                  ) : null}
                </TableCell>
                <TableCell>
                  <Badge variant="secondary">{run.trigger}</Badge>
                </TableCell>
                <TableCell>
                  {run.has_log ? (
                    <Link to={`/scan/${run.id}`} className="text-sm underline underline-offset-4">
                      log
                    </Link>
                  ) : null}
                </TableCell>
              </TableRow>
            ))}
            {data.history.length === 0 ? (
              <TableRow>
                <TableCell colSpan={5}>
                  <Empty>No scans recorded.</Empty>
                </TableCell>
              </TableRow>
            ) : null}
          </TableBody>
        </Table>
      </div>
      <p className="mt-3 max-w-[70ch] text-xs text-muted-foreground">
        Every scan is listed whatever started it — the trigger column says which. Only scans
        started from the dashboard have a captured log.{" "}
        <Link to="/runs" className="underline underline-offset-4">
          Full history →
        </Link>
      </p>
    </>
  )
}
