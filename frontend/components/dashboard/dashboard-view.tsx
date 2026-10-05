"use client";

import { useMemo, useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ArrowRight,
  Check,
  Clock3,
  CornerDownLeft,
  MapPin,
  Play,
  RefreshCw,
  Square,
  Timer,
  UserRound,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Faultier } from "@/components/assistant/faultier";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { CalendarSection } from "@/components/manage/calendar-section";
import { ExamsSection } from "@/components/manage/exams-section";
import { HomeworkSection } from "@/components/manage/homework-section";
import { ApiError, completeStudyTask, startStudyTask, stopStudyTask } from "@/lib/api";
import { describeSource } from "@/lib/ai-runtime";
import { Skeleton } from "@/components/ui/skeleton";
import {
  formatDuration,
  formatTime,
  getDayProgress,
  getLessonState,
} from "@/lib/format";
import { useNow } from "@/lib/use-now";
import { useGreeting } from "@/lib/use-greeting";
import { isDemoTaskId, type DashboardData, type DashboardLesson, type DashboardStudyTask, type LessonState, type StudyActionResult, type StudyTaskStatus } from "@/lib/types";
import { cn } from "@/lib/utils";
import { ScheduleCard } from "./schedule-view";

interface DashboardViewProps {
  data: DashboardData | null;
  error: string | null;
  isLoading: boolean;
  onRetry: () => void;
  username?: string | null;
}

const lessonStateLabels: Record<LessonState, string> = {
  completed: "Erledigt",
  current: "Läuft gerade",
  upcoming: "Als Nächstes",
};

const taskStatusLabels: Record<StudyTaskStatus, string> = {
  todo: "Offen",
  in_progress: "In Arbeit",
  completed: "Erledigt",
};

/**
 * Abstand, den ein Ankerziel oben frei haelt.
 *
 * Ohne das landet die Sprungmarke unter dem sticky Header, weil
 * `scroll-behavior: smooth` an der Fensteroberkante ausrichtet.
 */
const anchorClass = "scroll-mt-20";

const weekdayFormatter = new Intl.DateTimeFormat("de-DE", { weekday: "long", day: "numeric", month: "long" });

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
    <div className="space-y-4" aria-busy="true" aria-label="Dashboard wird geladen">
      <div className="space-y-2.5">
        <Skeleton className="h-3.5 w-36" />
        <Skeleton className="h-7 w-[28rem] max-w-full" />
      </div>
      <Skeleton className="h-[74px] w-full" />
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,380px)]">
        <div className="space-y-4">
          <Skeleton className="h-80 w-full" />
          <Skeleton className="h-32 w-full" />
        </div>
        <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-1">
          <Skeleton className="h-28 w-full" />
          <Skeleton className="h-40 w-full" />
          <Skeleton className="h-40 w-full" />
          <Skeleton className="h-28 w-full" />
        </div>
      </div>
    </div>
  );
}

function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="mx-auto flex max-w-md flex-col items-center py-20 text-center" role="alert">
      <h2 className="text-base font-semibold">Der Lernraum ist gerade nicht erreichbar</h2>
      <p className="mt-1.5 text-[13px] leading-5 text-muted-foreground">{message}</p>
      <Button type="button" variant="outline" className="mt-5" onClick={onRetry}>
        <RefreshCw aria-hidden="true" />
        Erneut versuchen
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
        <p className="text-[13px] text-muted-foreground" suppressHydrationWarning>
          {weekdayFormatter.format(reference)}
        </p>
        {status === "loading" ? (
          <Skeleton className="mt-2 h-7 w-[28rem] max-w-full" />
        ) : status === "error" || !greeting || !herkunft ? (
          <>
            <h1 className="mt-1 text-[22px] font-semibold leading-8 tracking-tight">
              Willkommen{username ? `, ${username}` : ""}.
            </h1>
            {error ? <p className="mt-1 text-[13px] text-muted-foreground">{error}</p> : null}
          </>
        ) : (
          <>
            <h1 className="mt-1 max-w-3xl text-[22px] font-semibold leading-8 tracking-tight">{greeting.text}</h1>
            <p className="mt-1.5 text-xs text-muted-foreground">{herkunft.text}</p>
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
        aria-label="Tagessatz neu formulieren"
        title="Neu formulieren"
      >
        <RefreshCw className={cn(refreshing && "animate-spin")} aria-hidden="true" />
      </Button>
    </header>
  );
}

