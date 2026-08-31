import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { AlertTriangle, Loader2, Play } from "lucide-react"
import { toast } from "sonner"

import { Failed, Loading, PageTitle, SectionTitle } from "@/components/page"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { ApiError, get, post, type Schedule, type ScheduleRow } from "@/lib/api"

/**
 * The timetable, as one form.
 *
 * Validation is the server's — `schedule.validate` is what the timer itself obeys, and a second
 * copy of those rules here would eventually disagree with it. What comes back on a 400 is the
 * per-board problem list, shown as-is.
 */
export default function SchedulePage() {
  const queries = useQueryClient()
  const { data, error, isPending } = useQuery({
    queryKey: ["schedule"],
    queryFn: () => get<Schedule>("/schedule"),
  })

  const [rows, setRows] = useState<ScheduleRow[]>([])
  const [problems, setProblems] = useState<Record<string, string[]>>({})

  useEffect(() => {
    if (data) setRows(data.rows)
  }, [data])

  const edit = (board: string, patch: Partial<ScheduleRow>) =>
    setRows((previous) =>
      previous.map((row) => (row.board === board ? { ...row, ...patch } : row)),
    )

  const save = useMutation({
    mutationFn: () => {
      const fields: Record<string, string | string[]> = {}
      for (const row of rows) {
        if (row.runnable) fields[`${row.board}.runnable`] = "on"
        if (row.enabled) fields[`${row.board}.enabled`] = "on"
        fields[`${row.board}.mode`] = row.mode
        fields[`${row.board}.at`] = row.at
        fields[`${row.board}.every_minutes`] = row.every_minutes
        fields[`${row.board}.window_from`] = row.window_from
        fields[`${row.board}.window_to`] = row.window_to
        fields[`${row.board}.days`] = row.days.map(String)
      }
      return post<{ changed: string[] }>("/schedule", fields)
    },
    onSuccess: ({ changed }) => {
      setProblems({})
      toast.success(changed.length ? `Saved · ${changed.join(", ")}` : "Schedule saved")
      queries.invalidateQueries({ queryKey: ["schedule"] })
    },
    onError: (failure: ApiError) => {
      const found = (failure.detail as { problems?: Record<string, string[]> })?.problems
      if (found) {
        setProblems(found)
        toast.error("Nothing was saved")
      } else {
        toast.error(failure.message)
      }
    },
  })

  const runDue = useMutation({
    mutationFn: () => post<{ boards: string[] }>("/schedule/run-due"),
    onSuccess: ({ boards }) =>
      boards.length
        ? toast.success(`Started ${boards.join(", ")}`)
        : toast("Nothing is due — every scheduled board has been scanned since its last slot"),
    onError: (failure: ApiError) => toast.error(failure.message),
  })

  const runNow = useMutation({
    mutationFn: (board: string) => post<{ run_id: string }>(`/scan/${board}`),
    onSuccess: (_, board) => {
      toast.success(`Scanning ${board}`)
      queries.invalidateQueries({ queryKey: ["schedule"] })
    },
    onError: (failure: ApiError) => toast.error(failure.message),
  })

  if (error) return <Failed error={error} />
  if (isPending || !data) return <Loading rows={9} />

  return (
    <>
      <PageTitle
        hint={
          <>
            <b>Enabled</b> is <code>boards.&lt;name&gt;.enabled</code> in{" "}
            <code>config.yml</code>: whether the board runs at all, for the timer, the terminal
            and “scan all” alike. <b>Scheduled</b> is this timetable: when the timer should start
            it. A board that is scheduled but not enabled never runs. The timer asks every ten
            minutes whether anything is due, and a scan you start yourself counts.
          </>
        }
      >
        Schedule
      </PageTitle>

      {!data.timer.loaded ? (
        <Alert variant="destructive" className="mb-4">
          <AlertTriangle />
          <AlertTitle>Nothing is reading this timetable</AlertTitle>
          <AlertDescription>
            <span>
              The launchd agent <code>{data.timer.label}</code> is not loaded, so no scan will
              start on its own. Install it once:
            </span>
            <code className="mt-1 block rounded bg-muted px-2 py-1 text-xs">
              {data.timer.install_command}
            </code>
          </AlertDescription>
        </Alert>
      ) : null}

      {Object.keys(problems).length > 0 ? (
        <Alert variant="destructive" className="mb-4">
          <AlertTriangle />
          <AlertTitle>Nothing was saved</AlertTitle>
          <AlertDescription>
            <ul className="list-disc pl-4">
              {Object.entries(problems).map(([board, messages]) => (
                <li key={board}>
                  {board}: {messages.join("; ")}
                </li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      ) : null}

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Button variant="outline" disabled={runDue.isPending} onClick={() => runDue.mutate()}>
          {runDue.isPending ? <Loader2 className="size-3.5 animate-spin" /> : null}
          Run what is due now
        </Button>
        <span className="text-xs text-muted-foreground">
          {data.due_now.length
            ? `the timer would run ${data.due_now.join(", ")} at its next tick`
            : "nothing is due; every scheduled board has been scanned since its last slot"}
        </span>
      </div>

      <div className="overflow-x-auto rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Board</TableHead>
              <TableHead>Enabled</TableHead>
              <TableHead>Scheduled</TableHead>
              <TableHead>Mode</TableHead>
              <TableHead>Times / interval</TableHead>
              <TableHead>Days</TableHead>
              <TableHead>Next</TableHead>
              <TableHead>Last scan</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={row.board} className={row.runnable ? "" : "opacity-55"}>
                <TableCell className="font-medium">{row.board}</TableCell>
                <TableCell>
                  <Checkbox
                    checked={row.runnable}
                    onCheckedChange={(on) => edit(row.board, { runnable: on === true })}
                    aria-label={`${row.board} enabled`}
                  />
                </TableCell>
                <TableCell>
                  <Checkbox
                    checked={row.enabled}
                    onCheckedChange={(on) => edit(row.board, { enabled: on === true })}
                    aria-label={`${row.board} scheduled`}
                  />
                </TableCell>
                <TableCell>
                  <Select
                    value={row.mode}
                    onValueChange={(mode) => edit(row.board, { mode: mode as "at" | "every" })}
                  >
                    <SelectTrigger size="sm" className="w-28">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="at">at times</SelectItem>
                      <SelectItem value="every">every</SelectItem>
                    </SelectContent>
                  </Select>
                </TableCell>
                <TableCell>
                  {row.mode === "at" ? (
                    <Input
                      className="h-8 w-40"
                      value={row.at}
                      placeholder="07:00, 18:00"
                      onChange={(event) => edit(row.board, { at: event.target.value })}
                    />
                  ) : (
                    <div className="flex items-center gap-1.5">
                      <Input
                        className="h-8 w-20"
                        type="number"
                        min={5}
                        step={5}
                        placeholder="min"
                        value={row.every_minutes}
                        onChange={(event) =>
                          edit(row.board, { every_minutes: event.target.value })
                        }
                      />
                      <span className="text-xs text-muted-foreground">between</span>
                      <Input
                        className="h-8 w-20"
                        placeholder="07:00"
                        value={row.window_from}
                        onChange={(event) => edit(row.board, { window_from: event.target.value })}
                      />
                      <Input
                        className="h-8 w-20"
                        placeholder="21:00"
                        value={row.window_to}
                        onChange={(event) => edit(row.board, { window_to: event.target.value })}
                      />
                    </div>
                  )}
                </TableCell>
                <TableCell>
                  <div className="flex gap-1">
                    {data.days.map((name, number) => {
                      const on = row.days.includes(number)
                      return (
                        <button
                          key={name}
                          type="button"
                          aria-pressed={on}
                          title={name}
                          onClick={() =>
                            edit(row.board, {
                              days: on
                                ? row.days.filter((d) => d !== number)
                                : [...row.days, number].sort((a, b) => a - b),
                            })
                          }
                          className={
                            on
                              ? "size-6 rounded border bg-primary text-[11px] text-primary-foreground"
                              : "size-6 rounded border text-[11px] text-muted-foreground hover:bg-accent"
                          }
                        >
                          {name[0]}
                        </button>
                      )
                    })}
                  </div>
                </TableCell>
                <TableCell
                  className={row.overdue ? "text-xs text-amber-600 dark:text-amber-400" : "text-xs"}
                >
                  {row.overdue ? "due now" : row.next_due || "—"}
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {row.last_run || "never"}
                </TableCell>
                <TableCell>
                  {data.running.includes(row.board) ? (
                    <span className="text-xs text-muted-foreground">scanning</span>
                  ) : row.runnable ? (
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={runNow.isPending}
                      onClick={() => runNow.mutate(row.board)}
                    >
                      <Play className="size-3.5" />
                      Run
                    </Button>
                  ) : (
                    <span className="text-xs text-muted-foreground" title="enable it first">
                      —
                    </span>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <div className="mt-4">
        <Button disabled={save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? <Loader2 className="size-3.5 animate-spin" /> : null}
          Save schedule
        </Button>
      </div>

      <SectionTitle>Current timetable</SectionTitle>
      <ul className="space-y-1 text-xs text-muted-foreground">
        {data.rows.map((row) => (
          <li key={row.board}>
            {row.board} — {row.summary}
          </li>
        ))}
      </ul>
    </>
  )
}
