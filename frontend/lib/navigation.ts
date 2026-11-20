import { t } from "./i18n";

/**
 * Bereiche mit Unterreitern: ein Punkt in der Navigation, oben auf der Seite die Reiter.
 * So bleibt die Navigation bei fuenf Zielen, ohne dass eine Seite verschwindet.
 */
export const NAV_SECTIONS: { href: string; tabs: { href: string; label: string }[] }[] = [
  {
    href: "/lernen",
    tabs: [
      { href: "/lernen", label: t("Wochenplan") },
      { href: "/klassenarbeiten", label: t("Klassenarbeiten") },
      { href: "/fortschritt", label: t("Fortschritt") },
    ],
  },
  {
    href: "/dokumente",
    tabs: [
      { href: "/dokumente", label: t("Dokumente") },
      { href: "/faecher", label: t("Fächer") },
    ],
  },
  {
    href: "/settings",
    tabs: [
      { href: "/settings", label: t("Einstellungen") },
      { href: "/ki", label: t("KI & Modelle") },
    ],
  },
];

/** Der Bereich, zu dem ein Pfad gehoert - oder `undefined` fuer Seiten ohne Reiter. */
export function sectionOf(pathname: string) {
  return NAV_SECTIONS.find((section) => section.tabs.some((tab) => tab.href === pathname));
}

/** Aktiv ist ein Navigationspunkt auf seiner eigenen Seite und auf jedem Reiter seines Bereichs. */
export function isSectionActive(pathname: string, href: string) {
  return pathname === href || sectionOf(pathname)?.href === href;
}

export function normalizeHash(hash: string) {
  return hash.startsWith("#") ? hash.slice(1) : hash;
}

export function isNavigationHrefActive(pathname: string, href: string, currentHash: string) {
  const [hrefPath, hrefHash = "overview"] = href.split("#");
  if (pathname !== hrefPath) {
    return false;
  }
  return normalizeHash(currentHash || "#overview") === hrefHash;
}
