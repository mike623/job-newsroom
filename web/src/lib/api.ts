// Every shape the dashboard's API answers with, and the two verbs used to reach it.
//
// The types are written from `dashboard/serialise.py`, which is the only place the Python side
// builds a response body — so a field gains a name in one file and is read in one file.

export interface Pipeline {
  present: boolean
  done: boolean
}

export interface Job {
  board: string
  job_id: string
  role_title: string
  company: string
  location: string
  salary: string
  salary_min: number | null
  salary_max: number | null
  salary_period: string
  pay: number | null
  url: string
  posted: string
  first_seen: string
  last_seen: string
  times_seen: number
  ingest_skip: string
  pipeline: Pipeline
  // Only on the detail endpoint.
  contract?: string
  search_title?: string
  search_location?: string
}

export interface BoardSummary {
  board: string
  scanned: boolean
  known: number
  new: number
  runs: number
  last_run: string
  last_run_jobs: number
  last_run_display: string
}

export interface ScanRun {
  id: string
  board: string
  status: string
  started: string
  ended: string
  exit_code: number | null
  trigger: string
  log: string
  jobs: number | null
  searches: number | null
  has_log: boolean
  display_started: string
}

export interface Run {
  board: string
  stamp: string
  jobs: number
  searches: number
  healthy: boolean
  status: string
  trigger: string
  run_id: string
  has_log: boolean
  display_time: string
}

export interface ScheduleRow {
  board: string
  enabled: boolean
  mode: "at" | "every"
  at: string
  every_minutes: string
  window_from: string
  window_to: string
  days: number[]
  runnable: boolean
  summary: string
  next_due: string
  last_run: string
  overdue: boolean
}

export interface Timer {
  loaded: boolean
  plist: string
  label: string
  install_command: string
}

export interface Overview {
  summaries: BoardSummary[]
  totals: { known: number; new: number; runs: number }
  history: ScanRun[]
  locks: Record<string, { pid: number }>
  scannable: string[]
  timetable: Record<string, ScheduleRow>
  timer: Timer
  scheduled: string[]
}

export interface JobList {
  jobs: Job[]
  boards: string[]
  sorts: string[]
  shown: number
  total: number
  has_pipeline: boolean
}

export interface JobDetail {
  job: Job
  sightings: string[]
  has_pipeline: boolean
}

export interface RunList {
  runs: Run[]
  boards: string[]
  board: string
  page: number
  pages: number
  total: number
  per_page: number
}

export interface Schedule {
  rows: ScheduleRow[]
  timer: Timer
  days: string[]
  running: string[]
  due_now: string[]
}

export interface Preview {
  board: string
  report: string
  stamp: string
  count: number
  error: string
}

export interface Candidate {
  board: string
  title: string
  company: string
  location: string
  posted: string
  url: string
  line: string
}

export interface Ingest {
  workspace: string
  previews: Preview[]
  rows: Candidate[]
  total: number
  boards: string[]
}

/** A refusal the server made on purpose, carrying the status so 409 reads as "busy". */
export class ApiError extends Error {
  status: number
  detail: unknown

  constructor(message: string, status: number, detail: unknown) {
    super(message)
    this.status = status
    this.detail = detail
  }
}

async function unwrap(response: Response) {
  const body = await response.text()
  const parsed = body ? JSON.parse(body) : {}
  if (!response.ok) {
    const detail = parsed?.detail
    throw new ApiError(
      typeof detail === "string" ? detail : response.statusText,
      response.status,
      detail,
    )
  }
  return parsed
}

export async function get<T>(path: string): Promise<T> {
  return unwrap(await fetch(`/api${path}`))
}

/**
 * A form-encoded POST, which is what the API reads.
 *
 * The bodies are small and flat — a report name, a whole timetable — and the server parses
 * them with the standard library rather than pulling in python-multipart.
 */
export async function post<T>(path: string, fields?: Record<string, string | string[]>): Promise<T> {
  const body = new URLSearchParams()
  for (const [key, value] of Object.entries(fields ?? {})) {
    if (Array.isArray(value)) value.forEach((v) => body.append(key, v))
    else body.append(key, value)
  }
  return unwrap(
    await fetch(`/api${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
    }),
  )
}
