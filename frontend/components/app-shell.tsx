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

export function AppShell({ children, user }: AppShellProps) {
  const pathname = usePathname();
  const [currentHash, setCurrentHash] = useState("");
  const displayName = user?.username ?? "Lernende:r";

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
      <aside className="fixed inset-y-0 left-0 z-40 hidden w-[280px] flex-col border-r border-border/70 bg-card/80 px-5 py-6 backdrop-blur-xl lg:flex">
        <Link href="/" className="flex items-center gap-3 rounded-2xl px-2 py-1.5" aria-label="Lernraum Startseite">
          <span className="flex h-11 w-11 items-center justify-center rounded-2xl bg-primary text-primary-foreground shadow-lg shadow-primary/20">
            <Sparkles className="h-5 w-5" aria-hidden="true" />
          </span>
          <span>
            <span className="block text-sm font-extrabold tracking-tight">Lernraum</span>
            <span className="block text-xs text-muted-foreground">Dein persönlicher Lerntag</span>
          </span>
        </Link>

        <div className="mt-12">
          <p className="mb-3 px-3 text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">Navigation</p>
          <nav className="space-y-1" aria-label="Hauptnavigation">
            {navigation.map((item) => {
              const Icon = item.icon;
              const active = isNavigationHrefActive(pathname, item.href, currentHash);
              return (
                <Link
                  key={item.label}
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "group flex items-center gap-3 rounded-xl px-3 py-3 text-sm font-semibold transition-colors",
                    active ? "bg-primary text-primary-foreground shadow-sm shadow-primary/20" : "text-muted-foreground hover:bg-muted hover:text-foreground",
                  )}
                >
                  <Icon className="h-[18px] w-[18px]" aria-hidden="true" />
                  {item.label}
                  {active ? <span className="ml-auto h-1.5 w-1.5 rounded-full bg-primary-foreground" /> : null}
                </Link>
              );
            })}
          </nav>
        </div>

        <div className="mt-10 rounded-2xl border border-primary/15 bg-primary/5 p-4">
          <div className="mb-3 flex items-center gap-2 text-primary">
            <ShieldCheck className="h-4 w-4" aria-hidden="true" />
            <span className="text-xs font-bold uppercase tracking-[0.16em]">Datenschutz</span>
          </div>
          <p className="text-sm font-semibold leading-5">Deine Lerninhalte bleiben in deinem Lernraum.</p>
          <p className="mt-2 text-xs leading-5 text-muted-foreground">Sie werden nicht als private Daten im Browser gespeichert.</p>
        </div>

        <div className="mt-auto flex items-center gap-3 border-t border-border/70 pt-5">
          <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-secondary text-xs font-extrabold text-secondary-foreground">
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
        <header className="sticky top-0 z-30 flex h-16 items-center justify-between border-b border-border/70 bg-background/85 px-4 backdrop-blur-xl sm:px-6 lg:hidden">
          <Link href="/" className="flex items-center gap-2.5" aria-label="Lernraum Startseite">
            <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary text-primary-foreground">
              <Sparkles className="h-4 w-4" aria-hidden="true" />
            </span>
            <span className="text-sm font-extrabold tracking-tight">Lernraum</span>
          </Link>
          <div className="flex items-center gap-1">
          <ThemeToggle />
          <Link href="/settings" className="flex h-9 w-9 items-center justify-center rounded-xl bg-secondary text-xs font-extrabold text-secondary-foreground" aria-label="Einstellungen">
            <Settings className="h-5 w-5" aria-hidden="true" />
          </Link>
          <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-secondary text-xs font-extrabold text-secondary-foreground">
            {getInitials(displayName)}
          </span>
          </div>
        </header>
        <main id="main-content" className="mx-auto min-h-screen w-full max-w-[1540px] px-4 pb-28 pt-6 sm:px-6 sm:pt-8 lg:px-10 lg:pb-10">
          {children}
        </main>
      </div>

      <nav className="fixed inset-x-0 bottom-0 z-40 border-t border-border/70 bg-card/90 px-2 pb-[max(0.5rem,env(safe-area-inset-bottom))] pt-2 backdrop-blur-xl lg:hidden" aria-label="Mobile Navigation">
        <div className="mx-auto grid max-w-lg grid-cols-6 gap-1">
          {navigation.map((item) => {
            const Icon = item.icon;
            const active = isNavigationHrefActive(pathname, item.href, currentHash);
            return (
              <Link
                key={item.label}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex min-h-14 flex-col items-center justify-center gap-1 rounded-xl px-1 text-[10px] font-bold transition-colors",
                  active ? "bg-primary/10 text-primary" : "text-muted-foreground hover:bg-muted hover:text-foreground",
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
