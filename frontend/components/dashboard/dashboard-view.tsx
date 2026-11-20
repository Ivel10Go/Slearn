"use client";

import { useMemo, useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ArrowRight,
  Check,
  CornerDownLeft,
  Play,
  RefreshCw,
  Square,
  Timer,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Faultier } from "@/components/assistant/faultier";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { CalendarSection } from "@/components/manage/calendar-section";
import { ExamsSection } from "@/components/manage/exams-section";
import { HomeworkSection } from "@/components/manage/homework-section";
import { ApiError, completeStudyTask, startStudyTask, stopStudyTask } from "@/lib/api";
import { describeSource } from "@/lib/ai-runtime";
import { Skeleton } from "@/components/ui/skeleton";
import {
  formatDuration,
} from "@/lib/format";
import { useNow } from "@/lib/use-now";
import { useGreeting } from "@/lib/use-greeting";
import { isDemoTaskId, type DashboardData, type DashboardStudyTask, type StudyActionResult, type StudyTaskStatus } from "@/lib/types";
import { cn } from "@/lib/utils";
import { DayBranch } from "./day-branch";
import { ScheduleCard } from "./schedule-view";
import { intlLocale, t } from "@/lib/i18n";

interface DashboardViewProps {
  data: DashboardData | null;
  error: string | null;
  isLoading: boolean;
  onRetry: () => void;
  username?: string | null;
}

const taskStatusLabels: Record<StudyTaskStatus, string> = {
  todo: t("Offen"),
  in_progress: t("In Arbeit"),
  completed: t("Erledigt"),
};

/**
 * Abstand, den ein Ankerziel oben frei haelt.
 *
 * Ohne das landet die Sprungmarke unter dem sticky Header, weil
 * `scroll-behavior: smooth` an der Fensteroberkante ausrichtet.
 */
const anchorClass = "scroll-mt-20";

const weekdayFormatter = new Intl.DateTimeFormat(intlLocale(), { weekday: "long", day: "numeric", month: "long" });

function ProgressBar({ value, className, indicatorClassName, label }: { value: number; className?: string; indicatorClassName?: string; label?: string }) {
  const percentage = Math.min(100, Math.max(0, value));
  return (
    <div
      className={cn("h-1 overflow-hidden rounded-full bg-muted", className)}
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={percentage}
    >
      <div className={cn("h-full rounded-full bg-foreground transition-[width] duration-slow", indicatorClassName)} style={{ width: `${percentage}%` }} />
    </div>
  );
}

function DashboardSkeleton() {
  return (
    <div className="space-y-4" aria-busy="true" aria-label={t("Dashboard wird geladen")}>
      <div className="space-y-2.5">
        <Skeleton className="h-3.5 w-36" />
        <Skeleton className="h-7 w-[28rem] max-w-full" />
      </div>
      <Skeleton className="h-[74px] w-full" />
      <Skeleton className="h-64 w-full" />
      <div className="grid gap-4 lg:grid-cols-2">
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    </div>
  );
}

function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="mx-auto flex max-w-md flex-col items-center py-20 text-center" role="alert">
      <h2 className="text-base font-semibold">{t("Slearn ist gerade nicht erreichbar")}</h2>
      <p className="mt-1.5 text-[15px] leading-5 text-muted-foreground">{message}</p>
      <Button type="button" variant="outline" className="mt-5" onClick={onRetry}>
        <RefreshCw aria-hidden="true" />
        {t("Erneut versuchen")}
      </Button>
    </div>
  );
}

/**
 * Die Kopfzeile des Dashboards: Datum, Tagessatz, Herkunft.
 *
 * Der Satz kommt vom Backend und ist einmal pro Tag von einem KI-Weg erzeugt.
 * Die Herkunft steht klein darunter - ein Server auf diesem Rechner, ein
 * Online-Dienst oder bei `fallback` der Satz aus den Daten -, damit niemand
 * einen Zufallssatz fuer eine KI-Aussage haelt. Beschriftet ueber
 * `describeSource`, damit Dashboard und Dokumentenfrage **eine** Formulierung
 * teilen.
 */
