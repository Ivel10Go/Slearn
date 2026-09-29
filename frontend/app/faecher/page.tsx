"use client";

import { AppShell } from "@/components/app-shell";
import { SubjectsView } from "@/components/manage/subjects-view";
import { useSession } from "@/lib/use-session";

export default function SubjectsPage() {
  return (
    <AppShell user={useSession()}>
      <SubjectsView />
    </AppShell>
  );
}
