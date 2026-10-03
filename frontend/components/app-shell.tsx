"use client";

import { useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BookOpen,
  Bot,
  CalendarClock,
  CalendarDays,
  FileText,
  Home,
  Layers,
  ListChecks,
  MessageSquareText,
  Settings,
  TrendingUp,
  type LucideIcon,
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

interface NavItem {
  label: string;
  /** Kuerzere Beschriftung fuer die untere Leiste auf schmalen Bildschirmen. */
  mobile: string;
  href: string;
  icon: LucideIcon;
}

/**
 * Die Navigation in zwei Gruppen.
 *
 * "Heute" sind die Anker des Dashboards - das, was am Lerntag ansteht.
 * "Bibliothek" sind die eigenen Seiten, auf denen angelegt und verwaltet wird.
 * Die Einstellungen stehen getrennt unten, weil sie keine Tagesfrage
 * beantworten. "KI & Modelle" hat eine eigene Seite, weil KI-Dienst, lokale
 * Modelle und Stimmen zusammen eine Entscheidung sind und in den Einstellungen
 * die halbe Seite fuellten. Der fruehere Punkt "Nutzung" ist in die Kennzahlenleiste oben
 * im Dashboard aufgegangen - er zeigte dieselben drei Zahlen ein zweites Mal.
 */
const navigationGroups: { label: string | null; items: NavItem[] }[] = [
  {
    label: null,
    items: [{ label: "Übersicht", mobile: "Start", href: "/#overview", icon: Home }],
  },
  {
    label: "Heute",
    items: [
      { label: "Stundenplan", mobile: "Plan", href: "/#schedule", icon: CalendarDays },
      { label: "Hausaufgaben", mobile: "Aufgaben", href: "/#homework", icon: BookOpen },
      { label: "Prüfungen", mobile: "Prüfungen", href: "/#exams", icon: Layers },
      { label: "Termine", mobile: "Termine", href: "/#calendar", icon: CalendarClock },
    ],
  },
  {
    label: "Bibliothek",
    items: [
      { label: "Lernplan", mobile: "Lernen", href: "/lernen", icon: ListChecks },
      { label: "Fächer", mobile: "Fächer", href: "/faecher", icon: BookOpen },
      { label: "Dokumente", mobile: "Dokumente", href: "/dokumente", icon: FileText },
      { label: "Fortschritt", mobile: "Fortschritt", href: "/fortschritt", icon: TrendingUp },
    ],
  },
  {
    label: "Werkzeuge",
    items: [
      { label: "Assistent", mobile: "Assistent", href: "/assistent", icon: MessageSquareText },
      { label: "KI & Modelle", mobile: "KI", href: "/ki", icon: Bot },
    ],
  },
];

const settingsItem: NavItem = { label: "Einstellungen", mobile: "Mehr", href: "/settings", icon: Settings };
const mobileNavigation = [...navigationGroups.flatMap((group) => group.items), settingsItem];

/** Die Anker, die das Dashboard verlinkt, in Dokumentreihenfolge. */
const sectionIds = ["overview", "focus", "study-tasks", "schedule", "homework", "exams", "calendar"] as const;

/** Das Zeichen der App: ein schlichtes Monogramm statt eines Symbols. */
export function BrandMark({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "flex h-6 w-6 shrink-0 items-center justify-center rounded-[5px] bg-foreground text-[13px] font-semibold leading-none text-background",
        className,
      )}
      aria-hidden="true"
    >
      L
    </span>
  );
}