function GreetingHeader({ username, reference }: { username?: string | null; reference: Date }) {
  const { greeting, error, status, refresh, refreshing } = useGreeting();
  const herkunft = greeting ? describeSource(greeting.source, greeting.model) : null;

  return (
    <header id="overview" className={cn(anchorClass, "flex items-start justify-between gap-4")}>
      <div className="min-w-0">
        <p className="text-[15px] text-muted-foreground" suppressHydrationWarning>
          {weekdayFormatter.format(reference)}
        </p>
        {status === "loading" ? (
          <Skeleton className="mt-2 h-7 w-[28rem] max-w-full" />
        ) : status === "error" || !greeting || !herkunft ? (
          <>
            <h1 className="mt-1 text-[26px] font-semibold leading-9 tracking-tight">
              {username ? t("Willkommen, {name}.", { name: username }) : t("Willkommen.")}
            </h1>
            {error ? <p className="mt-1 text-[15px] text-muted-foreground">{error}</p> : null}
          </>
        ) : (
          <>
            <h1 className="mt-1 max-w-3xl text-[26px] font-semibold leading-9 tracking-tight">{greeting.text}</h1>
            <p className="mt-1.5 text-[13px] text-muted-foreground">{herkunft.text}</p>
          </>
        )}
      </div>

      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="mt-5 shrink-0"
        onClick={() => void refresh()}
        disabled={refreshing || status === "loading"}
        aria-label={t("Tagessatz neu formulieren")}
        title={t("Neu formulieren")}
      >
        <RefreshCw className={cn(refreshing && "animate-spin")} aria-hidden="true" />
      </Button>
    </header>
  );
}