/**
 * Eine Zeile fuer den Tagesstand: die laufende bzw. naechste Stunde vorn,
 * dahinter vier Zahlen. Ersetzt den frueheren Hero-Block, den Fortschrittsring,
 * die Nutzungskarte und die eigene Karte fuer die naechste Stunde - die
 * standen alle untereinander und belegten den ersten Bildschirm, ohne mehr zu
 * sagen als diese Zeile.
 */
/** Haarlinien zwischen den Zahlen: 2x2 auf dem Telefon, sonst eine Reihe. */
function statCellClass(index: number) {
  return cn(
    "border-border px-4 py-3 md:border-l",
    index > 0 && "sm:border-l",
    index % 2 === 1 && "max-sm:border-l",
    index >= 2 && "max-sm:border-t",
  );
}

function TodayStrip({ data, dayProgress, lesson, reference }: { data: DashboardData; dayProgress: number; lesson: DashboardLesson | undefined; reference: Date }) {
  const state = lesson ? getLessonState(lesson, reference) : "upcoming";
  const stats = [
    { label: "Stunden", value: `${data.summary.lessonsCompleted}/${data.summary.lessonsTotal}` },
    { label: "Lernblöcke", value: `${data.summary.tasksCompleted}/${data.summary.tasksTotal}` },
    { label: "Hausaufgaben offen", value: String(data.summary.homeworkPending) },
  ];
  return (
    <section aria-label="Tagesstand" className="grid overflow-hidden rounded-lg border border-border md:grid-cols-[minmax(0,1.6fr)_repeat(4,minmax(0,1fr))]">
      <Link
        id="focus"
        href="#schedule"
        className={cn(anchorClass, "group flex min-w-0 items-center gap-3 px-4 py-3 transition-colors hover:bg-muted/50 max-md:border-b max-md:border-border")}
      >
        <div className="min-w-0 flex-1">
          <p className="text-xs text-muted-foreground">{lesson ? lessonStateLabels[state] : "Unterricht"}</p>
          {lesson ? (
            <>
              <p className="truncate text-[15px] font-semibold tracking-tight">{lesson.subject}</p>
              <p className="flex flex-wrap gap-x-3 text-xs text-muted-foreground">
                <span className="inline-flex items-center gap-1 tabular-nums">
                  <Clock3 className="h-3 w-3" aria-hidden="true" />
                  {formatTime(lesson.startTime)}–{formatTime(lesson.endTime)}
                </span>
                {lesson.room ? (
                  <span className="inline-flex items-center gap-1">
                    <MapPin className="h-3 w-3" aria-hidden="true" />
                    {lesson.room}
                  </span>
                ) : null}
                {lesson.teacher ? (
                  <span className="inline-flex min-w-0 items-center gap-1 truncate">
                    <UserRound className="h-3 w-3" aria-hidden="true" />
                    {lesson.teacher}
                  </span>
                ) : null}
              </p>
            </>
          ) : (
            <p className="text-[15px] font-semibold tracking-tight">Heute kein Unterricht mehr</p>
          )}
        </div>
        <ArrowRight className="h-4 w-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5" aria-hidden="true" />
      </Link>
      <div className="grid grid-cols-2 sm:grid-cols-4 md:contents">
        {stats.map((stat, index) => (
          <div key={stat.label} className={statCellClass(index)}>
            <p className="text-xs text-muted-foreground">{stat.label}</p>
            <p className="mt-0.5 text-lg font-semibold tabular-nums tracking-tight">{stat.value}</p>
          </div>
        ))}
        <div className={statCellClass(stats.length)}>
          <p className="text-xs text-muted-foreground">Tag geschafft</p>
          <div className="mt-0.5 flex items-center gap-3">
            <p className="text-lg font-semibold tabular-nums tracking-tight">{Math.round(dayProgress)}%</p>
            <ProgressBar value={dayProgress} label="Tagesfortschritt" className="flex-1" />
          </div>
        </div>
      </div>
    </section>
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
      setActionError(error instanceof ApiError ? error.message : "Die Lernaktion konnte nicht ausgeführt werden.");
    } finally {
      setBusyTaskId(null);
    }
  }

  return (
    <Card id="study-tasks" className={anchorClass}>
      <CardHeader className="flex-row items-center justify-between space-y-0 pb-3">
        <CardTitle>Heute lernen</CardTitle>
        <div className="flex items-center gap-3">
          <span className="text-xs tabular-nums text-muted-foreground">{openCount} offen</span>
          <Link href="/lernen" className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
            Lernplan
            <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
          </Link>
        </div>
      </CardHeader>
      <CardContent className="px-0 pb-1.5">
        {actionError ? <p className="mx-4 mb-3 rounded-md border border-foreground/30 bg-muted px-3 py-2 text-[13px]" role="alert">{actionError}</p> : null}
        {taskList.length === 0 ? (
          <div className="px-4 pb-3">
            <EmptyState icon={Timer} title="Keine Lernaufgaben" description="Geplante Lernschritte erscheinen hier." />
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
                    aria-label={completed ? `${task.title} erledigt` : `${task.title} abschließen`}
                    className={cn(
                      "flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-full border transition-colors",
                      completed ? "border-foreground bg-foreground text-background" : "border-input hover:border-foreground",
                    )}
                  >
                    {completed ? <Check className="h-3 w-3" strokeWidth={3} aria-hidden="true" /> : null}
                  </button>
                  <div className="min-w-0 flex-1">
                    <p className={cn("truncate text-[13px] font-medium", completed && "text-muted-foreground line-through")}>{task.title}</p>
                    <p className="truncate text-xs text-muted-foreground">
                      {[task.subject, task.topic].filter(Boolean).join(" · ")}
                      {!completed && task.status === "in_progress" && !active ? ` · ${taskStatusLabels[task.status]}` : null}
                    </p>
                  </div>
                  {active && startedAt ? (
                    <span className="inline-flex items-center gap-1 text-xs font-medium tabular-nums">
                      <Timer className="h-3.5 w-3.5" aria-hidden="true" />
                      {elapsedLabel(startedAt, now)}
                    </span>
                  ) : (
                    <span className="hidden w-14 text-right text-xs tabular-nums text-muted-foreground sm:block">{formatDuration(task.durationMinutes)}</span>
                  )}
                  <div className="hidden w-16 sm:block">
                    <ProgressBar value={task.progress} label={`Fortschritt ${task.title}`} />
                  </div>
                  {completed ? (
                    <span className="w-[72px] text-right text-xs text-muted-foreground">Erledigt</span>
                  ) : active ? (
                    <Button type="button" size="sm" variant="outline" className="w-[72px]" onClick={() => void handleAction(task, "stop")} disabled={busy || demo} aria-label={`${task.title} stoppen`}>
                      <Square className="!size-3 fill-current" aria-hidden="true" />
                      Stopp
                    </Button>
                  ) : (
                    <Button type="button" size="sm" variant="outline" className="w-[72px]" onClick={() => void handleAction(task, "start")} disabled={busy || demo} aria-label={`${task.title} starten`}>
                      <Play className="!size-3 fill-current" aria-hidden="true" />
                      {demo ? "Demo" : "Start"}
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
function AssistantCard() {
  const router = useRouter();
  const [question, setQuestion] = useState("");

  function handleAsk(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const questionText = question.trim();
    router.push(questionText ? `/assistent?frage=${encodeURIComponent(questionText)}` : "/assistent");
  }

  return (
    <Card id="assistant" className={anchorClass}>
      <CardHeader className="flex flex-row items-center justify-between gap-3 space-y-0 pb-3">
        <div className="min-w-0 space-y-0.5">
          <CardTitle>
            <Link href="/assistent" className="hover:underline hover:underline-offset-2">
              Assistent
            </Link>
          </CardTitle>
          <CardDescription className="text-xs">Unterlagen, Planung, Einträge – auch per Sprache.</CardDescription>
        </div>
        <Link href="/assistent" aria-label="Zum Assistenten" className="-my-2 shrink-0">
          <Faultier groesse={44} koerper />
        </Link>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleAsk} className="relative">
          <label htmlFor="assistant-question" className="sr-only">Frage an den Assistenten</label>
          <Input
            id="assistant-question"
            type="text"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="Was steht diese Woche an?"
            className="pr-10"
          />
          <button
            type="submit"
            className="absolute right-1 top-1 flex h-7 w-7 items-center justify-center rounded-[5px] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            aria-label="Fragen"
          >
            <CornerDownLeft className="h-4 w-4" aria-hidden="true" />
          </button>
        </form>
      </CardContent>
    </Card>
  );
}

export function DashboardView({ data, error, isLoading, onRetry, username }: DashboardViewProps) {
  const now = useNow();
  const reference = useMemo(() => now ?? new Date(), [now]);
  const focusLesson = useMemo(() => {
    if (!data) {
      return undefined;
    }
    return data.lessons.find((lesson) => getLessonState(lesson, reference) === "current") ?? data.lessons.find((lesson) => getLessonState(lesson, reference) === "upcoming");
  }, [data, reference]);

  if (isLoading && !data) {
    return <DashboardSkeleton />;
  }

  if (!data) {
    return <ErrorState message={error ?? "Die Daten konnten nicht geladen werden."} onRetry={onRetry} />;
  }

  const dayProgress = data.summary.dayProgress > 0 ? data.summary.dayProgress : getDayProgress(data.lessons, reference);

  return (
    <div className="space-y-4">
      <GreetingHeader username={username} reference={reference} />

      {error ? (
        <div className="flex flex-col gap-3 rounded-md border border-foreground/30 bg-muted px-4 py-2.5 text-[13px] sm:flex-row sm:items-center sm:justify-between" role="alert">
          <span>{error}</span>
          <Button type="button" size="sm" variant="outline" onClick={onRetry}>
            <RefreshCw aria-hidden="true" />
            Erneut laden
          </Button>
        </div>
      ) : null}

      <TodayStrip data={data} dayProgress={dayProgress} lesson={focusLesson} reference={reference} />

      {/*
        Zwei Spalten: links, was den Tag strukturiert - Stundenplan und
        Lernblöcke, beide brauchen Breite -, rechts das Fällige als schmale
        Listen. Vorher standen Hausaufgaben und Prüfungen als zwei halbe
        Karten unter dem Stundenplan, und die rechte Spalte endete nach
        Kalender und Assistent im Leeren. Zwischen lg und xl läuft die rechte
        Spalte zweispaltig unter der linken.
        Lernfortschritt, Noten und Belohnung stehen auf `/fortschritt`: sie
        beantworten "wie stehe ich?", nicht "was steht heute an?".
      */}
      <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,380px)]">
        <div className="space-y-4">
          <ScheduleCard />
          <StudyTasksCard tasks={data.studyTasks} />
        </div>
        <div className="grid items-start gap-4 lg:grid-cols-2 xl:grid-cols-1">
          <AssistantCard />
          <HomeworkSection reference={reference} />
          <ExamsSection reference={reference} />
          <CalendarSection reference={reference} />
        </div>
      </div>
    </div>
  );
}
