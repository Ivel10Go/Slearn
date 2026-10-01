"use client";

import { useState } from "react";
import { AppShell } from "@/components/app-shell";
import { StudyPlansCard } from "@/components/manage/study-plans-card";
import { StudySeriesCard } from "@/components/manage/study-series-card";
import { StudyTasksCard } from "@/components/manage/study-tasks-card";
import { createStudyPlan, deleteStudyPlan, getStudyPlans, renameStudyPlan } from "@/lib/api";
import { useCollection } from "@/lib/use-collection";
import { useSession } from "@/lib/use-session";
import type { StudyPlan } from "@/lib/types";

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
    <div className="space-y-6">
      <header>
        <h1 className="text-[22px] font-semibold leading-8 tracking-tight">Lernen</h1>
        <p className="mt-1 max-w-2xl text-[13px] leading-5 text-muted-foreground">
          Lernpläne sammeln Aufgaben zu einem Thema. Serien erzeugen daraus wiederkehrende Aufgaben, und eine Prüfung kann ihre Themen
          direkt in Aufgaben umsetzen.
        </p>
      </header>

      <div className="grid items-start gap-6 xl:grid-cols-2">
        <div className="space-y-6">
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

        <div className="space-y-6">
          <StudyTasksCard plans={plaene.items} onChanged={() => void plaene.reload()} refreshKey={planNeu} />
        </div>
      </div>
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
