import type { Job } from "./api"

const POUNDS = new Intl.NumberFormat("en-GB")

/** What a job pays, as the advert stated it — or an em dash when it stated nothing. */
export function pay(job: Job): string {
  const { salary_min: min, salary_max: max, salary_period: period } = job
  let figure: string
  if (min && max && min !== max) figure = `£${POUNDS.format(min)}–${POUNDS.format(max)}`
  else if (job.pay) figure = `${min ? "" : "up to "}${max ? "" : "from "}£${POUNDS.format(job.pay)}`
  else return "—"
  return period && period !== "year" ? `${figure}/${period}` : figure
}

/** A run stamp — `2026-08-31_181500` — as a date a person reads. */
export function stamp(value: string): string {
  const digits = value.replace(/\D/g, "")
  if (digits.length < 12) return value
  return `${digits.slice(0, 4)}-${digits.slice(4, 6)}-${digits.slice(6, 8)} ${digits.slice(8, 10)}:${digits.slice(10, 12)}`
}

export function day(value: string): string {
  return value.slice(0, 10)
}

export function plural(count: number, one: string, many = `${one}s`): string {
  return `${count} ${count === 1 ? one : many}`
}

/** The colour a scan outcome should read in: green done, amber survivable, red not. */
export function outcomeTone(status: string): string {
  if (status === "done") return "text-emerald-600 dark:text-emerald-400"
  if (status === "busy" || status === "interrupted") return "text-amber-600 dark:text-amber-400"
  if (status === "failed") return "text-red-600 dark:text-red-400"
  return "text-muted-foreground"
}