function elapsedLabel(startedAt: string, now: Date | null) {
  if (!now) {
    return "0:00";
  }
  const seconds = Math.max(0, Math.floor((now.getTime() - new Date(startedAt).getTime()) / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function mergeStudyAction(task: DashboardStudyTask, result: StudyActionResult, action: "start" | "stop" | "complete") {
  const responseTask = result.task;
  const responseSession = result.session ?? responseTask?.session;
  const session: DashboardStudyTask["session"] = action !== "start" && !responseSession && task.session?.state === "active"
    ? { ...task.session, endedAt: task.session.endedAt ?? new Date().toISOString(), state: action === "complete" ? "completed" : "stopped" }
    : responseSession ?? task.session;
  const status = action === "complete" ? "completed" : responseTask?.status ?? (session?.state === "active" ? "in_progress" : task.status);
  return {
    ...task,
    ...(responseTask ?? {}),
    status,
    progress: action === "complete" ? 100 : responseTask?.progress ?? task.progress,
    session,
    startedAt: session?.startedAt ?? responseTask?.startedAt ?? task.startedAt,
  };
}

function StudyTasksCard({ tasks }: { tasks: DashboardStudyTask[] }) {
  const now = useNow();
  const [taskOverrides, setTaskOverrides] = useState<Record<string, DashboardStudyTask>>({});
  const [busyTaskId, setBusyTaskId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const taskList = tasks.map((task) => taskOverrides[task.id] ?? task);
  const openCount = taskList.filter((task) => task.status !== "completed").length;

  async function handleAction(task: DashboardStudyTask, action: "start" | "stop" | "complete") {
    setActionError(null);
    setBusyTaskId(task.id);
    try {
      const result = action === "start" ? await startStudyTask(task.id) : action === "stop" ? await stopStudyTask(task.id) : await completeStudyTask(task.id);
      setTaskOverrides((current) => ({ ...current, [task.id]: mergeStudyAction(task, result, action) }));
    } catch (error) {
      setActionError(error instanceof ApiError ? error.message : t("Die Lernaktion konnte nicht ausgeführt werden."));
    } finally {
      setBusyTaskId(null);
    }
  }

  return (
    <Card id="study-tasks" className={anchorClass}>
      <CardHeader className="flex-row items-center justify-between space-y-0 pb-3">
        <CardTitle>{t("Heute lernen")}</CardTitle>
        <div className="flex items-center gap-3">
          <span className="text-[13px] tabular-nums text-muted-foreground">{openCount} {t("offen")}</span>
          <Link href="/lernen" className="inline-flex items-center gap-1 text-[13px] text-muted-foreground hover:text-foreground">
            {t("Lernplan")}
            <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
          </Link>
        </div>
      </CardHeader>
      <CardContent className="px-0 pb-1.5">
        {actionError ? <p className="mx-4 mb-3 rounded-md border border-foreground/30 bg-muted px-3 py-2 text-[15px]" role="alert">{actionError}</p> : null}
        {taskList.length === 0 ? (
          <div className="px-4 pb-3">
            <EmptyState icon={Timer} title={t("Keine Lernaufgaben")} description={t("Geplante Lernschritte erscheinen hier.")} />
          </div>
        ) : (
          <ul className="divide-y divide-border border-t border-border">
            {taskList.slice(0, 5).map((task) => {
              const active = task.session?.state === "active";
              const completed = task.status === "completed";
              const demo = isDemoTaskId(task.id);
              const busy = busyTaskId === task.id;
              const startedAt = task.session?.startedAt ?? task.startedAt;
              return (
                <li key={task.id} className={cn("flex items-center gap-3 px-4 py-2.5", active && "bg-muted/60")}>
                  <button
                    type="button"
                    onClick={() => void handleAction(task, "complete")}
                    disabled={completed || busy || demo}
                    aria-label={completed ? t("{titel} erledigt", { titel: task.title }) : t("{titel} abschließen", { titel: task.title })}
                    className={cn(
                      "flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-full border transition-colors",
                      completed ? "border-foreground bg-foreground text-background" : "border-input hover:border-foreground",
                    )}
                  >
                    {completed ? <Check className="h-3 w-3" strokeWidth={3} aria-hidden="true" /> : null}
                  </button>
                  <div className="min-w-0 flex-1">
                    <p className={cn("truncate text-[15px] font-medium", completed && "text-muted-foreground line-through")}>{task.title}</p>
                    <p className="truncate text-[13px] text-muted-foreground">
                      {[task.subject, task.topic].filter(Boolean).join(" · ")}
                      {!completed && task.status === "in_progress" && !active ? ` · ${taskStatusLabels[task.status]}` : null}
                    </p>
                  </div>
                  {active && startedAt ? (
                    <span className="inline-flex items-center gap-1 text-[13px] font-medium tabular-nums">
                      <Timer className="h-3.5 w-3.5" aria-hidden="true" />
                      {elapsedLabel(startedAt, now)}
                    </span>
                  ) : (
                    <span className="hidden w-14 text-right text-[13px] tabular-nums text-muted-foreground sm:block">{formatDuration(task.durationMinutes)}</span>
                  )}
                  <div className="hidden w-16 sm:block">
                    <ProgressBar value={task.progress} label={`Fortschritt ${task.title}`} />
                  </div>
                  {completed ? (
                    <span className="w-[72px] text-right text-[13px] text-muted-foreground">{t("Erledigt")}</span>
                  ) : active ? (
                    <Button type="button" size="sm" variant="outline" className="w-[72px]" onClick={() => void handleAction(task, "stop")} disabled={busy || demo} aria-label={t("{titel} stoppen", { titel: task.title })}>
                      <Square className="!size-3 fill-current" aria-hidden="true" />
                      {t("Stopp")}
                    </Button>
                  ) : (
                    <Button type="button" size="sm" variant="outline" className="w-[72px]" onClick={() => void handleAction(task, "start")} disabled={busy || demo} aria-label={t("{titel} starten", { titel: task.title })}>
                      <Play className="!size-3 fill-current" aria-hidden="true" />
                      {demo ? t("Demo") : t("Start")}
                    </Button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

/**
 * Der Einstieg in den Assistenten. Frueher stand hier „Frag deine Unterlagen“
 * mit eigener Antwortflaeche; jetzt fuehrt die Frage auf `/assistent`, wo sie
 * sofort gestellt wird und das Gespraech weitergeht.
 */
const vorschlaege = [
  t("Trag eine Hausaufgabe ein"),
  t("Plane meine Lernzeit diese Woche"),
  t("Frag mich für die nächste Klausur ab"),
];

/*
 * Der Einstieg ueber den Assistenten steht oben, nicht als Karte in der
 * Seitenspalte: Eintragen, Planen und Abfragen laufen ueber ihn, statt ueber
 * je ein Formular pro Abschnitt.
 */
function AgentBar() {
  const router = useRouter();
  const [question, setQuestion] = useState("");

  function ask(text: string) {
    const questionText = text.trim();
    router.push(questionText ? `/assistent?frage=${encodeURIComponent(questionText)}` : "/assistent");
  }

  function handleAsk(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    ask(question);
  }

  return (
    <section id="assistant" aria-label={t("Assistent")} className={cn(anchorClass, "space-y-3")}>
      <form onSubmit={handleAsk} className="flex items-center gap-3 rounded-full border border-input bg-card pl-4 pr-2 transition-colors focus-within:border-foreground">
        <Faultier groesse={30} />
        <label htmlFor="assistant-question" className="sr-only">{t("Frage an den Assistenten")}</label>
        <input
          id="assistant-question"
          type="text"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder={t("Was möchtest du tun?")}
          className="h-14 min-w-0 flex-1 bg-transparent text-[17px] outline-none placeholder:text-muted-foreground"
        />
        <Button type="submit" size="icon" variant="ghost" aria-label={t("Fragen")}>
          <CornerDownLeft aria-hidden="true" />
        </Button>
      </form>
      <div className="flex flex-wrap gap-2">
        {vorschlaege.map((vorschlag) => (
          <button
            key={vorschlag}
            type="button"
            onClick={() => ask(vorschlag)}
            className="rounded-full border border-input bg-card px-3.5 py-1.5 text-[15px] text-muted-foreground transition-colors hover:border-foreground hover:text-foreground"
          >
            {vorschlag}
          </button>
        ))}
      </div>
    </section>
  );
}

export function DashboardView({ data, error, isLoading, onRetry, username }: DashboardViewProps) {
  const now = useNow();
  const reference = useMemo(() => now ?? new Date(), [now]);

  if (isLoading && !data) {
    return <DashboardSkeleton />;
  }

  if (!data) {
    return <ErrorState message={error ?? t("Die Daten konnten nicht geladen werden.")} onRetry={onRetry} />;
  }

  return (
    <div className="space-y-4">
      <GreetingHeader username={username} reference={reference} />

      {error ? (
        <div className="flex flex-col gap-3 rounded-md border border-foreground/30 bg-muted px-4 py-2.5 text-[15px] sm:flex-row sm:items-center sm:justify-between" role="alert">
          <span>{error}</span>
          <Button type="button" size="sm" variant="outline" onClick={onRetry}>
            <RefreshCw aria-hidden="true" />
            {t("Erneut laden")}
          </Button>
        </div>
      ) : null}

      <AgentBar />

      <DayBranch data={data} reference={reference} />

      {/*
        Der Stundenplan über die ganze Breite (die Woche braucht sie), darunter
        die vier Listen als 2×2-Raster. Vorher zwei Spalten mit zwei Karten
        links und drei rechts: die Kanten lagen nie auf einer Höhe, und links
        unten blieb eine leere Fläche - an Tagen ohne Stunden die halbe Seite.
        Die Zeilen strecken ihre Karten (kein `items-start`), damit Nachbarn
        gleich hoch enden.
        Lernfortschritt, Noten und Belohnung stehen auf `/fortschritt`: sie
        beantworten "wie stehe ich?", nicht "was steht heute an?".
      */}
      <ScheduleCard />
      <div className="grid gap-4 lg:grid-cols-2">
        <StudyTasksCard tasks={data.studyTasks} />
        <HomeworkSection reference={reference} />
        <ExamsSection reference={reference} />
        <CalendarSection reference={reference} />
      </div>
    </div>
  );
}
