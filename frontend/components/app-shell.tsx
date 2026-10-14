"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BookOpen,
  Bot,
  FileText,
  Home,
  ListChecks,
  MessageSquareText,
  Settings,
  TrendingUp,
  type LucideIcon,
} from "lucide-react";
import { ThemeToggle } from "@/components/theme-toggle";
import { getInitials } from "@/lib/format";
import { isNavigationHrefActive } from "@/lib/navigation";
import type { UserResponse } from "@/lib/types";
import { cn } from "@/lib/utils";
import { t } from "@/lib/i18n";

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
 * Die Navigation: die Uebersicht, dann die eigenen Seiten.
 *
 * Die frueheren Eintraege "Stundenplan", "Hausaufgaben", "Pruefungen" und
 * "Termine" waren nur Anker im Dashboard - vier Punkte, die alle auf dieselbe
 * Seite fuehrten. Sie sind entfallen; das Dashboard zeigt alles auf einen Blick.
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
    items: [{ label: t("Übersicht"), mobile: t("Start"), href: "/", icon: Home }],
  },
  {
    label: t("Bibliothek"),
    items: [
      { label: t("Lernplan"), mobile: t("Lernen"), href: "/lernen", icon: ListChecks },
      { label: t("Fächer"), mobile: t("Fächer"), href: "/faecher", icon: BookOpen },
      { label: t("Dokumente"), mobile: t("Dokumente"), href: "/dokumente", icon: FileText },
      { label: t("Fortschritt"), mobile: t("Fortschritt"), href: "/fortschritt", icon: TrendingUp },
    ],
  },
  {
    label: t("Werkzeuge"),
    items: [
      { label: t("Assistent"), mobile: t("Assistent"), href: "/assistent", icon: MessageSquareText },
      { label: t("KI & Modelle"), mobile: t("KI"), href: "/ki", icon: Bot },
    ],
  },
];

const settingsItem: NavItem = { label: t("Einstellungen"), mobile: t("Mehr"), href: "/settings", icon: Settings };
const mobileNavigation = [...navigationGroups.flatMap((group) => group.items), settingsItem];

/**
 * Aktiv ist ein Eintrag allein ueber den Pfad. Der Hash zaehlt nicht mehr:
 * Links wie `/#study-tasks` aus den Benachrichtigungen springen nur innerhalb
 * der Uebersicht, und die soll dabei markiert bleiben.
 */
function isActive(pathname: string, href: string) {
  return isNavigationHrefActive(pathname, href, "");
}

/**
 * Das Zeichen der App: der Kopf des Faultiers (Entwurf C1 hell / C2 dunkel).
 * Der Hintergrund folgt dem Theme, der Hairline-Ring haelt das
 * Quadrat auf dem Seitenhintergrund sichtbar.
 */
export function BrandMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 120 120"
      className={cn("h-6 w-6 shrink-0 rounded-[6px] ring-1 ring-foreground/15", className)}
      aria-hidden="true"
      focusable="false"
    >
      <rect width="120" height="120" className="fill-[#e9e9e9] dark:fill-[#0a0a0a]" />
      <g transform="translate(60 60) scale(1.5) translate(-60 -58)"><ellipse cx="60" cy="58" rx="31" ry="27" fill="#cdcdcd" stroke="#0a0a0a" strokeWidth="2.4"/><path d="M60 43C76 43 85 52 85 63C85 74 74 81 60 81C46 81 35 74 35 63C35 52 44 43 60 43Z" fill="#fff"/><ellipse cx="41" cy="71" rx="4" ry="2.5" fill="#ececec"/><ellipse cx="79" cy="71" rx="4" ry="2.5" fill="#ececec"/><path d="M56 56.5C52 51.5 42 54.5 37 63C35 67.5 39 70 43.5 68C50.5 65 58 61.5 56 56.5Z" fill="#0a0a0a"/><path d="M64 56.5C68 51.5 78 54.5 83 63C85 67.5 81 70 76.5 68C69.5 65 62 61.5 64 56.5Z" fill="#0a0a0a"/><circle cx="48.5" cy="60.5" r="3.4" fill="#fff"/><circle cx="71.5" cy="60.5" r="3.4" fill="#fff"/><circle cx="48.9" cy="60.9" r="2" fill="#0a0a0a"/><circle cx="71.9" cy="60.9" r="2" fill="#0a0a0a"/><ellipse cx="60" cy="66" rx="4.4" ry="2.9" fill="#0a0a0a"/><path d="M54.5 70.5q2.75 3 5.5 0q2.75 3 5.5 0" fill="none" stroke="#0a0a0a" strokeWidth="2" strokeLinecap="round"/></g>
    </svg>
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
  const displayName = user?.username ?? "Lernende:r";

  return (
    <div className="app-surface min-h-screen bg-background text-foreground">
      <a
        href="#main-content"
        className="sr-only z-50 focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:rounded-md focus:border focus:border-border focus:bg-card focus:px-3 focus:py-2 focus:text-[13px] focus:font-medium"
      >
        {t("Zum Inhalt springen")}
      </a>

      <aside className="fixed inset-y-0 left-0 z-40 hidden w-[232px] flex-col border-r border-border bg-sidebar lg:flex">
        {/*
          Die Kopfzeile der Seitenleiste ist in der Desktop-App die Fensterleiste:
          man zieht das Fenster daran.
        */}
        <div className="app-drag flex h-12 shrink-0 items-center px-4">
          <Link href="/" className="app-no-drag flex items-center gap-2 rounded-md px-1 py-1" aria-label={t("Slearn Startseite")}>
            <BrandMark />
            <span className="text-[13px] font-semibold tracking-tight">{t("Slearn")}</span>
          </Link>
        </div>

        <nav className="flex-1 space-y-4 overflow-y-auto px-2.5 pb-4 pt-2" aria-label={t("Hauptnavigation")}>
          {navigationGroups.map((group) => (
            <div key={group.label ?? "start"}>
              {group.label ? <p className="mb-1 px-2.5 text-[11px] font-medium text-muted-foreground/80">{group.label}</p> : null}
              <div className="space-y-px">
                {group.items.map((item) => (
                  <SidebarLink key={item.href} item={item} active={isActive(pathname, item.href)} />
                ))}
              </div>
            </div>
          ))}
        </nav>

        <div className="space-y-px border-t border-border px-2.5 py-2.5">
          <SidebarLink item={settingsItem} active={isActive(pathname, settingsItem.href)} />
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
          <Link href="/" className="flex items-center gap-2" aria-label={t("Slearn Startseite")}>
            <BrandMark />
            <span className="text-[13px] font-semibold tracking-tight">{t("Slearn")}</span>
          </Link>
          <div className="flex items-center gap-1">
            <ThemeToggle />
            <Link
              href="/settings"
              className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
              aria-label={t("Einstellungen")}
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
        Acht Eintraege passen nicht in ein Raster auf 375px. Deshalb waagerecht
        scrollbar mit fester Mindestbreite je Eintrag - man sieht, dass es
        weitergeht, statt dass ein Teil der Navigation fehlt.
      */}
      <nav
        className="fixed inset-x-0 bottom-0 z-40 border-t border-border bg-background px-2 pb-[max(0.375rem,env(safe-area-inset-bottom))] pt-1.5 lg:hidden"
        aria-label={t("Mobile Navigation")}
      >
        <div className="mx-auto flex max-w-lg snap-x gap-0.5 overflow-x-auto">
          {mobileNavigation.map((item) => {
            const Icon = item.icon;
            const active = isActive(pathname, item.href);
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
