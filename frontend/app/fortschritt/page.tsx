"use client";

import { AppShell } from "@/components/app-shell";
import { GradesCard } from "@/components/dashboard/grades-card";
import { ProgressCard } from "@/components/dashboard/progress-card";
import { RewardsCard } from "@/components/dashboard/rewards-card";
import { useSession } from "@/lib/use-session";

/**
 * Lernfortschritt, Noten und Belohnung auf einer eigenen Seite.
 *
 * Auf dem Dashboard standen die drei unter dem Stundenplan und machten es zur
 * laengsten Seite der App. Sie beantworten "wie stehe ich?", nicht "was steht
 * heute an?" - und das fragt man seltener als einmal pro Blick auf den Tag.
 *
 * Kein `refreshKey` mehr: auf dem Dashboard hingen Fortschritt und Belohnung
 * an einem Zaehler, den die Start-/Stopp-Knoepfe der Lernaufgaben hochzaehlten.
 * Hier gibt es diese Knoepfe nicht; beim Oeffnen der Seite laden beide Karten
 * den aktuellen Stand ohnehin neu.
 */
export default function FortschrittPage() {
  const user = useSession();
  return (
    <AppShell user={user}>
      <div className="space-y-4">
        <header>
          <h1 className="text-[22px] font-semibold leading-8 tracking-tight">Fortschritt</h1>
          <p className="mt-0.5 max-w-2xl text-[13px] leading-5 text-muted-foreground">
            Lernzeit, Wiederholungen, Noten und Punkte - wie du stehst, nicht was heute ansteht.
          </p>
        </header>

        <div className="grid items-start gap-4 xl:grid-cols-2">
          <ProgressCard />
          <div className="space-y-4">
            <GradesCard />
            <RewardsCard />
          </div>
        </div>
      </div>
    </AppShell>
  );
}
