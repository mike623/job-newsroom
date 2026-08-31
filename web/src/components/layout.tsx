import { Link, NavLink, Outlet, useLocation } from "react-router-dom"
import { Monitor, Moon, Sun } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Boundary } from "@/components/page"
import { useTheme } from "@/components/theme"
import { cn } from "@/lib/utils"

const PAGES = [
  { to: "/", label: "Overview", end: true },
  { to: "/jobs", label: "Jobs" },
  { to: "/runs", label: "Runs" },
  { to: "/schedule", label: "Schedule" },
  { to: "/ingest", label: "Ingest" },
]

function ThemeToggle() {
  const { theme, setTheme } = useTheme()
  const Icon = theme === "dark" ? Moon : theme === "light" ? Sun : Monitor
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" aria-label="Colour theme">
          <Icon className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        {(["light", "dark", "system"] as const).map((option) => (
          <DropdownMenuItem key={option} onSelect={() => setTheme(option)}>
            {option}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

export function Layout() {
  // Keyed on the path so a page that threw does not keep its error across a navigation.
  const { pathname } = useLocation()
  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="sticky top-0 z-20 border-b bg-background/85 backdrop-blur">
        <div className="mx-auto flex max-w-[88rem] items-center gap-6 px-6 py-3">
          <Link to="/" className="text-sm font-semibold tracking-tight">
            Job Newsroom
          </Link>
          <nav className="flex flex-1 items-center gap-1">
            {PAGES.map((page) => (
              <NavLink key={page.to} to={page.to} end={page.end}>
                {({ isActive }) => (
                  <span
                    className={cn(
                      "rounded-md px-2.5 py-1.5 text-sm transition-colors",
                      isActive
                        ? "bg-accent font-medium text-accent-foreground"
                        : "text-muted-foreground hover:text-foreground",
                    )}
                  >
                    {page.label}
                  </span>
                )}
              </NavLink>
            ))}
          </nav>
          <ThemeToggle />
        </div>
      </header>
      <main className="mx-auto max-w-[88rem] px-6 py-7">
        <Boundary key={pathname}>
          <Outlet />
        </Boundary>
      </main>
    </div>
  )
}
