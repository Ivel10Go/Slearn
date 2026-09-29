"use client";

import { AppShell } from "@/components/app-shell";
import { DocumentsView } from "@/components/manage/documents-view";
import { useSession } from "@/lib/use-session";

export default function DocumentsPage() {
  return (
    <AppShell user={useSession()}>
      <DocumentsView />
    </AppShell>
  );
}
