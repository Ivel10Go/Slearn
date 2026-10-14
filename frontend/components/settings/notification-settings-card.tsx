"use client";

import { useState } from "react";
import { BellRing, CheckCircle2, LoaderCircle, RefreshCw, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDateTime } from "@/lib/format";
import {
  aktiveKanaele,
  bezeichnung,
  beschreibeRuhezeit,
  pruefknopfMoeglich,
} from "@/lib/notifications";
import type {
  SlearnNotification,
  NotificationSettings,
  PushSubscriptionRecord,
  ReminderRunReport,
  VapidKey,
} from "@/lib/types";
import { cn } from "@/lib/utils";
import { t } from "@/lib/i18n";

/*
 * Die Erinnerungseinstellungen.
 *
 * Die Karte bedient sich **eines** Hooks, damit nicht drei Komponenten dieselbe
 * Abfrage stellen und drei Zustaende zeigen koennen. Jeder Schalter schreibt
 * sofort - ein "Speichern"-Knopf waere eine zweite Wahrheit ueber den Zustand.
 *
 * Drei Dinge, die hier bewusst sichtbar gemacht werden und nicht versteckt:
 *
 *  * **Der Hauptschalter allein tut nichts.** Er schaltet die Kanaele stumm,
 *    die Kanaele entscheiden, ob ueberhaupt etwas entsteht. Das steht als Text
 *    daneben, weil ein Schalter ohne Wirkung wie ein Fehler aussieht.
 *  * **Ruhezeiten laufen ueber Mitternacht.** `22:00 bis 07:00` steht als
 *    solches da, und eine Erinnerung, die in die Ruhezeit fielt, kommt danach -
 *    nicht mitten in der Nacht.
 *  * **Web-Push ist optional.** Fehlt die Berechtigung oder das Schluesselpaar
 *    auf dem Server, steht das hier als Klartext. Die Erinnerungen laufen dann
 *    ueber die geoeffnete Seite, und das ist der normale Weg.
 */

interface NotificationSettingsCardProps {
  settings: NotificationSettings | null;
  vapid: VapidKey | null;
  subscriptions: PushSubscriptionRecord[];
  permission: NotificationPermission | "unsupported";
  pushMoeglich: boolean;
  status: "loading" | "ready" | "error";
  error: string | null;
  actionError: string | null;
  pending: string | null;
  bericht: ReminderRunReport | null;
  /** Die in dieser Sitzung angezeigten Erinnerungen. */
  zuletzt: SlearnNotification[];
  onSpeichern: (changes: {
    enabled?: boolean;
    channels?: Record<string, boolean>;
    reminder_time?: string;
    quiet_hours?: { enabled: boolean; start: string; end: string };
  }) => Promise<boolean>;
  onErlaubnis: () => Promise<boolean>;
  onPushAnmelden: () => Promise<boolean>;
  onPushAbmelden: () => Promise<boolean>;
  onPruefen: () => Promise<ReminderRunReport | null>;
  onQuittieren: (ids: number[]) => Promise<void>;
}

function Schalter({ an, label, disabled, onUmschalten }: { an: boolean; label: string; disabled?: boolean; onUmschalten: () => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={an}
      aria-label={label}
      disabled={disabled}
      onClick={onUmschalten}
      className={cn(
        "relative h-6 w-11 shrink-0 rounded-full transition-colors duration-fast disabled:opacity-50 active:opacity-80",
        an ? "bg-primary" : "bg-muted",
      )}
    >
      <span
        className={cn(
          "absolute top-0.5 h-5 w-5 rounded-full border border-border bg-card transition-[left] duration-fast",
          an ? "left-[22px]" : "left-0.5",
        )}
      />
    </button>
  );
}

