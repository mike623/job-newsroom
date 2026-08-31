import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useSearchParams } from "react-router-dom"
import { ExternalLink, Loader2, Send } from "lucide-react"
import { toast } from "sonner"

import { Empty, Failed, Loading, PageTitle, SectionTitle } from "@/components/page"
import { Button } from "@/components/ui/button"
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
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { ApiError, get, post, type Ingest } from "@/lib/api"

const ANY = "any"

interface Sent {
  appended: string[]
  skipped: string[]
  added: number
}

export default function IngestPage() {
  const queries = useQueryClient()
  const [params, setParams] = useSearchParams()
  const [query, setQuery] = useState(params.get("q") ?? "")

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
    queryKey: ["ingest", search],
    queryFn: () => get<Ingest>(`/ingest?${search}`),
  })

  const announce = ({ appended, skipped, added }: Sent) => {
    if (appended.length) {
      toast.success(
        `Appended from ${appended.join(", ")}${added ? ` (${added} entries)` : ""}`,
      )
    }
    if (skipped.length) {
      toast.warning(
        `Not sent: ${skipped.join(", ")}. A board whose newest report changed since this page ` +
          "was drawn is skipped rather than appended unseen — reload and look again.",
      )
    }
    queries.invalidateQueries({ queryKey: ["ingest"] })
  }

  const sendOne = useMutation({
    mutationFn: ({ board, report, count }: { board: string; report: string; count: number }) =>
      post<Sent>(`/ingest/${board}`, { report, count: String(count) }),
    onSuccess: announce,
    onError: (failure: ApiError) => toast.error(failure.message),
  })

  const sendAll = useMutation({
    mutationFn: () => {
      const fields: Record<string, string> = { count: String(data?.total ?? 0) }
      for (const preview of pending) fields[`report.${preview.board}`] = preview.report
      return post<Sent>("/ingest-all", fields)
    },
    onSuccess: announce,
    onError: (failure: ApiError) => toast.error(failure.message),
  })

  if (error) return <Failed error={error} />
  if (isPending || !data) return <Loading rows={8} />

  if (!data.workspace) {
    return (
      <>
        <PageTitle>Ingest</PageTitle>
        <p className="text-sm text-muted-foreground">
          No downstream workspace is configured, so there is nothing to ingest into. Set{" "}
          <code>career_ops.workspace</code> in <code>config.yml</code>.
        </p>
      </>
    )
  }

  const pending = data.previews.filter((preview) => preview.count)
  const board = params.get("board") ?? ""

  return (
    <>
      <PageTitle
        hint={
          <>
            Appends to <code>{data.workspace}/data/pipeline.md</code>. A report is everything a
            board showed; what is listed here is what survives <b>portals.yml</b>’s title and
            location filters and is not already downstream. This never happens on a schedule —
            only when you press a button.
          </>
        }
      >
        Ingest
      </PageTitle>

      <SectionTitle>Send leads downstream</SectionTitle>
      <div className="overflow-x-auto rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Board</TableHead>
              <TableHead>Report</TableHead>
              <TableHead className="text-right">To add</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.previews.map((preview) => (
              <TableRow key={preview.board}>
                <TableCell className="font-medium">{preview.board}</TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {preview.report || "—"}
                </TableCell>
                <TableCell className="text-right tabular-nums">{preview.count || ""}</TableCell>
                <TableCell>
                  {preview.error ? (
                    <span className="text-xs text-muted-foreground">{preview.error}</span>
                  ) : !preview.count ? (
                    <span className="text-xs text-muted-foreground">nothing new</span>
                  ) : (
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={sendOne.isPending}
                      onClick={() =>
                        sendOne.mutate({
                          board: preview.board,
                          report: preview.report,
                          count: preview.count,
                        })
                      }
                    >
                      {sendOne.isPending && sendOne.variables?.board === preview.board ? (
                        <Loader2 className="size-3.5 animate-spin" />
                      ) : (
                        <Send className="size-3.5" />
                      )}
                      Send {preview.count}
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {pending.length > 1 ? (
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <Button disabled={sendAll.isPending} onClick={() => sendAll.mutate()}>
            {sendAll.isPending ? <Loader2 className="size-3.5 animate-spin" /> : null}
            Send all {data.total} to the pipeline
          </Button>
          <span className="text-xs text-muted-foreground">
            {pending.map((preview) => preview.board).join(", ")}
          </span>
        </div>
      ) : null}

      <SectionTitle>What would be added</SectionTitle>
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <Select
          value={board || ANY}
          onValueChange={(value) =>
            setParams((previous) => {
              const next = new URLSearchParams(previous)
              if (value === ANY) next.delete("board")
              else next.set("board", value)
              return next
            })
          }
        >
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
        <Input
          className="w-64"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="title, company or location"
        />
        <span className="text-xs text-muted-foreground">
          {data.rows.length} of {data.total} shown
          {board || query
            ? " · filtering changes what you are reading, not what a button sends"
            : ""}
        </span>
      </div>

      <div className="overflow-x-auto rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Board</TableHead>
              <TableHead>Title</TableHead>
              <TableHead>Company</TableHead>
              <TableHead>Location</TableHead>
              <TableHead>Posted</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.rows.map((row) => (
              <TableRow key={`${row.board}-${row.url}-${row.title}`}>
                <TableCell className="text-muted-foreground">{row.board}</TableCell>
                <TableCell>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <a
                        href={row.url}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1.5 font-medium hover:underline"
                      >
                        {row.title}
                        <ExternalLink className="size-3 text-muted-foreground" />
                      </a>
                    </TooltipTrigger>
                    {/* The exact line the pipeline will receive, so the page and the file
                        can never tell two different stories about the same job. */}
                    <TooltipContent className="max-w-lg break-all">{row.line}</TooltipContent>
                  </Tooltip>
                </TableCell>
                <TableCell>{row.company}</TableCell>
                <TableCell className="text-muted-foreground">{row.location || "—"}</TableCell>
                <TableCell className="text-muted-foreground">{row.posted || "—"}</TableCell>
              </TableRow>
            ))}
            {data.rows.length === 0 ? (
              <TableRow>
                <TableCell colSpan={5}>
                  <Empty>Nothing to add.</Empty>
                </TableCell>
              </TableRow>
            ) : null}
          </TableBody>
        </Table>
      </div>
    </>
  )
}
