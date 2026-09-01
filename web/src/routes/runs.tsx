import { keepPreviousData, useQuery } from "@tanstack/react-query"
import { Link, useSearchParams } from "react-router-dom"

import { Empty, Failed, Loading, PageTitle } from "@/components/page"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
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
import { get, type RunList } from "@/lib/api"
import { outcomeTone, plural } from "@/lib/format"

const ANY = "any"

export default function RunsPage() {
  const [params, setParams] = useSearchParams()
  const search = params.toString()
  const { data, error, isPending } = useQuery({
    queryKey: ["runs", search],
    queryFn: () => get<RunList>(`/runs?${search}`),
    placeholderData: keepPreviousData,
  })

  const go = (key: string, value: string) =>
    setParams((previous) => {
      const next = new URLSearchParams(previous)
      if (value) next.set(key, value)
      else next.delete(key)
      if (key !== "page") next.delete("page")
      return next
    })

  if (error) return <Failed error={error} />
  if (isPending || !data) return <Loading rows={10} />

  return (
    <>
      <PageTitle>Runs</PageTitle>

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Select value={data.board || ANY} onValueChange={(v) => go("board", v === ANY ? "" : v)}>
          <SelectTrigger className="w-40">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ANY}>all boards</SelectItem>
            {data.boards.map((name) => (
              <SelectItem key={name} value={name}>
                {name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span className="text-xs text-muted-foreground">
          {plural(data.total, "scan")} recorded · page {data.page} of {data.pages}
        </span>
      </div>

      <div className="max-w-5xl overflow-x-auto rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>When</TableHead>
              <TableHead>Board</TableHead>
              <TableHead>Outcome</TableHead>
              <TableHead>Trigger</TableHead>
              <TableHead className="text-right">Searches</TableHead>
              <TableHead className="text-right">Jobs found</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.runs.map((run) => (
              <TableRow key={`${run.board}-${run.stamp}`}>
                <TableCell className="tabular-nums">{run.display_time}</TableCell>
                <TableCell className="text-muted-foreground">{run.board}</TableCell>
                <TableCell className={outcomeTone(run.status)}>{run.status || "—"}</TableCell>
                <TableCell>
                  {run.trigger ? <Badge variant="secondary">{run.trigger}</Badge> : "—"}
                </TableCell>
                <TableCell className="text-right tabular-nums">{run.searches}</TableCell>
                <TableCell className="text-right tabular-nums">{run.jobs}</TableCell>
                <TableCell className="space-x-3 text-sm whitespace-nowrap">
                  {run.has_log ? (
                    <Link to={`/scan/${run.run_id}`} className="underline underline-offset-4">
                      log
                    </Link>
                  ) : null}
                  {run.status === "interrupted" || run.status === "failed" ? (
                    <span className="text-amber-600 dark:text-amber-400">no report</span>
                  ) : !run.healthy ? (
                    <span className="text-destructive">found nothing</span>
                  ) : (
                    <Link to={`/jobs?board=${run.board}`} className="underline underline-offset-4">
                      jobs
                    </Link>
                  )}
                </TableCell>
              </TableRow>
            ))}
            {data.runs.length === 0 ? (
              <TableRow>
                <TableCell colSpan={7}>
                  <Empty>No scans recorded.</Empty>
                </TableCell>
              </TableRow>
            ) : null}
          </TableBody>
        </Table>
      </div>

      {data.pages > 1 ? (
        <div className="mt-4 flex items-center gap-3">
          <Button
            variant="outline"
            size="sm"
            disabled={data.page <= 1}
            onClick={() => go("page", String(data.page - 1))}
          >
            ← newer
          </Button>
          <span className="text-xs text-muted-foreground">
            {data.page} / {data.pages}
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={data.page >= data.pages}
            onClick={() => go("page", String(data.page + 1))}
          >
            older →
          </Button>
        </div>
      ) : null}
    </>
  )
}
