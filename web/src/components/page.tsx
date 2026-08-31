import { Component, type ReactNode } from "react"
import { AlertCircle } from "lucide-react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Card, CardContent } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { ApiError } from "@/lib/api"

export function PageTitle({ children, hint }: { children: React.ReactNode; hint?: React.ReactNode }) {
  return (
    <div className="mb-5">
      <h1 className="text-xl font-semibold tracking-tight">{children}</h1>
      {hint ? <p className="mt-1 max-w-3xl text-sm text-muted-foreground">{hint}</p> : null}
    </div>
  )
}

export function SectionTitle({ children }: { children: React.ReactNode }) {
  return <h2 className="mt-8 mb-3 text-sm font-semibold tracking-tight first:mt-0">{children}</h2>
}

export function Stat({ value, label }: { value: React.ReactNode; label: string }) {
  return (
    <Card className="min-w-36 py-0">
      <CardContent className="px-4 py-3.5">
        <div className="text-2xl leading-tight font-semibold tabular-nums">{value}</div>
        <div className="text-xs text-muted-foreground">{label}</div>
      </CardContent>
    </Card>
  )
}

/** Whatever went wrong, said plainly. A dashboard that renders empty hides its own failures. */
export function Failed({ error }: { error: unknown }) {
  const message =
    error instanceof ApiError
      ? `${error.message} (${error.status})`
      : error instanceof Error
        ? error.message
        : String(error)
  return (
    <Alert variant="destructive">
      <AlertCircle />
      <AlertTitle>That did not load</AlertTitle>
      <AlertDescription>{message}</AlertDescription>
    </Alert>
  )
}

export function Loading({ rows = 6 }: { rows?: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-9 w-full" />
      ))}
    </div>
  )
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <p className="py-8 text-center text-sm text-muted-foreground">{children}</p>
}


/**
 * A page that throws says so rather than blanking.
 *
 * Without this a render error unmounts the whole application, which looks exactly like a
 * server that returned nothing — and that is a bug you go looking for in the wrong half of
 * the project.
 */
export class Boundary extends Component<{ children: ReactNode }, { error: unknown }> {
  state = { error: null as unknown }

  static getDerivedStateFromError(error: unknown) {
    return { error }
  }

  render() {
    return this.state.error ? <Failed error={this.state.error} /> : this.props.children
  }
}
