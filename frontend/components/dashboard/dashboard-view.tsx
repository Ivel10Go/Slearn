"use client";

import { useMemo, useState, type CSSProperties, type FormEvent } from "react";
import Link from "next/link";
import {
  BookOpen,
  Check,
  CheckCircle2,
  ChevronRight,
  Clock3,
  GraduationCap,
  LoaderCircle,
  MapPin,
  Play,
  RefreshCw,
  Send,
  ShieldCheck,
  Sparkles,
  Target,
  Timer,
  UserRound,
  X,
  Zap,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { CalendarSection } from "@/components/manage/calendar-section";
import { ExamsSection } from "@/components/manage/exams-section";
import { HomeworkSection } from "@/components/manage/homework-section";
import { ApiError, askDocuments, completeStudyTask, startStudyTask, stopStudyTask } from "@/lib/api";
import { Skeleton } from "@/components/ui/skeleton";
import {
  formatDate,
  formatDuration,
  formatTime,
  getDayProgress,
  getLessonState,
} from "@/lib/format";
import { useNow } from "@/lib/use-now";
import { useGreeting } from "@/lib/use-greeting";
import { isDemoTaskId, type DashboardData, type DashboardLesson, type DashboardStudyTask, type DocumentAnswer, type LessonState, type StudyActionResult, type StudyTaskStatus } from "@/lib/types";
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
  current: "LÃ¤uft gerade",
  upcoming: "Kommt gleich",
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
const anchorClass = "scroll-mt-24";

function taskVariant(status: StudyTaskStatus) {
  if (status === "completed") {
    return "success" as const;
  }
  if (status === "in_progress") {
    return "info" as const;
  }
  return "secondary" as const;
}

function ProgressBar({ value, className, indicatorClassName, label }: { value: number; className?: string; indicatorClassName?: string; label?: string }) {
  const percentage = Math.min(100, Math.max(0, value));
  return (
    <div
      className={cn("h-2 overflow-hidden bg-muted", className)}
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={percentage}
    >
      <div className={cn("h-full bg-primary transition-[width] duration-slow", indicatorClassName)} style={{ width: `${percentage}%` }} />
    </div>
  );
}

/** Ringvariante desselben Werts wie der Balken, deshalb mit Label und aria-Werten. */
function ProgressRing({ value, label }: { value: number; label: string }) {
  const percentage = Math.min(100, Math.max(0, value));
  const style: CSSProperties = {
    background: `conic-gradient(currentColor ${percentage * 3.6}deg, color-mix(in srgb, currentColor 18%, transparent) 0deg)`,
  };
  return (
    <div
      className="relative flex h-32 w-32 shrink-0 items-center justify-center p-2 text-primary-foreground"
      style={style}
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={percentage}
    >
      <div className="flex h-full w-full flex-col items-center justify-center bg-primary text-primary-foreground">
        <span className="text-3xl font-extrabold tracking-tight">{percentage}%</span>
        <span className="text-[11px] font-bold uppercase tracking-[0.16em] opacity-75">geschafft</span>
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
      <Skeleton className="h-52 w-full" />
      <div className="grid gap-6 xl:grid-cols-2">
        <Skeleton className="h-56 w-full" />
        <Skeleton className="h-56 w-full" />
      </div>
      <Skeleton className="h-80 w-full" />
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
        <div className="space-y-6">
          <Skeleton className="h-80 w-full" />
          <Skeleton className="h-96 w-full" />
          <Skeleton className="h-72 w-full" />
        </div>
        <div className="space-y-6">
          <Skeleton className="h-64 w-full" />
          <Skeleton className="h-72 w-full" />
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
  const title = isCurrent ? "Du bist mittendrin." : lesson ? "Als NÃ¤chstes steht an" : "Dein Stundenplan ist frei.";

  return (
    <Card id="focus" className={cn(anchorClass, "relative overflow-hidden border-foreground bg-card")}>
      <CardHeader className="relative pb-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-[0.18em] text-primary">
            <Target className="h-4 w-4" aria-hidden="true" />
            Tagesfokus
          </div>
          {lesson ? <Badge variant={isCurrent ? "success" : "info"}>{isCurrent ? "Jetzt" : "Als NÃ¤chstes"}</Badge> : <Badge variant="secondary">Kein Termin</Badge>}
        </div>
        <CardTitle className="mt-3 text-2xl sm:text-3xl">{title}</CardTitle>
        <CardDescription className="text-base">Ein klarer nÃ¤chster Schritt macht den Unterschied.</CardDescription>
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
                  {formatTime(lesson.startTime)} â€“ {formatTime(lesson.endTime)} Uhr
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
          <EmptyState icon={GraduationCap} title="Keine Unterrichtsstunden" description="FÃ¼r heute sind keine Stunden eingetragen. Nutze die Zeit fÃ¼r deine offenen Aufgaben." />
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
    <header id="overview" className={cn(anchorClass, "flex flex-col justify-between gap-4 sm:flex-row sm:items-start")}>
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
              {error ?? "Der Willkommenssatz ist gerade nicht verfÃ¼gbar."}
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
                  <span className="text-border">Â·</span>
                  <span>fÃ¼r {formatDate(new Date(`${greeting.generatedOn}T12:00:00`))}</span>
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
      setActionError(error instanceof ApiError ? error.message : "Die Lernaktion konnte nicht ausgefÃ¼hrt werden.");
    } finally {
      setBusyTaskId(null);
    }
  }

  return (
    <Card id="study-tasks" className={anchorClass}>
      <CardHeader className="flex-row items-start justify-between space-y-0 pb-4">
        <div>
          <CardTitle className="flex items-center gap-2 text-lg">
            <Zap className="h-5 w-5 text-primary" aria-hidden="true" />
            Heute lernen
          </CardTitle>
          <CardDescription className="mt-1">Kleine FokusblÃ¶cke, groÃŸer Fortschritt.</CardDescription>
        </div>
        <Badge variant="info">{taskList.filter((task) => task.status !== "completed").length} Tasks</Badge>
      </CardHeader>
      <CardContent>
        {actionError ? <p className="mb-4 rounded-xl border border-destructive/20 bg-destructive/10 px-3 py-2 text-sm text-destructive" role="alert">{actionError}</p> : null}
        {taskList.length === 0 ? (
          <EmptyState icon={Timer} title="Keine Lernaufgaben" description="Deine nÃ¤chsten Lernschritte werden hier angezeigt, sobald sie geplant sind." />
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
                      <p className="mt-1 text-xs font-semibold text-muted-foreground">{task.subject} Â· {task.topic}</p>
                      {active && startedAt ? <p className="mt-1 flex items-center gap-1 text-xs font-bold text-primary"><Timer className="h-3.5 w-3.5" aria-hidden="true" />{elapsedLabel(startedAt, now)} Min.</p> : null}
                    </div>
                    <span className="shrink-0 text-xs font-bold text-muted-foreground">{formatDuration(task.durationMinutes)}</span>
                  </div>
                  <div className="mt-4 flex items-center gap-3">
                    <ProgressBar value={task.progress} label={`Fortschritt ${task.title}`} className="h-1.5 flex-1" indicatorClassName={completed ? "bg-foreground" : "bg-primary"} />
                    <span className="w-9 text-right text-xs font-bold text-muted-foreground">{task.progress}%</span>
                    {completed ? (
                      <span className="flex h-8 w-8 items-center justify-center bg-foreground text-background" aria-label="Erledigt">
                        <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
                      </span>
                    ) : active ? (
                      <div className="flex items-center gap-2">
                        <Button type="button" size="sm" variant="outline" onClick={() => void handleAction(task, "complete")} disabled={busy || demo} aria-label={`${task.title} abschlieÃŸen`}>
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
    <Card id="usage" className={anchorClass}>
      <CardHeader className="flex-row items-start justify-between space-y-0 pb-4">
        <div>
          <CardTitle className="flex items-center gap-2 text-lg">
            <ShieldCheck className="h-5 w-5 text-primary" aria-hidden="true" />
            Nutzung & Datenschutz
          </CardTitle>
          <CardDescription className="mt-1">Deine LernaktivitÃ¤t bleibt nachvollziehbar.</CardDescription>
        </div>
        <Badge variant="secondary">Privat</Badge>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-3 gap-2">
          <div className="rounded-xl bg-muted/50 p-3">
            <p className="text-lg font-extrabold">{data.summary.tasksCompleted}/{data.summary.tasksTotal}</p>
            <p className="mt-1 text-[11px] font-bold uppercase tracking-[0.1em] text-muted-foreground">FokusblÃ¶cke</p>
          </div>
          <div className="rounded-xl bg-muted/50 p-3">
            <p className="text-lg font-extrabold">{data.summary.lessonsCompleted}/{data.summary.lessonsTotal}</p>
            <p className="mt-1 text-[11px] font-bold uppercase tracking-[0.1em] text-muted-foreground">Stunden</p>
          </div>
          <div className="rounded-xl bg-muted/50 p-3">
            <p className="text-lg font-extrabold">{data.summary.homeworkPending}</p>
            <p className="mt-1 text-[11px] font-bold uppercase tracking-[0.1em] text-muted-foreground">Offen</p>
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

function AnswerBadge({ answer }: { answer: DocumentAnswer }) {
  if (answer.source === "none") {
    return <Badge variant="outline">Keine Treffer</Badge>;
  }
  if (answer.source === "ai") {
    return <Badge variant="secondary">{answer.model ? `KI-Antwort Â· ${answer.model}` : "KI-Antwort"}</Badge>;
  }
  return <Badge variant="info">Aus deinen Dokumenten</Badge>;
}

function AssistantCard() {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<DocumentAnswer | null>(null);
  const [isAsking, setIsAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleAsk(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const questionText = question.trim();
    if (!questionText || isAsking) {
      return;
    }
    setIsAsking(true);
    setError(null);
    try {
      setAnswer(await askDocuments({ question: questionText }));
    } catch (askError) {
      setAnswer(null);
      setError(askError instanceof ApiError ? askError.message : "Die Frage konnte nicht beantwortet werden.");
    } finally {
      setIsAsking(false);
    }
  }

  return (
    <Card id="assistant" className={cn(anchorClass, "relative overflow-hidden border-foreground bg-card")}>
      <CardHeader className="relative pb-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <span className="flex h-12 w-12 shrink-0 items-center justify-center bg-primary text-primary-foreground">
              <Sparkles className="h-6 w-6" aria-hidden="true" />
            </span>
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <CardTitle className="text-xl">Dein Lernassistent</CardTitle>
                {answer ? <AnswerBadge answer={answer} /> : <Badge variant="secondary">Antwortet aus deinen Dokumenten</Badge>}
              </div>
              <CardDescription className="mt-1">Stelle eine Frage zu deinen Unterlagen â€“ die Antwort kommt mit Quellenangabe.</CardDescription>
            </div>
          </div>
          <span className="hidden items-center gap-1.5 border border-border bg-background px-3 py-1.5 text-xs font-bold text-muted-foreground xl:inline-flex">
            <Sparkles className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
            Deine Daten, deine Worte
          </span>
        </div>
      </CardHeader>
      <CardContent className="relative space-y-4">
        <form onSubmit={handleAsk} className="flex gap-2" role="search">
          <label htmlFor="assistant-question" className="sr-only">Frage an deine Dokumente</label>
          <Input
            id="assistant-question"
            type="text"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="Zum Beispiel: Was sagt der Satz des Pythagoras?"
            disabled={isAsking}
            className="min-w-0 flex-1"
          />
          <Button type="submit" disabled={isAsking || !question.trim()}>
            {isAsking ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <Send aria-hidden="true" />}
            {isAsking ? "Fragt â€¦" : "Fragen"}
          </Button>
        </form>

        {error ? (
          <p className="rounded-2xl border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm font-medium text-destructive" role="alert">
            {error}
          </p>
        ) : null}

        {answer ? (
          <div className="space-y-3">
            <p className="rounded-2xl border border-border bg-background/60 px-4 py-3 text-sm leading-6">{answer.answer}</p>
            {answer.sources.length > 0 ? (
              <ul className="space-y-2">
                {answer.sources.map((source) => (
                  <li key={`${source.document_id}-${source.chunk_index}`} className="rounded-xl border border-border/70 bg-muted/30 px-3 py-2.5">
                    <p className="text-xs font-bold">
                      {source.filename}
                      <span className="ml-2 font-semibold text-muted-foreground">Abschnitt {source.chunk_index + 1}</span>
                    </p>
                    <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">{source.content}</p>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-muted-foreground">Es wurden keine passenden Stellen in deinen Dokumenten gefunden.</p>
            )}
          </div>
        ) : (
          <div className="flex min-h-28 flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-border bg-background/40 px-5 py-8 text-center" role="status">
            <Sparkles className="h-6 w-6 text-primary" aria-hidden="true" />
            <p className="text-sm font-bold">Noch keine Frage</p>
            <p className="max-w-md text-xs leading-5 text-muted-foreground">
              Indexiere zuerst einen Dokumentordner unter Einstellungen â€“ dann beantwortet der Assistent deine Fragen zu diesen Unterlagen.
            </p>
          </div>
        )}
        <p className="text-xs leading-5 text-muted-foreground">Antworten entstehen aus deinen eigenen Dokumenten; ohne verbundenen KI-Dienst wird aus den Treffern zitiert.</p>
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
        <CardContent className="relative flex flex-col gap-8 p-6 sm:p-8 md:p-10 lg:flex-row lg:items-center lg:justify-between">
          <div className="max-w-2xl">
            <div className="mb-4 flex flex-wrap items-center gap-2">
              <Badge className="border-primary-foreground/20 bg-primary-foreground/15 text-primary-foreground">Heute</Badge>
              <span className="text-xs font-bold uppercase tracking-[0.18em] text-primary-foreground/70">Dein Tagesfortschritt</span>
            </div>
            <h2 className="text-2xl font-extrabold tracking-tight sm:text-3xl">Du machst den Unterschied.</h2>
            <p className="mt-2 max-w-xl text-sm leading-6 text-primary-foreground/75">Halte deinen Rhythmus. Jeder abgeschlossene Fokusblock zÃ¤hlt.</p>
            <div className="mt-7 grid max-w-xl grid-cols-3 gap-3 sm:gap-6">
              <div>
                <p className="text-2xl font-extrabold">{data.summary.lessonsCompleted}/{data.summary.lessonsTotal}</p>
                <p className="mt-1 text-xs font-bold uppercase tracking-[0.12em] text-primary-foreground/65">Stunden</p>
              </div>
              <div>
                <p className="text-2xl font-extrabold">{data.summary.tasksCompleted}/{data.summary.tasksTotal}</p>
                <p className="mt-1 text-xs font-bold uppercase tracking-[0.12em] text-primary-foreground/65">FokusblÃ¶cke</p>
              </div>
              <div>
                <p className="text-2xl font-extrabold">{data.summary.homeworkPending}</p>
                <p className="mt-1 text-xs font-bold uppercase tracking-[0.12em] text-primary-foreground/65">Aufgaben</p>
              </div>
            </div>
            <ProgressBar value={dayProgress} label="Tagesfortschritt" className="mt-7 max-w-xl bg-primary-foreground/15" indicatorClassName="bg-primary-foreground" />
          </div>
          <ProgressRing value={dayProgress} label="Tagesfortschritt" />
        </CardContent>
      </Card>

      <div className="grid items-start gap-6 xl:grid-cols-2">
        <FocusLessonCard lesson={focusLesson} reference={reference} />
        <AssistantCard />
      </div>

      {/*
        Der Stundenplan steht Ã¼ber die volle Breite. Sieben Tage brauchen
        sieben gleich breite Spalten - in der frÃ¼heren Zweispalten-Schale blieben
        rund 120px je Tag, und die Stundenzeiten waren nicht mehr ausgerichtet.
      */}
      <ScheduleCard />

      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
        <div className="space-y-6">
          <StudyTasksCard tasks={data.studyTasks} />
          <ExamsSection reference={reference} />
          <HomeworkSection reference={reference} />
        </div>
        <div className="space-y-6">
          <CalendarSection reference={reference} />
          <UsageCard data={data} />
        </div>
      </div>

      <footer className="flex flex-col items-center justify-between gap-2 border-t border-border/70 pt-5 text-center text-xs text-muted-foreground sm:flex-row sm:text-left">
        <span>Ein ruhiger Ort fÃ¼r deinen nÃ¤chsten Schritt.</span>
        <span className="inline-flex items-center gap-1.5"><Sparkles className="h-3.5 w-3.5 text-primary" aria-hidden="true" />Lernraum</span>
      </footer>
    </div>
  );
}
