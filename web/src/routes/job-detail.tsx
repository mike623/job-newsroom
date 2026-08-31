import { useQuery } from "@tanstack/react-query"
import { Link, useParams } from "react-router-dom"
import { ArrowLeft, ExternalLink } from "lucide-react"

import { Failed, Loading, SectionTitle, Stat } from "@/components/page"
import { Badge } from "@/components/ui/badge"
import { Table, TableBody, TableCell, TableRow } from "@/components/ui/table"
import { get, type JobDetail } from "@/lib/api"
import { day, pay, plural, stamp } from "@/lib/format"

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <TableRow>
      <TableCell className="w-44 align-top text-muted-foreground">{label}</TableCell>
      <TableCell>{children}</TableCell>
    </TableRow>
  )
}

export default function JobDetailPage() {
  const { board = "", jobId = "" } = useParams()
  const { data, error, isPending } = useQuery({
    queryKey: ["job", board, jobId],
    queryFn: () => get<JobDetail>(`/jobs/${board}/${encodeURIComponent(jobId)}`),
  })

  if (error) return <Failed error={error} />
  if (isPending || !data) return <Loading />

  const { job } = data

  return (
    <>
      <Link
        to="/jobs"
        className="mb-4 inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-3.5" /> all jobs
      </Link>

      <h1 className="text-xl font-semibold tracking-tight">{job.role_title}</h1>
      <p className="mt-1 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
        {job.company || "Unknown company"}
        {job.location ? <span>· {job.location}</span> : null}
        <Badge variant="secondary">{job.board}</Badge>
      </p>

      <div className="mt-5 flex flex-wrap gap-3">
        <Stat value={job.times_seen} label="sightings" />
        <Stat value={<span className="text-base">{day(job.first_seen)}</span>} label="first seen" />
        <Stat value={<span className="text-base">{day(job.last_seen)}</span>} label="last seen" />
      </div>

      <SectionTitle>Advert</SectionTitle>
      <div className="max-w-3xl overflow-hidden rounded-lg border">
        <Table>
          <TableBody>
            <Field label="Salary as printed">{job.salary || "—"}</Field>
            <Field label="Parsed">
              {job.pay ? pay(job) : <span className="text-muted-foreground">no figure stated</span>}
            </Field>
            <Field label="Contract">{job.contract || "—"}</Field>
            <Field label="Posted">{job.posted || "—"}</Field>
            <Field label="Found under">
              <span className="text-muted-foreground">
                {job.search_title || "—"} / {job.search_location || "—"}
              </span>
            </Field>
            {data.has_pipeline ? (
              <Field label="Downstream">
                {job.pipeline.done ? (
                  <Badge variant="secondary">actioned</Badge>
                ) : job.pipeline.present ? (
                  <Badge variant="outline">imported, not yet ticked off</Badge>
                ) : (
                  <span className="text-muted-foreground">not in the pipeline</span>
                )}
              </Field>
            ) : null}
            <Field label="Job id">
              <code className="rounded bg-muted px-1.5 py-0.5 text-xs">{job.job_id}</code>
            </Field>
            <Field label="Source">
              {job.url ? (
                <a
                  href={job.url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1.5 underline underline-offset-4"
                >
                  view on {job.board} <ExternalLink className="size-3.5" />
                </a>
              ) : (
                <span className="text-muted-foreground">—</span>
              )}
            </Field>
          </TableBody>
        </Table>
      </div>

      <SectionTitle>Seen in {plural(data.sightings.length, "scan")}</SectionTitle>
      <div className="max-w-md overflow-hidden rounded-lg border">
        <Table>
          <TableBody>
            {data.sightings.map((seen, index) => (
              <TableRow key={seen}>
                <TableCell className="tabular-nums">{stamp(seen)}</TableCell>
                <TableCell className="text-right text-xs text-muted-foreground">
                  {index === 0 ? "most recent sighting" : ""}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </>
  )
}