function SidebarLink({ item, active }: { item: NavItem; active: boolean }) {
  const Icon = item.icon;
  return (
    <Link
      href={item.href}
      aria-current={active ? "location" : undefined}
      className={cn(
        "flex h-8 items-center gap-2.5 rounded-md px-2.5 text-[13px] transition-colors",
        active ? "bg-accent font-medium text-foreground" : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
      )}
    >
      <Icon className="h-4 w-4 shrink-0" strokeWidth={active ? 2 : 1.75} aria-hidden="true" />
      {item.label}
    </Link>
  );
}

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
        className="sr-only z-50 focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:rounded-md focus:border focus:border-border focus:bg-card focus:px-3 focus:py-2 focus:text-[13px] focus:font-medium"
      >
        Zum Inhalt springen
      </a>

      <aside className="fixed inset-y-0 left-0 z-40 hidden w-[232px] flex-col border-r border-border bg-sidebar lg:flex">
        {/*
          Die Kopfzeile der Seitenleiste ist in der Desktop-App die Fensterleiste:
          man zieht das Fenster daran. Auf dem Mac liegen die Ampelknoepfe hier,
          deshalb rueckt die Marke dort nach unten.
        */}
        <div className="app-drag flex h-12 shrink-0 items-center px-4 mac:h-[52px] mac:items-end mac:pb-2">
          <Link href="/" className="app-no-drag flex items-center gap-2 rounded-md px-1 py-1" aria-label="Lernraum Startseite">
            <BrandMark />
            <span className="text-[13px] font-semibold tracking-tight">Lernraum</span>
          </Link>
        </div>

        <nav className="flex-1 space-y-4 overflow-y-auto px-2.5 pb-4 pt-2" aria-label="Hauptnavigation">
          {navigationGroups.map((group) => (
            <div key={group.label ?? "start"}>
              {group.label ? <p className="mb-1 px-2.5 text-[11px] font-medium text-muted-foreground/80">{group.label}</p> : null}
              <div className="space-y-px">
                {group.items.map((item) => (
                  <SidebarLink key={item.href} item={item} active={isNavigationHrefActive(pathname, item.href, activeHash)} />
                ))}
              </div>
            </div>
          ))}
        </nav>

        <div className="space-y-px border-t border-border px-2.5 py-2.5">
          <SidebarLink item={settingsItem} active={isNavigationHrefActive(pathname, settingsItem.href, activeHash)} />
          <div className="flex items-center gap-2.5 px-2.5 pt-1.5">
            <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-secondary text-[10px] font-semibold text-secondary-foreground">
              {getInitials(displayName)}
            </span>
            <span className="min-w-0 flex-1 truncate text-[13px] font-medium">{displayName}</span>
            <ThemeToggle />
          </div>
        </div>
      </aside>

      <div className="lg:pl-[232px]">
        {/* `env(safe-area-inset-top)`: mit viewportFit "cover" liegt der Header
            sonst unter der Notch. */}
        <header className="sticky top-0 z-30 flex h-[calc(3rem+env(safe-area-inset-top))] items-center justify-between border-b border-border bg-background px-4 pt-[env(safe-area-inset-top)] sm:px-6 lg:hidden">
          <Link href="/" className="flex items-center gap-2" aria-label="Lernraum Startseite">
            <BrandMark />
            <span className="text-[13px] font-semibold tracking-tight">Lernraum</span>
          </Link>
          <div className="flex items-center gap-1">
            <ThemeToggle />
            <Link
              href="/settings"
              className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
              aria-label="Einstellungen"
            >
              <Settings className="h-4 w-4" aria-hidden="true" />
            </Link>
          </div>
        </header>
        {/* In der Desktop-App ein schmaler Streifen oben zum Ziehen des Fensters -
            die Seitenleiste allein waere auf breiten Fenstern eine kleine Griffflaeche. */}
        <div className="app-drag sticky top-0 z-20 hidden h-3 desktop:lg:block" aria-hidden="true" />
        <main
          id="main-content"
          tabIndex={-1}
          className="mx-auto w-full max-w-[1440px] scroll-mt-16 px-4 pb-28 pt-5 outline-none sm:px-6 lg:px-8 lg:pb-10 lg:pt-6 desktop:lg:pt-4"
        >
          {children}
        </main>
      </div>

      {/*
        Elf Eintraege passen nicht in ein Raster auf 375px. Deshalb waagerecht
        scrollbar mit fester Mindestbreite je Eintrag - man sieht, dass es
        weitergeht, statt dass ein Teil der Navigation fehlt.
      */}
      <nav
        className="fixed inset-x-0 bottom-0 z-40 border-t border-border bg-background px-2 pb-[max(0.375rem,env(safe-area-inset-bottom))] pt-1.5 lg:hidden"
        aria-label="Mobile Navigation"
      >
        <div className="mx-auto flex max-w-lg snap-x gap-0.5 overflow-x-auto">
          {mobileNavigation.map((item) => {
            const Icon = item.icon;
            const active = isNavigationHrefActive(pathname, item.href, activeHash);
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "location" : undefined}
                className={cn(
                  "flex min-h-12 min-w-[4.25rem] shrink-0 snap-start flex-col items-center justify-center gap-0.5 rounded-md px-2 text-[10px] transition-colors",
                  active ? "font-semibold text-foreground" : "text-muted-foreground hover:text-foreground",
                )}
              >
                <Icon className="h-[18px] w-[18px]" strokeWidth={active ? 2 : 1.75} aria-hidden="true" />
                <span>{item.mobile}</span>
              </Link>
            );
          })}
        </div>
      </nav>
    </div>
  );
}
