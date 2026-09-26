"use client";

import { useMemo, useState, type CSSProperties } from "react";
import Link from "next/link";
import {
  AlarmClock,
  BookOpen,
  CalendarDays,
  Check,
  CheckCircle2,
  ChevronRight,
  Clock3,
  GraduationCap,
  ListChecks,
  MapPin,
  Play,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Target,
  Timer,
  TrendingUp,
  UserRound,
  X,
  Zap,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { ApiError, completeStudyTask, startStudyTask, stopStudyTask } from "@/lib/api";
import { Skeleton } from "@/components/ui/skeleton";
import {
  formatDate,
  formatDueDate,
  formatDuration,
  formatTime,
  getCountdown,
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
  upcoming: "Kommt gleich",
};

const taskStatusLabels: Record<StudyTaskStatus, string> = {
  todo: "Offen",
  in_progress: "In Arbeit",
  completed: "Erledigt",
};

function taskVariant(status: StudyTaskStatus) {
  if (status === "completed") {
    return "success" as const;
  }
  if (status === "in_progress") {
    return "info" as const;
  }
  return "secondary" as const;
}

function ProgressBar({ value, className, indicatorClassName }: { value: number; className?: string; indicatorClassName?: string }) {
  const percentage = Math.min(100, Math.max(0, value));
  return (
    <div className={cn("h-2 overflow-hidden bg-muted", className)} role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percentage}>
      <div className={cn("h-full bg-primary transition-all duration-500", indicatorClassName)} style={{ width: `${percentage}%` }} />
    </div>
  );
}

function ProgressRing({ value }: { value: number }) {
  const percentage = Math.min(100, Math.max(0, value));
  const style: CSSProperties = {
    background: `conic-gradient(currentColor ${percentage * 3.6}deg, color-mix(in srgb, currentColor 18%, transparent) 0deg)`,
  };
  return (
    <div className="relative flex h-32 w-32 shrink-0 items-center justify-center p-2 text-primary-foreground" style={style}>
      <div className="flex h-full w-full flex-col items-center justify-center bg-primary text-primary-foreground">
        <span className="text-3xl font-extrabold tracking-tight">{percentage}%</span>
        <span className="text-[10px] font-bold uppercase tracking-[0.16em] opacity-75">geschafft</span>
      </div>
    </div>
  );
}

function DashboardSkeleton() {
  return (
    <div className="space-y-6" aria-busy="true" aria-label="Dashboard wird geladen">
      <div className="flex flex-col justify-between gap-5 sm:flex-row sm:items-end">
        <div className="space-y-3">
          <Skeleton className="h-4 w-32" />
          <Skeleton className="h-10 w-72 max-w-full" />
          <Skeleton className="h-4 w-56" />
        </div>
        <Skeleton className="h-10 w-32" />
      </div>
      <Skeleton className="h-52 w-full rounded-2xl" />
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.45fr)_minmax(330px,0.85fr)]">
        <div className="space-y-6">
          <Skeleton className="h-72 w-full rounded-2xl" />
          <Skeleton className="h-96 w-full rounded-2xl" />
          <Skeleton className="h-80 w-full rounded-2xl" />
        </div>
        <div className="space-y-6">
          <Skeleton className="h-80 w-full rounded-2xl" />
          <Skeleton className="h-72 w-full rounded-2xl" />
          <Skeleton className="h-80 w-full rounded-2xl" />
          <Skeleton className="h-64 w-full rounded-2xl" />
        </div>
      </div>
    </div>
  );
}

function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <Card className="mx-auto max-w-2xl border-destructive/20 bg-card" role="alert">
      <CardContent className="flex flex-col items-center px-6 py-14 text-center sm:px-10">
        <span className="mb-5 flex h-14 w-14 items-center justify-center rounded-2xl bg-destructive/10 text-destructive">
          <X className="h-7 w-7" aria-hidden="true" />
        </span>
        <h2 className="text-xl font-extrabold">Dein Lernraum ist gerade nicht erreichbar</h2>
        <p className="mt-2 max-w-md text-sm leading-6 text-muted-foreground">{message}</p>
        <Button type="button" className="mt-7" onClick={onRetry}>
          <RefreshCw aria-hidden="true" />
          Erneut versuchen
        </Button>
      </CardContent>
    </Card>
  );
}

