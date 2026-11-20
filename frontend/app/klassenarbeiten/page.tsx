"use client";

import { AppShell } from "@/components/app-shell";
import { NotebookView } from "@/components/notebook/notebook-view";
import { useSession } from "@/lib/use-session";

export default function KlassenarbeitenPage() {
  const user = useSession();
  return (
    <AppShell user={user}>
      <NotebookView />
    </AppShell>
  );
}
