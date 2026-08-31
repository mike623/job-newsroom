import { useEffect, useState } from "react"
import { keepPreviousData, useQuery } from "@tanstack/react-query"
import { Link, useSearchParams } from "react-router-dom"
import { Download } from "lucide-react"

import { Empty, Failed, Loading, PageTitle } from "@/components/page"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
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
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { get, type JobList } from "@/lib/api"
import { day, pay, plural } from "@/lib/format"
import { cn } from "@/lib/utils"

const ANY = "any"

export default function JobsPage() {
  const [params, setParams] = useSearchParams()
  const [query, setQuery] = useState(params.get("q") ?? "")

  // Typing filters as you go, but the request is the server's — it holds every job and the
  // filtering rules the CSV export shares. A short debounce keeps that one request per pause.
  useEffect(() => {
    const timer = setTimeout(() => {
      setParams(
        (previous) => {
          const next = new URLSearchParams(previous)
          if (query) next.set("q", query)
          else next.delete("q")
          return next
        },
        { replace: true },
      )
    }, 250)
    return () => clearTimeout(timer)
  }, [query, setParams])

  const search = params.toString()
  const { data, error, isPending } = useQuery({
    queryKey: ["jobs", search],
    queryFn: () => get<JobList>(`/jobs?${search}`),
    placeholderData: keepPreviousData,
  })

  const set = (key: string, value: string) =>
    setParams((previous) => {
      const next = new URLSearchParams(previous)
      if (value) next.set(key, value)
      else next.delete(key)
      return next
    })

  if (error) return <Failed error={error} />

  const board = params.get("board") ?? ""
  const actioned = params.get("actioned") ?? ""
  const sort = params.get("sort") ?? "first_seen"

  return (
    <>
      <PageTitle>Jobs</PageTitle>

      <Card className="mb-4 py-0">
        <CardContent className="flex flex-wrap items-end gap-3 px-4 py-3.5">
          <div className="min-w-56 flex-1">
            <Label htmlFor="q" className="mb-1.5 text-xs text-muted-foreground">
              Search
            </Label>
            <Input
              id="q"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="role, company or location"
            />
          </div>

          <div>
            <Label className="mb-1.5 text-xs text-muted-foreground">Board</Label>
            <Select value={board || ANY} onValueChange={(v) => set("board", v === ANY ? "" : v)}>
              <SelectTrigger className="w-36">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ANY}>all boards</SelectItem>
                {(data?.boards ?? []).map((name) => (
                  <SelectItem key={name} value={name}>
                    {name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div>
            <Label htmlFor="min_pay" className="mb-1.5 text-xs text-muted-foreground">
              Min £
            </Label>
            <Input
              id="min_pay"
              type="number"
              step={5000}
              className="w-28"
              value={params.get("min_pay") ?? ""}
              onChange={(event) => set("min_pay", event.target.value)}
            />
          </div>

          {data?.has_pipeline ? (
            <div>
              <Label className="mb-1.5 text-xs text-muted-foreground">Downstream</Label>
              <Select
                value={actioned || ANY}
                onValueChange={(v) => set("actioned", v === ANY ? "" : v)}
              >
                <SelectTrigger className="w-44">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ANY}>actioned or not</SelectItem>
                  <SelectItem value="no">not yet actioned</SelectItem>
                  <SelectItem value="yes">already actioned</SelectItem>
                </SelectContent>
              </Select>
            </div>
          ) : null}

          <div>
            <Label className="mb-1.5 text-xs text-muted-foreground">Sort</Label>
            <Select value={sort} onValueChange={(v) => set("sort", v)}>
              <SelectTrigger className="w-36">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(data?.sorts ?? []).map((name) => (
                  <SelectItem key={name} value={name}>
                    {name.replace(/_/g, " ")}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <Button
            variant="ghost"
            onClick={() => {
              setQuery("")
              setParams(new URLSearchParams())
            }}
          >
            Reset
          </Button>
        </CardContent>
      </Card>

      <div className="mb-2.5 flex items-center justify-between text-xs text-muted-foreground">
        <span>
          {data ? plural(data.shown, "job") : "…"}
          {data && data.shown !== data.total ? ` of ${data.total}` : ""}
        </span>
        <Button asChild variant="ghost" size="sm">
          <a href={`/export.csv?${search}`}>
            <Download className="size-3.5" />
            CSV
          </a>
        </Button>
      </div>

      {isPending || !data ? (
        <Loading rows={10} />
      ) : (
        <div className="overflow-x-auto rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Role</TableHead>
                <TableHead>Company</TableHead>
                <TableHead>Location</TableHead>
                <TableHead className="text-right">Pay</TableHead>
                <TableHead>Board</TableHead>
                <TableHead>First seen</TableHead>
                <TableHead className="text-right">Seen</TableHead>
                {data.has_pipeline ? <TableHead>Pipeline</TableHead> : null}
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.jobs.map((job) => (
                <TableRow
                  key={`${job.board}/${job.job_id}`}
                  /* A job portals.yml would drop on ingest: shown, but visibly not going anywhere. */
                  className={cn(job.ingest_skip && "bg-destructive/5")}
                >
                  {/* Titles run long and are the one column worth truncating: everything to
                      its right is short and fixed, and would otherwise be pushed off-screen. */}
                  <TableCell className="max-w-[24rem]">
                    <Link
                      to={`/jobs/${job.board}/${encodeURIComponent(job.job_id)}`}
                      title={job.role_title}
                      className={cn(
                        "block truncate font-medium hover:underline",
                        job.ingest_skip && "text-destructive",
                      )}
                    >
                      {job.role_title}
                    </Link>
                  </TableCell>
                  <TableCell className="max-w-44 truncate">{job.company || "—"}</TableCell>
                  <TableCell className="max-w-40 truncate text-muted-foreground">
                    {job.location || "—"}
                  </TableCell>
                  <TableCell className="text-right whitespace-nowrap tabular-nums">
                    {pay(job)}
                  </TableCell>
                  <TableCell className="text-muted-foreground">{job.board}</TableCell>
                  <TableCell className="whitespace-nowrap text-muted-foreground">
                    {day(job.first_seen)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums text-muted-foreground">
                    {job.times_seen}
                  </TableCell>
                  {data.has_pipeline ? (
                    <TableCell>
                      {job.pipeline.done ? (
                        <Badge variant="secondary">done</Badge>
                      ) : job.pipeline.present ? (
                        <Badge variant="outline">imported</Badge>
                      ) : job.ingest_skip ? (
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Badge variant="destructive" className="cursor-help font-normal">
                              skipped — {job.ingest_skip}
                            </Badge>
                          </TooltipTrigger>
                          <TooltipContent>
                            portals.yml would drop this job on ingest
                          </TooltipContent>
                        </Tooltip>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </TableCell>
                  ) : null}
                </TableRow>
              ))}
              {data.jobs.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={8}>
                    <Empty>Nothing matches these filters.</Empty>
                  </TableCell>
                </TableRow>
              ) : null}
            </TableBody>
          </Table>
        </div>
      )}
    </>
  )
}