function FocusLessonCard({ lesson, reference }: { lesson: DashboardLesson | undefined; reference: Date }) {
  const state = lesson ? getLessonState(lesson, reference) : "upcoming";
  const isCurrent = state === "current";
  const title = isCurrent ? "Du bist mittendrin." : lesson ? "Als Nächstes steht an" : "Dein Stundenplan ist frei.";

  return (
    <Card id="focus" className="relative overflow-hidden border-foreground bg-card">
      <CardHeader className="relative pb-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-[0.18em] text-primary">
            <Target className="h-4 w-4" aria-hidden="true" />
            Tagesfokus
          </div>
          {lesson ? <Badge variant={isCurrent ? "success" : "info"}>{isCurrent ? "Jetzt" : "Als Nächstes"}</Badge> : <Badge variant="secondary">Kein Termin</Badge>}
        </div>
        <CardTitle className="mt-3 text-2xl sm:text-3xl">{title}</CardTitle>
        <CardDescription className="text-base">Ein klarer nächster Schritt macht den Unterschied.</CardDescription>
      </CardHeader>
      <CardContent className="relative">
        {lesson ? (
          <div className="flex flex-col gap-5 rounded-2xl border border-border/70 bg-muted/35 p-4 sm:flex-row sm:items-center sm:justify-between sm:p-5">
            <div className="min-w-0">
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <span className="text-sm font-extrabold text-primary">{lesson.subject}</span>
                <span className="text-xs text-muted-foreground">{lessonStateLabels[state]}</span>
              </div>
              <h3 className="truncate text-xl font-bold tracking-tight">{lesson.title}</h3>
              <div className="mt-3 flex flex-wrap gap-x-5 gap-y-2 text-sm text-muted-foreground">
                <span className="inline-flex items-center gap-1.5">
                  <Clock3 className="h-4 w-4" aria-hidden="true" />
                  {formatTime(lesson.startTime)} – {formatTime(lesson.endTime)} Uhr
                </span>
                <span className="inline-flex items-center gap-1.5">
                  <MapPin className="h-4 w-4" aria-hidden="true" />
                  {lesson.room}
                </span>
                <span className="inline-flex items-center gap-1.5">
                  <UserRound className="h-4 w-4" aria-hidden="true" />
                  {lesson.teacher}
                </span>
              </div>
            </div>
            <Link href="#schedule" className={cn(buttonVariants({ variant: "outline", size: "sm" }), "shrink-0 self-start sm:self-center")}>
              Stundenplan
              <ChevronRight aria-hidden="true" />
            </Link>
          </div>
        ) : (
          <EmptyState icon={GraduationCap} title="Keine Unterrichtsstunden" description="Für heute sind keine Stunden eingetragen. Nutze die Zeit für deine offenen Aufgaben." />
        )}
      </CardContent>
    </Card>
  );
}