export function NotificationSettingsCard({
  settings,
  vapid,
  subscriptions,
  permission,
  pushMoeglich,
  status,
  error,
  actionError,
  pending,
  bericht,
  zuletzt,
  onSpeichern,
  onErlaubnis,
  onPushAnmelden,
  onPushAbmelden,
  onPruefen,
  onQuittieren,
}: NotificationSettingsCardProps) {
  const [lokalFehler, setLokalFehler] = useState<string | null>(null);

  if (status === "loading" && !settings) {
    return (
      <Card className="border-border/80 bg-card/90">
        <CardHeader>
          <CardTitle>{t("Erinnerungen")}</CardTitle>
          <CardDescription className="mt-1">{t("Einstellungen werden geladen.")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-20 w-full" />
        </CardContent>
      </Card>
    );
  }

  if (!settings) {
    return (
      <Card className="border-border/80 bg-card/90">
        <CardHeader>
          <CardTitle>{t("Erinnerungen")}</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="rounded-md border border-destructive/20 bg-destructive/10 px-3.5 py-3 text-sm font-medium text-destructive" role="alert">
            {error ?? t("Die Einstellungen konnten nicht geladen werden.")}
          </p>
        </CardContent>
      </Card>
    );
  }

  const beschaeftigt = pending !== null;
  const kanaele = settings.channels_available;
  const aktive = aktiveKanaele(settings);
  const pushEingeschaltet = settings.enabled && Boolean(vapid?.configured) && permission === "granted";

  async function speichern(changes: Parameters<NotificationSettingsCardProps["onSpeichern"]>[0]) {
    setLokalFehler(null);
    const gespeichert = await onSpeichern(changes);
    if (!gespeichert) {
      // Kein Zufallstext: der Fehler des Hooks steht eine Zeile darunter.
      setLokalFehler(t("Bitte prüfe die Eingaben und versuche es noch einmal."));
    }
  }

  return (
    <Card className="border-border/80 bg-card/90">
      <CardHeader>
        <CardTitle>{t("Erinnerungen")}</CardTitle>
        <CardDescription className="mt-1">
          {t("Fällige Lernaufgaben und Prüfungen, einmal am Tag zur gewünschten Uhrzeit.")}
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-5">
        <div className="flex items-start justify-between gap-3 border-b border-border/70 pb-4">
          <div className="min-w-0">
            <p className="text-sm font-semibold">{t("Hauptschalter")}</p>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              {settings.enabled
                ? aktive.length > 0
                  ? t("Erinnerungen für {liste}.", { liste: aktive.join(` ${t("und")} `) })
                  : t("Der Hauptschalter ist an, aber es ist kein Kanal freigegeben - es gibt also keine Erinnerungen.")
                : t("Aus. Auch mit freigegebenen Kanälen kommt keine Erinnerung.")}
            </p>
          </div>
          <Schalter
            an={settings.enabled}
            label={t("Erinnerungen ein- oder ausschalten")}
            disabled={beschaeftigt}
            onUmschalten={() => void speichern({ enabled: !settings.enabled })}
          />
        </div>

        <fieldset className="space-y-2" disabled={beschaeftigt || !settings.enabled}>
          <legend className="text-sm font-semibold">{t("Kanäle")}</legend>
          {kanaele.length === 0 ? (
            <p className="text-xs text-muted-foreground">{t("Der Server kennt keine Kanäle.")}</p>
          ) : (
            kanaele.map((kanal) => (
              <label key={kanal} className="flex items-center justify-between gap-3 border border-border/70 bg-muted/40 px-3.5 py-3 text-sm">
                <span className="font-semibold">{settings.channel_labels[kanal] ?? kanal}</span>
                <input
                  type="checkbox"
                  className="h-4 w-4 accent-foreground"
                  checked={settings.channels[kanal] ?? false}
                  onChange={(event) =>
                    void speichern({ channels: { [kanal]: event.target.checked } })
                  }
                />
              </label>
            ))
          )}
          <p className="text-xs leading-5 text-muted-foreground">
            {t("Standardmäßig ist kein Kanal freigegeben: Eine Erinnerung kommt erst, wenn du sie hier einschaltest.")}
          </p>
        </fieldset>

        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-2">
            <label className="text-sm font-semibold" htmlFor="erinnerung-uhrzeit">
              {t("Uhrzeit")}
            </label>
            <input
              id="erinnerung-uhrzeit"
              type="time"
              className="flex h-11 w-full border border-input bg-card px-3.5 py-2 text-sm text-foreground focus-visible:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/20"
              value={settings.reminder_time}
              disabled={beschaeftigt || !settings.enabled}
              onChange={(event) => void speichern({ reminder_time: event.target.value })}
            />
            <p className="text-xs leading-5 text-muted-foreground">{t("Rechnet in {zone}.", { zone: settings.timezone })}</p>
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between gap-3">
              <span className="text-sm font-semibold">{t("Ruhezeit")}</span>
              <Schalter
                an={settings.quiet_hours.enabled}
                label={t("Ruhezeit ein- oder ausschalten")}
                disabled={beschaeftigt || !settings.enabled}
                onUmschalten={() =>
                  void speichern({
                    quiet_hours: {
                      enabled: !settings.quiet_hours.enabled,
                      start: settings.quiet_hours.start,
                      end: settings.quiet_hours.end,
                    },
                  })
                }
              />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <label className="text-xs font-semibold text-muted-foreground" htmlFor="ruhe-von">
                {t("von")}
                <input
                  id="ruhe-von"
                  type="time"
                  className="mt-1 flex h-11 w-full border border-input bg-card px-3.5 py-2 text-sm text-foreground focus-visible:border-primary focus-visible:outline-none"
                  value={settings.quiet_hours.start}
                  disabled={beschaeftigt || !settings.quiet_hours.enabled}
                  onChange={(event) =>
                    void speichern({
                      quiet_hours: {
                        enabled: settings.quiet_hours.enabled,
                        start: event.target.value,
                        end: settings.quiet_hours.end,
                      },
                    })
                  }
                />
              </label>
              <label className="text-xs font-semibold text-muted-foreground" htmlFor="ruhe-bis">
                {t("bis")}
                <input
                  id="ruhe-bis"
                  type="time"
                  className="mt-1 flex h-11 w-full border border-input bg-card px-3.5 py-2 text-sm text-foreground focus-visible:border-primary focus-visible:outline-none"
                  value={settings.quiet_hours.end}
                  disabled={beschaeftigt || !settings.quiet_hours.enabled}
                  onChange={(event) =>
                    void speichern({
                      quiet_hours: {
                        enabled: settings.quiet_hours.enabled,
                        start: settings.quiet_hours.start,
                        end: event.target.value,
                      },
                    })
                  }
                />
              </label>
            </div>
            <p className="text-xs leading-5 text-muted-foreground">
              {t("Aktuell {ruhezeit}. Eine Erinnerung, die in die Ruhezeit fällt, kommt direkt danach.", { ruhezeit: beschreibeRuhezeit(settings.quiet_hours) })}
            </p>
          </div>
        </div>

        <div className="space-y-3 border-t border-border/70 pt-4">
          <p className="text-sm font-semibold">{t("Web-Push")}</p>
          <p className="text-xs leading-5 text-muted-foreground">
            {permission === "unsupported"
              ? t("Dieser Browser kennt keine Benachrichtigungen. Erinnerungen erscheinen dann nur, solange die Seite geöffnet ist.")
              : !vapid?.configured
                ? t("Auf diesem Server ist kein Push-Schlüssel eingerichtet. Die Erinnerungen kommen über die geöffnete Seite - dafür braucht es keinen Push-Dienst.")
                : permission !== "granted"
                  ? t("Erlaubnis fehlt. Ohne sie kann der Browser keine Benachrichtigung anzeigen.")
                  : t("Der Browser wird benachrichtigt, auch wenn die Seite nicht geöffnet ist.")}
          </p>

          <div className="flex flex-wrap items-center gap-2">
            {permission !== "granted" && permission !== "unsupported" ? (
              <Button type="button" size="sm" variant="outline" disabled={!pushMoeglich || beschaeftigt} onClick={() => void onErlaubnis()}>
                <BellRing aria-hidden="true" />
                {t("Erlaubnis erbitten")}
              </Button>
            ) : null}
            {permission === "granted" && vapid?.configured ? (
              subscriptions.length === 0 ? (
                <Button type="button" size="sm" disabled={!pushMoeglich || beschaeftigt} onClick={() => void onPushAnmelden()}>
                  {pending === "push" ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <BellRing aria-hidden="true" />}
                  {t("Dieses Gerät anmelden")}
                </Button>
              ) : (
                <Button type="button" size="sm" variant="ghost" disabled={beschaeftigt} onClick={() => void onPushAbmelden()}>
                  <Trash2 aria-hidden="true" />
                  {t("Gerät abmelden")}
                </Button>
              )
            ) : null}
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={!pruefknopfMoeglich(settings) || beschaeftigt}
              onClick={() => void onPruefen()}
            >
              {pending === "pruefen" ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <RefreshCw aria-hidden="true" />}
              {t("Jetzt prüfen")}
            </Button>
          </div>

          {subscriptions.length > 0 ? (
            <ul className="space-y-2">
              {subscriptions.map((eintrag) => (
                <li key={eintrag.id} className="flex flex-wrap items-center gap-2 border border-border/70 bg-muted/40 px-3.5 py-2.5 text-xs">
                  <Badge variant="outline">{eintrag.endpoint_hint}</Badge>
                  <span className="text-muted-foreground">{t("angemeldet am {datum}", { datum: formatDateTime(eintrag.created_at) })}</span>
                  {eintrag.last_success_at ? (
                    <span className="text-muted-foreground">{t("zuletzt zugestellt {datum}", { datum: formatDateTime(eintrag.last_success_at) })}</span>
                  ) : null}
                  {eintrag.failures > 0 ? <Badge variant="warning">{t("{n} Fehlversuche", { n: eintrag.failures })}</Badge> : null}
                </li>
              ))}
            </ul>
          ) : null}

          {subscriptions.some((eintrag) => eintrag.last_error) ? (
            <p className="text-xs font-semibold text-muted-foreground">
              {t("Letzter Fehler:")}{" "}{subscriptions.find((eintrag) => eintrag.last_error)?.last_error}
            </p>
          ) : null}
        </div>

        {/*
          Die Live-Region steht immer im DOM, ihr Inhalt wechselt. Ein
          `role="status"`, das erst mit dem Text eingefuegt wird, wird von
          Screenreadern nicht angesagt.
        */}
        <div role="status" aria-live="polite" className="space-y-2">
          {bericht ? (
            <p className="flex items-start gap-2 border border-border px-3.5 py-2.5 text-xs font-medium">
              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
              <span>
                {t("{angelegt} Erinnerung(en) angelegt, {zugestellt} davon an einen Push-Dienst gegeben.", { angelegt: bericht.created, zugestellt: bericht.delivered })}
                {bericht.scheduler_running
                  ? ` ${t("Der planmäßige Versand läuft alle {n} Minuten.", { n: Math.round(bericht.interval_seconds / 60) })}`
                  : t(" Der planmäßige Versand ist auf diesem Server aus.")}
              </span>
            </p>
          ) : null}
          {pushEingeschaltet ? (
            <p className="text-xs text-muted-foreground">{t("Push ist für dieses Gerät eingeschaltet.")}</p>
          ) : null}
        </div>

        {zuletzt.length > 0 ? (
          <div className="space-y-2 border-t border-border/70 pt-4">
            <p className="text-sm font-semibold">{t("Zuletzt angezeigt")}</p>
            <ul className="space-y-2">
              {zuletzt.map((erinnerung) => (
                <li key={erinnerung.id} className="border border-border/70 bg-muted/40 px-3.5 py-2.5">
                  <p className="text-sm font-semibold">{erinnerung.title}</p>
                  <p className="mt-0.5 text-xs text-muted-foreground">{erinnerung.body}</p>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <Badge variant="outline">{bezeichnung(erinnerung.kind)}</Badge>
                    <Badge variant={erinnerung.push_status === "sent" ? "success" : "secondary"}>
                      {erinnerung.push_status === "sent" ? t("per Push zugestellt") : t("über diese Seite")}
                    </Badge>
                    <span className="text-xs text-muted-foreground">{formatDateTime(erinnerung.created_at)}</span>
                    <Button type="button" size="sm" variant="ghost" onClick={() => void onQuittieren([erinnerung.id])}>
                      {t("Aus der Liste nehmen")}
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {lokalFehler || actionError ? (
          <p className="border border-destructive/20 bg-destructive/10 px-3.5 py-3 text-sm font-medium text-destructive" role="alert">
            {actionError ?? lokalFehler}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
