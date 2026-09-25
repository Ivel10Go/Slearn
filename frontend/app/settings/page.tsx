"use client";

import { useEffect, useState } from "react";
import { getIntegrations } from "@/lib/api";
import { IntegrationRead } from "@/lib/types";

export default function SettingsPage() {
  const [integrations, setIntegrations] = useState<IntegrationRead[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getIntegrations()
      .then(setIntegrations)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  return (
    <div className="p-6">
      <h1 className="text-2xl font-bold mb-4">Einstellungen</h1>
      {error && <p className="text-red-600">{error}</p>}
      <ul>
        {integrations.map((i) => (
          <li key={i.id} className="border-b py-2">
            <span className="font-medium">{i.name}</span> – {i.kind} – {i.status}
          </li>
        ))}
      </ul>
    </div>
  );
}
