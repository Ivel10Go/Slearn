"use client";

import { useState } from "react";
import { AppShell } from "@/components/app-shell";
import { StudyPlansCard } from "@/components/manage/study-plans-card";
import { StudySeriesCard } from "@/components/manage/study-series-card";
import { StudyTasksCard } from "@/components/manage/study-tasks-card";
import { WeekPlannerCard } from "@/components/manage/week-planner-card";
import { createStudyPlan, deleteStudyPlan, getStudyPlans, renameStudyPlan } from "@/lib/api";
import { useCollection } from "@/lib/use-collection";
import { useSession } from "@/lib/use-session";
import type { StudyPlan } from "@/lib/types";
import { t } from "@/lib/i18n";

/**
 * Verwaltung von Lernplänen, Serien und Aufgaben.
 *
 * Die Planliste liegt hier oben, nicht in der Plankarte: Serien und Aufgaben
 * brauchen dieselbe Liste fuer ihre Auswahlfelder. Laede jede Karte ihre
 * eigene, waere das eine zweite Anfrage - und nach dem Anlegen eines Plans
 * wuessten die beiden anderen Karten den neuen Plan erst nach einem Neuladen.
 *
 * `planNeu` ist ein Zaehler, kein Zustand: er steigt, wenn ein Plan
 * geloescht wurde, und die beiden abhaengigen Karten laden daraufhin neu. Ein
 * Ereignisbus waere hier falsch - es gaebe einen zweiten Weg, den Zustand zu
 * aendern, und niemand koerte beim Lesen sehen, wer ihn ausloest.
 */
function LernenBereich() {
  const plaene = useCollection<StudyPlan, { title: string; description?: string | null }, { title: string }>(() => getStudyPlans(), {
    create: createStudyPlan,
    update: (planId, changes) => renameStudyPlan(planId, changes.title),
    remove: deleteStudyPlan,
  });
  const [planNeu, setPlanNeu] = useState(0);

  async function planEntfernen(planId: number) {
    // Aufgaben und Serien des Plans sind mitgefallen. Beide Karten zeigen
    // sie aus eigener Quelle und duerfen ihre Waisen nicht behalten.
    const entfernt = await plaene.remove(planId);
    if (entfernt) {
      setPlanNeu((zaehler) => zaehler + 1);
    }
  }

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-[26px] font-semibold leading-9 tracking-tight">{t("Lernen")}</h1>
        <p className="sr-only">
          {t("Der Wochenplan verteilt alles Offene auf deine freie Zeit. Lernpläne sammeln Aufgaben zu einem Thema, Serien erzeugen daraus wiederkehrende Aufgaben, und eine Prüfung kann ihre Themen direkt in Aufgaben umsetzen.")}
        </p>
      </header>

      {/* Der Planer legt Aufgaben an und setzt Termine: danach laden Pläne
          (neuer Plan "Wochenplan") und Aufgaben über denselben Zähler neu. */}
      <WeekPlannerCard
        onApplied={() => {
          void plaene.reload();
          setPlanNeu((zaehler) => zaehler + 1);
        }}
      />

      <StudyTasksCard plans={plaene.items} onChanged={() => void plaene.reload()} refreshKey={planNeu} />

      {/* Pläne und Serien braucht man selten: der Wochenplan legt beides selbst an.
          Zugeklappt bleiben sie erreichbar, ohne den Alltag zu füllen. */}
      <details className="group">
        <summary className="cursor-pointer select-none rounded-full px-3 py-1.5 text-[15px] text-muted-foreground hover:text-foreground">
          {t("Erweitert: Lernpläne und wiederkehrende Aufgaben")}
        </summary>
        <div className="mt-3 grid items-start gap-4 xl:grid-cols-2">
          <StudyPlansCard
            plans={plaene.items}
            status={plaene.status}
            error={plaene.error}
            actionError={plaene.actionError}
            pending={plaene.pending}
            onReload={() => void plaene.reload()}
            onCreate={plaene.create}
            onRename={(planId, titel) => void plaene.update(planId, { title: titel })}
            onDelete={(planId) => void planEntfernen(planId)}
          />
          <StudySeriesCard plans={plaene.items} onChanged={() => void plaene.reload()} refreshKey={planNeu} />
        </div>
      </details>
    </div>
  );
}

export default function LernenPage() {
  const user = useSession();
  return (
    <AppShell user={user}>
      <LernenBereich />
    </AppShell>
  );
}