function ExamsCard({ data, reference }: { data: DashboardData; reference: Date }) {
  return (
    <Card id="exams">
      <CardHeader className="flex-row items-start justify-between space-y-0 pb-4">
        <div>
          <CardTitle className="flex items-center gap-2 text-lg">
            <TrendingUp className="h-5 w-5 text-primary" aria-hidden="true" />
            Nächste Prüfungen
          </CardTitle>
          <CardDescription className="mt-1">Mit Countdown und Vorbereitungsfortschritt.</CardDescription>
        </div>
        <Badge variant="warning">{data.summary.upcomingExams} offen</Badge>
      </CardHeader>
      <CardContent>
        {data.upcomingExams.length === 0 ? (
          <EmptyState icon={CheckCircle2} title="Keine Prüfungstermine" description="Keine anstehenden Prüfungen. Genieße den klaren Blick." />
        ) : (
          <div className="space-y-4">
            {data.upcomingExams.slice(0, 4).map((exam) => {
              const countdown = getCountdown(exam.date, reference);
              return (
                <div key={exam.id} className="border border-border p-4 transition-colors hover:border-foreground hover:bg-muted/40">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-xs font-bold uppercase tracking-[0.14em] text-primary">{exam.subject}</p>
                      <h3 className="mt-1 truncate text-sm font-extrabold">{exam.title}</h3>
                    </div>
                    <Badge variant={countdown.isOverdue ? "outline" : countdown.days <= 3 ? "warning" : "info"}>
                      {countdown.isOverdue ? "Vorbei" : countdown.label}
                    </Badge>
                  </div>
                  <div className="mt-3 flex items-center gap-2 text-xs font-semibold text-muted-foreground">
                    <CalendarDays className="h-3.5 w-3.5" aria-hidden="true" />
                    {formatDate(exam.date, { weekday: "short", day: "2-digit", month: "short", year: "numeric" })}
                  </div>
                  {exam.topics.length > 0 ? (
                    <div className="mt-3 flex flex-wrap gap-1.5">
                      {exam.topics.slice(0, 3).map((topic) => (
                        <span key={topic} className="rounded-md bg-muted px-2 py-1 text-[10px] font-semibold text-muted-foreground">
                          {topic}
                        </span>
                      ))}
                      {exam.topics.length > 3 ? <span className="rounded-md bg-muted px-2 py-1 text-[10px] font-semibold text-muted-foreground">+{exam.topics.length - 3}</span> : null}
                    </div>
                  ) : null}
                  <div className="mt-4 flex items-center justify-between gap-3 text-xs font-semibold">
                    <span className="text-muted-foreground">Vorbereitung</span>
                    <span className={exam.progress === null ? "text-muted-foreground" : "text-primary"}>
                      {exam.progress === null ? "noch nicht erfasst" : `${exam.progress}%`}
                    </span>
                  </div>
                  {exam.progress === null ? null : <ProgressBar value={exam.progress} className="mt-2 h-1.5" />}
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function HomeworkCard({ data, reference }: { data: DashboardData; reference: Date }) {
  const openHomework = data.homework.filter((item) => !item.completed);
  return (
    <Card id="homework">
      <CardHeader className="flex-row items-start justify-between space-y-0 pb-4">
        <div>
          <CardTitle className="flex items-center gap-2 text-lg">
            <ListChecks className="h-5 w-5 text-primary" aria-hidden="true" />
            Offene Hausaufgaben
          </CardTitle>
          <CardDescription className="mt-1">Was als Nächstes auf deinem Schreibtisch wartet.</CardDescription>
        </div>
        <Badge variant={openHomework.length ? "warning" : "success"}>{openHomework.length} offen</Badge>
      </CardHeader>
      <CardContent>
        {openHomework.length === 0 ? (
          <EmptyState icon={CheckCircle2} title="Alles erledigt" description="Für heute sind keine offenen Hausaufgaben eingetragen." />
        ) : (
          <div className="space-y-3">
            {openHomework.slice(0, 4).map((item) => (
              <div key={item.id} className="flex gap-3 rounded-2xl border border-border/70 p-3.5">
                <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center bg-muted text-foreground">
                  <BookOpen className="h-4 w-4" aria-hidden="true" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="truncate text-sm font-extrabold">{item.title}</p>
                    <span className="text-xs font-semibold text-primary">{item.subject}</span>
                  </div>
                  {item.description ? <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">{item.description}</p> : null}
                  <div className="mt-2 flex items-center gap-1.5 text-xs font-semibold text-muted-foreground">
                    <AlarmClock className="h-3.5 w-3.5" aria-hidden="true" />
                    <span className={cn(formatDueDate(item.dueDate, reference) === "Überfällig" && "text-destructive")}>{formatDueDate(item.dueDate, reference)}</span>
                    <span className="text-border">·</span>
                    <span>{formatDate(item.dueDate, { day: "2-digit", month: "short" })}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
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

/**
 * Die Kopfzeile des Dashboards.
 *
 * Der Satz kommt vom Backend und ist einmal pro Tag vom KI-Provider erzeugt.
 * Steht dort `source: "fallback"`, hat kein Modell geschrieben - der Satz wurde
 * aus den Daten gebaut. Das wird sichtbar gemacht, damit niemand einen
 * Zufallssatz fuer eine KI-Aussage haelt.
 */
function GreetingHeader({ username }: { username?: string | null }) {
  const { greeting, error, status, refresh, refreshing } = useGreeting();

  return (
    <header id="overview" className="flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
      <div className="min-w-0">
        <p className="mb-2 flex items-center gap-1.5 text-sm font-bold uppercase tracking-[0.18em] text-primary">
          <Sparkles className="h-4 w-4" aria-hidden="true" />
          Dein Tagesbriefing
        </p>
        {status === "loading" ? (
          <Skeleton className="h-9 w-full max-w-xl" />
        ) : status === "error" || !greeting ? (
          <>
            <h1 className="text-2xl font-extrabold tracking-[-0.03em] sm:text-3xl">
              Willkommen{username ? `, ${username}` : ""}.
            </h1>
            <p className="mt-2 text-sm text-muted-foreground">
              {error ?? "Der Willkommenssatz ist gerade nicht verfügbar."}
            </p>
          </>
        ) : (
          <>
            <h1 className="max-w-3xl text-2xl font-extrabold leading-snug tracking-[-0.03em] sm:text-3xl">
              {greeting.text}
            </h1>
            <p className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
              <Badge variant={greeting.source === "ai" ? "info" : "secondary"}>
                {greeting.source === "ai" ? "Von der KI" : "Aus deinen Daten"}
              </Badge>
              {greeting.source === "ai" && greeting.model ? <span>{greeting.model}</span> : null}
              {greeting.source === "ai" && greeting.generatedOn ? (
                <>
                  <span className="text-border">·</span>
                  <span>für {formatDate(new Date(`${greeting.generatedOn}T12:00:00`))}</span>
                </>
              ) : null}
            </p>
          </>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => void refresh()}
          disabled={refreshing || status === "loading"}
        >
          <RefreshCw className={cn(refreshing && "animate-spin")} aria-hidden="true" />
          Neu formulieren
        </Button>
      </div>
    </header>
  );
}

function StudyTasksCard({ tasks }: { tasks: DashboardStudyTask[] }) {
  const now = useNow();
  const [taskOverrides, setTaskOverrides] = useState<Record<string, DashboardStudyTask>>({});
  const [busyTaskId, setBusyTaskId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const taskList = tasks.map((task) => taskOverrides[task.id] ?? task);

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
    <Card id="study-tasks">
      <CardHeader className="flex-row items-start justify-between space-y-0 pb-4">
        <div>
          <CardTitle className="flex items-center gap-2 text-lg">
            <Zap className="h-5 w-5 text-primary" aria-hidden="true" />
            Heute lernen
          </CardTitle>
          <CardDescription className="mt-1">Kleine Fokusblöcke, großer Fortschritt.</CardDescription>
        </div>
        <Badge variant="info">{taskList.filter((task) => task.status !== "completed").length} Tasks</Badge>
      </CardHeader>
      <CardContent>
        {actionError ? <p className="mb-4 rounded-xl border border-destructive/20 bg-destructive/10 px-3 py-2 text-sm text-destructive" role="alert">{actionError}</p> : null}
        {taskList.length === 0 ? (
          <EmptyState icon={Timer} title="Keine Lernaufgaben" description="Deine nächsten Lernschritte werden hier angezeigt, sobald sie geplant sind." />
        ) : (
          <div className="space-y-3">
            {taskList.slice(0, 4).map((task) => {
              const active = task.session?.state === "active";
              const completed = task.status === "completed";
              const demo = isDemoTaskId(task.id);
              const busy = busyTaskId === task.id;
              const startedAt = task.session?.startedAt ?? task.startedAt;
              return (
                <div key={task.id} className={cn("border p-4 transition-colors", active ? "border-foreground bg-muted" : "border-border")}>
                  <div className="flex items-start gap-3">
                    <span className={cn("mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center", completed ? "bg-foreground text-background" : "bg-secondary text-secondary-foreground")}>
                      {completed ? <Check className="h-4 w-4" aria-hidden="true" /> : <BookOpen className="h-4 w-4" aria-hidden="true" />}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className={cn("truncate text-sm font-extrabold", completed && "text-muted-foreground line-through")}>{task.title}</p>
                        <Badge variant={taskVariant(task.status)}>{taskStatusLabels[task.status]}</Badge>
                      </div>
                      <p className="mt-1 text-xs font-semibold text-muted-foreground">{task.subject} · {task.topic}</p>
                      {active && startedAt ? <p className="mt-1 flex items-center gap-1 text-xs font-bold text-primary"><Timer className="h-3.5 w-3.5" aria-hidden="true" />{elapsedLabel(startedAt, now)} Min.</p> : null}
                    </div>
                    <span className="shrink-0 text-xs font-bold text-muted-foreground">{formatDuration(task.durationMinutes)}</span>
                  </div>
                  <div className="mt-4 flex items-center gap-3">
                    <ProgressBar value={task.progress} className="h-1.5 flex-1" indicatorClassName={completed ? "bg-foreground" : "bg-primary"} />
                    <span className="w-9 text-right text-xs font-bold text-muted-foreground">{task.progress}%</span>
                    {completed ? (
                      <span className="flex h-8 w-8 items-center justify-center bg-foreground text-background" aria-label="Erledigt">
                        <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
                      </span>
                    ) : active ? (
                      <div className="flex items-center gap-2">
                        <Button type="button" size="sm" variant="outline" onClick={() => void handleAction(task, "complete")} disabled={busy || demo} aria-label={`${task.title} abschließen`}>
                          <Check aria-hidden="true" />
                          Fertig
                        </Button>
                        <Button type="button" size="sm" variant="secondary" onClick={() => void handleAction(task, "stop")} disabled={busy || demo} aria-label={`${task.title} stoppen`}>
                          <Timer aria-hidden="true" />
                          Stopp
                        </Button>
                      </div>
                    ) : (
                      <Button type="button" size="sm" variant="outline" onClick={() => void handleAction(task, "start")} disabled={busy || demo} aria-label={`${task.title} starten`}>
                        <Play aria-hidden="true" />
                        {demo ? "Demo" : "Start"}
                      </Button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function UsageCard({ data }: { data: DashboardData }) {
  return (
    <Card id="usage">
      <CardHeader className="flex-row items-start justify-between space-y-0 pb-4">
        <div>
          <CardTitle className="flex items-center gap-2 text-lg">
            <ShieldCheck className="h-5 w-5 text-primary" aria-hidden="true" />
            Nutzung & Datenschutz
          </CardTitle>
          <CardDescription className="mt-1">Deine Lernaktivität bleibt nachvollziehbar.</CardDescription>
        </div>
        <Badge variant="secondary">Privat</Badge>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-3 gap-2">
          <div className="rounded-xl bg-muted/50 p-3">
            <p className="text-lg font-extrabold">{data.summary.tasksCompleted}/{data.summary.tasksTotal}</p>
            <p className="mt-1 text-[10px] font-bold uppercase tracking-[0.1em] text-muted-foreground">Fokusblöcke</p>
          </div>
          <div className="rounded-xl bg-muted/50 p-3">
            <p className="text-lg font-extrabold">{data.summary.lessonsCompleted}/{data.summary.lessonsTotal}</p>
            <p className="mt-1 text-[10px] font-bold uppercase tracking-[0.1em] text-muted-foreground">Stunden</p>
          </div>
          <div className="rounded-xl bg-muted/50 p-3">
            <p className="text-lg font-extrabold">{data.summary.homeworkPending}</p>
            <p className="mt-1 text-[10px] font-bold uppercase tracking-[0.1em] text-muted-foreground">Offen</p>
          </div>
        </div>
        <div className="flex gap-3 rounded-xl border border-border/70 p-3">
          <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden="true" />
          <p className="text-xs leading-5 text-muted-foreground">Private Lerninhalte werden nicht im Browser zwischengespeichert. Die Anmeldung nutzt eine serverseitige Session.</p>
        </div>
      </CardContent>
    </Card>
  );
}

function CalendarCard({ events }: { events: DashboardData["calendarEvents"] }) {
  return (
    <Card id="calendar">
      <CardHeader className="flex-row items-start justify-between space-y-0 pb-4">
        <div>
          <CardTitle className="flex items-center gap-2 text-lg">
            <CalendarDays className="h-5 w-5 text-primary" aria-hidden="true" />
            Kalender
          </CardTitle>
          <CardDescription className="mt-1">Termine und Erinnerungen für deinen Lernalltag.</CardDescription>
        </div>
        <Badge variant="secondary">{events.length} Termine</Badge>
      </CardHeader>
      <CardContent>
        {events.length === 0 ? (
          <EmptyState icon={CalendarDays} title="Keine Termine" description="Dein Kalender ist aktuell frei. Ein guter Moment zum Durchatmen." />
        ) : (
          <div className="space-y-3">
            {events.slice(0, 4).map((event) => (
              <div key={event.id} className="flex gap-3 rounded-2xl border border-border/70 p-3">
                <div className="flex h-12 w-12 shrink-0 flex-col items-center justify-center rounded-xl bg-secondary text-secondary-foreground">
                  <span className="text-[10px] font-bold uppercase">{formatDate(event.startTime, { month: "short" })}</span>
                  <span className="text-lg font-extrabold leading-5">{formatDate(event.startTime, { day: "2-digit" })}</span>
                </div>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-extrabold">{event.title}</p>
                  <p className="mt-1 text-xs font-semibold text-muted-foreground">
                    {event.allDay ? "Ganztägig" : `${formatTime(event.startTime)} – ${formatTime(event.endTime)}`}
                  </p>
                  {event.description ? <p className="mt-1 line-clamp-1 text-xs text-muted-foreground">{event.description}</p> : null}
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function AssistantCard() {
  return (
    <Card id="assistant" className="relative overflow-hidden border-foreground bg-card">
      <CardHeader className="relative pb-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <span className="flex h-12 w-12 shrink-0 items-center justify-center bg-primary text-primary-foreground">
              <Sparkles className="h-6 w-6" aria-hidden="true" />
            </span>
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <CardTitle className="text-xl">Dein AI-Lernassistent</CardTitle>
                <Badge variant="secondary"><span className="h-1.5 w-1.5 bg-muted-foreground" />Noch nicht verbunden</Badge>
              </div>
              <CardDescription className="mt-1">KI-Gateway folgt in einer späteren Phase.</CardDescription>
            </div>
          </div>
          <span className="hidden items-center gap-1.5 border border-border bg-background px-3 py-1.5 text-xs font-bold text-muted-foreground sm:inline-flex">
            <Sparkles className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
            Lernhilfe vorbereitet
          </span>
        </div>
      </CardHeader>
      <CardContent className="relative">
        <div className="flex min-h-32 flex-col items-center justify-center rounded-2xl border border-dashed border-border bg-background/50 px-5 py-8 text-center" role="status">
          <Sparkles className="mb-3 h-7 w-7 text-primary" aria-hidden="true" />
          <p className="font-bold">Noch nicht verbunden</p>
          <p className="mt-1 max-w-sm text-sm leading-6 text-muted-foreground">Die KI-Unterstützung ist für eine spätere Phase vorbereitet.</p>
        </div>
        <p className="mt-3 text-[11px] leading-5 text-muted-foreground">Es werden keine Lerninhalte an externe Dienste übertragen.</p>
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
    <div className="space-y-6">
      <GreetingHeader username={username} />

      {error ? (
        <div className="flex flex-col gap-3 rounded-2xl border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm text-destructive sm:flex-row sm:items-center sm:justify-between" role="alert">
          <span>{error}</span>
          <Button type="button" size="sm" variant="outline" onClick={onRetry}>
            <RefreshCw aria-hidden="true" />
            Retry
          </Button>
        </div>
      ) : null}

      <Card className="relative overflow-hidden border-primary bg-primary text-primary-foreground">
        <CardContent className="relative flex flex-col gap-8 p-6 sm:p-8 lg:flex-row lg:items-center lg:justify-between">
          <div className="max-w-2xl">
            <div className="mb-4 flex flex-wrap items-center gap-2">
              <Badge className="border-primary-foreground/20 bg-primary-foreground/15 text-primary-foreground">Heute</Badge>
              <span className="text-xs font-bold uppercase tracking-[0.18em] text-primary-foreground/70">Dein Tagesfortschritt</span>
            </div>
            <h2 className="text-2xl font-extrabold tracking-tight sm:text-3xl">Du machst den Unterschied.</h2>
            <p className="mt-2 max-w-xl text-sm leading-6 text-primary-foreground/75">Halte deinen Rhythmus. Jeder abgeschlossene Fokusblock zählt.</p>
            <div className="mt-7 grid max-w-xl grid-cols-3 gap-3 sm:gap-6">
              <div>
                <p className="text-2xl font-extrabold">{data.summary.lessonsCompleted}/{data.summary.lessonsTotal}</p>
                <p className="mt-1 text-[11px] font-bold uppercase tracking-[0.12em] text-primary-foreground/65">Stunden</p>
              </div>
              <div>
                <p className="text-2xl font-extrabold">{data.summary.tasksCompleted}/{data.summary.tasksTotal}</p>
                <p className="mt-1 text-[11px] font-bold uppercase tracking-[0.12em] text-primary-foreground/65">Fokusblöcke</p>
              </div>
              <div>
                <p className="text-2xl font-extrabold">{data.summary.homeworkPending}</p>
                <p className="mt-1 text-[11px] font-bold uppercase tracking-[0.12em] text-primary-foreground/65">Aufgaben</p>
              </div>
            </div>
            <ProgressBar value={dayProgress} className="mt-7 max-w-xl bg-primary-foreground/15" indicatorClassName="bg-primary-foreground" />
          </div>
          <ProgressRing value={dayProgress} />
        </CardContent>
      </Card>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.45fr)_minmax(330px,0.85fr)]">
        <div className="space-y-6">
          <FocusLessonCard lesson={focusLesson} reference={reference} />
          <AssistantCard />
          <ScheduleCard />
        </div>
        <div className="space-y-6">
          <ExamsCard data={data} reference={reference} />
          <HomeworkCard data={data} reference={reference} />
          <StudyTasksCard tasks={data.studyTasks} />
          <UsageCard data={data} />
          <CalendarCard events={data.calendarEvents} />
        </div>
      </div>

      <footer className="flex flex-col items-center justify-between gap-2 border-t border-border/70 pt-5 text-center text-xs text-muted-foreground sm:flex-row sm:text-left">
        <span>Ein ruhiger Ort für deinen nächsten Schritt.</span>
        <span className="inline-flex items-center gap-1.5"><Sparkles className="h-3.5 w-3.5 text-primary" aria-hidden="true" />Lernraum</span>
      </footer>
    </div>
  );
}
