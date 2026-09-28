"use client";

import { useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  CalendarDays,
  LayoutDashboard,
  ListChecks,
  ShieldCheck,
  Sparkles,
  Settings,
} from "lucide-react";
import { ThemeToggle } from "@/components/theme-toggle";
import { getInitials } from "@/lib/format";
import { isNavigationHrefActive } from "@/lib/navigation";
import { useActiveSection } from "@/lib/use-active-section";
import type { UserResponse } from "@/lib/types";
import { cn } from "@/lib/utils";

interface AppShellProps {
  children: ReactNode;
  user?: UserResponse | null;
}

const navigation = [
  { label: "Übersicht", href: "/#overview", icon: LayoutDashboard },
  { label: "Stundenplan", href: "/#schedule", icon: CalendarDays },
  { label: "Lernaufgaben", href: "/#study-tasks", icon: ListChecks },
  { label: "Kalender", href: "/#calendar", icon: CalendarDays },
  { label: "Nutzung", href: "/#usage", icon: ShieldCheck },
  { label: "Einstellungen", href: "/settings", icon: Settings },
];

/** Die Anker, die das Dashboard verlinkt, in Dokumentreihenfolge. */
const sectionIds = ["overview", "focus", "schedule", "exams", "homework", "study-tasks", "usage", "calendar"] as const;

export function AppShell({ children, user }: AppShellProps) {
  const pathname = usePathname();
  const [currentHash, setCurrentHash] = useState("");
  const scrolledSection = useActiveSection(sectionIds, pathname === "/");
  const displayName = user?.username ?? "Lernende:r";
  // Der Hash aus der URL gilt sofort, der Scrollzustand uebernimmt danach.
  const activeHash = scrolledSection ? `#${scrolledSection}` : currentHash;

  useEffect(() => {
    const updateHash = () => setCurrentHash(window.location.hash);
    const frame = window.requestAnimationFrame(updateHash);
    window.addEventListener("hashchange", updateHash);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("hashchange", updateHash);
    };
  }, [pathname]);

  return (
    <div className="app-surface min-h-screen bg-background text-foreground">
      <a
        href="#main-content"
        className="sr-only z-50 focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:border focus:border-border focus:bg-card focus:px-4 focus:py-2.5 focus:text-sm focus:font-bold"
      >
        Zum Inhalt springen
      </a>

      <aside className="fixed inset-y-0 left-0 z-40 hidden w-[280px] flex-col border-r border-border bg-card px-5 py-6 lg:flex">
        <Link href="/" className="flex items-center gap-3 px-2 py-1.5" aria-label="Lernraum Startseite">
          <span className="flex h-11 w-11 items-center justify-center bg-primary text-primary-foreground">
            <Sparkles className="h-5 w-5" aria-hidden="true" />
          </span>
          <span>
            <span className="block text-sm font-extrabold tracking-tight">Lernraum</span>
            <span className="block text-xs text-muted-foreground">Dein persönlicher Lerntag</span>
          </span>
        </Link>

        <div className="mt-12">
          <p className="mb-3 px-3 text-[11px] font-bold uppercase tracking-[0.2em] text-muted-foreground">Navigation</p>
          <nav className="space-y-1" aria-label="Hauptnavigation">
            {navigation.map((item) => {
              const Icon = item.icon;
              const active = isNavigationHrefActive(pathname, item.href, activeHash);
              return (
                <Link
                  key={item.label}
                  href={item.href}
                  aria-current={active ? "location" : undefined}
                  className={cn(
                    "group flex items-center gap-3 px-3 py-3 text-sm font-semibold transition-colors active:opacity-80",
                    active ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted hover:text-foreground",
                  )}
                >
                  <Icon className="h-[18px] w-[18px]" aria-hidden="true" />
                  {item.label}
                  {active ? <span className="ml-auto h-1.5 w-1.5 bg-primary-foreground" /> : null}
                </Link>
              );
            })}
          </nav>
        </div>

        <div className="mt-10 border border-border bg-muted p-4">
          <div className="mb-3 flex items-center gap-2">
            <ShieldCheck className="h-4 w-4" aria-hidden="true" />
            <span className="text-xs font-bold uppercase tracking-[0.16em]">Datenschutz</span>
          </div>
          <p className="text-sm font-semibold leading-5">Deine Lerninhalte bleiben in deinem Lernraum.</p>
          <p className="mt-2 text-xs leading-5 text-muted-foreground">Sie werden nicht als private Daten im Browser gespeichert.</p>
        </div>

        <div className="mt-auto flex items-center gap-3 border-t border-border pt-5">
          <span className="flex h-10 w-10 items-center justify-center bg-secondary text-xs font-extrabold text-secondary-foreground">
            {getInitials(displayName)}
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm font-bold">{displayName}</span>
            <span className="block truncate text-xs text-muted-foreground">Lernkonto</span>
          </span>
          <ThemeToggle />
        </div>
      </aside>

      <div className="lg:pl-[280px]">
        {/* `env(safe-area-inset-top)`: mit viewportFit "cover" liegt der Header
            sonst unter der Notch. */}
        <header className="sticky top-0 z-30 flex h-[calc(4rem+env(safe-area-inset-top))] items-center justify-between border-b border-border bg-background px-4 pt-[env(safe-area-inset-top)] sm:px-6 md:px-8 lg:hidden">
          <Link href="/" className="flex items-center gap-2.5" aria-label="Lernraum Startseite">
            <span className="flex h-9 w-9 items-center justify-center bg-primary text-primary-foreground">
              <Sparkles className="h-4 w-4" aria-hidden="true" />
            </span>
            <span className="text-sm font-extrabold tracking-tight">Lernraum</span>
          </Link>
          <div className="flex items-center gap-1">
            <ThemeToggle />
            <Link href="/settings" className="flex h-9 w-9 items-center justify-center bg-secondary text-xs font-extrabold text-secondary-foreground" aria-label="Einstellungen">
              <Settings className="h-5 w-5" aria-hidden="true" />
            </Link>
            <span className="flex h-9 w-9 items-center justify-center bg-secondary text-xs font-extrabold text-secondary-foreground">
              {getInitials(displayName)}
            </span>
          </div>
        </header>
        <main
          id="main-content"
          tabIndex={-1}
          className="mx-auto w-full max-w-[1540px] scroll-mt-24 px-4 pb-28 pt-6 outline-none sm:px-6 sm:pt-8 md:px-8 lg:px-10 lg:pb-10"
        >
          {children}
        </main>
      </div>

      <nav className="fixed inset-x-0 bottom-0 z-40 border-t border-border bg-card px-2 pb-[max(0.5rem,env(safe-area-inset-bottom))] pt-2 lg:hidden" aria-label="Mobile Navigation">
        <div className="mx-auto grid max-w-lg grid-cols-6 gap-1">
          {navigation.map((item) => {
            const Icon = item.icon;
            const active = isNavigationHrefActive(pathname, item.href, activeHash);
            return (
              <Link
                key={item.label}
                href={item.href}
                aria-current={active ? "location" : undefined}
                className={cn(
                  "flex min-h-14 flex-col items-center justify-center gap-1 px-1 text-[11px] font-bold transition-colors active:opacity-80",
                  active ? "bg-muted text-foreground" : "text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
              >
                <Icon className="h-[18px] w-[18px]" aria-hidden="true" />
                <span>{item.label === "Stundenplan" ? "Plan" : item.label === "Lernaufgaben" ? "Lernen" : item.label}</span>
              </Link>
            );
          })}
        </div>
      </nav>
    </div>
  );
}
