import { createContext, useContext, useEffect, useState } from "react"

type Theme = "light" | "dark" | "system"

const KEY = "job-newsroom-theme"
const ThemeContext = createContext<{ theme: Theme; setTheme: (t: Theme) => void }>({
  theme: "system",
  setTheme: () => {},
})

/**
 * Light, dark, or whatever the machine says.
 *
 * "system" is the default and stores nothing, so a laptop that switches at sunset takes the
 * dashboard with it. Choosing explicitly is what writes to localStorage.
 */
export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setTheme] = useState<Theme>(() => (localStorage.getItem(KEY) as Theme) || "system")

  useEffect(() => {
    const root = document.documentElement
    const media = window.matchMedia("(prefers-color-scheme: dark)")
    const paint = () => {
      const dark = theme === "dark" || (theme === "system" && media.matches)
      root.classList.toggle("dark", dark)
    }
    paint()
    media.addEventListener("change", paint)
    return () => media.removeEventListener("change", paint)
  }, [theme])

  return (
    <ThemeContext.Provider
      value={{
        theme,
        setTheme: (next) => {
          if (next === "system") localStorage.removeItem(KEY)
          else localStorage.setItem(KEY, next)
          setTheme(next)
        },
      }}
    >
      {children}
    </ThemeContext.Provider>
  )
}

export const useTheme = () => useContext(ThemeContext)
