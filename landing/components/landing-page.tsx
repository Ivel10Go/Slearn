import type { ReactNode } from "react";
import { AppPreview } from "@/components/app-preview";
import { AssistantPreview } from "@/components/assistant-preview";
import { Faultier } from "@/components/faultier";
import { LanguageHint } from "@/components/language-hint";
import { WaitlistForm } from "@/components/waitlist-form";
import { CONTENT, homeHref, otherLocale, type LandingContent } from "@/lib/content";
import { cn } from "@/lib/utils";

/* Die ganze Seite, je Sprache mit eigenem Textobjekt (`lib/content.ts`). */
export function LandingPage({ content }: { content: LandingContent }) {
  const andere = otherLocale(content.locale);
  return (
    <div className="min-h-screen">
      <Kopf content={content} />
      <LanguageHint target={andere} targetHref={homeHref(andere)} texts={CONTENT[andere].languageHint} />
      <main>
        <Hero content={content} />
        <Funktionen content={content} />
        <Assistent content={content} />
        <Datenschutz content={content} />
        <Varianten content={content} />
        <Warteliste content={content} />
        <Fragen content={content} />
      </main>
      <Fuss content={content} />
    </div>
  );
}

function Kopf({ content }: { content: LandingContent }) {
  const { header, ids } = content;
  const andere = otherLocale(content.locale);
  return (
    <header className="sticky top-0 z-20 border-b border-border bg-background/90 backdrop-blur supports-[backdrop-filter]:bg-background/75">
      <div className="mx-auto flex h-14 max-w-6xl items-center justify-between gap-4 px-4 sm:px-6">
        <a href="#" aria-label={header.homeLabel} className="flex items-center gap-2 text-[15px] font-semibold tracking-tight">
          <Faultier groesse={24} still />
          Slearn
        </a>
        <nav aria-label={header.navLabel} className="hidden items-center gap-1 md:flex">
          {header.nav.map((punkt) => (
            <a
              key={punkt.section}
              href={`#${ids[punkt.section]}`}
              className="rounded-md px-2.5 py-1.5 text-[13px] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              {punkt.label}
            </a>
          ))}
        </nav>
        <div className="flex items-center gap-1.5">
          <a
            href={homeHref(andere)}
            hrefLang={andere}
            lang={andere}
            aria-label={header.otherLanguage.label}
            title={header.otherLanguage.label}
            data-sprache={andere}
            className="inline-flex h-8 items-center rounded-md border border-border px-2.5 text-[12px] font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            {header.otherLanguage.short}
          </a>
          <a
            href={`#${ids.waitlist}`}
            className="inline-flex h-8 items-center rounded-md border border-primary bg-primary px-3 text-[13px] font-medium text-primary-foreground transition-colors hover:bg-primary/88"
          >
            {header.waitlist}
          </a>
        </div>
      </div>
    </header>
  );
}

function Hero({ content }: { content: LandingContent }) {
  const { hero } = content;
  return (
    <section className="border-b border-border">
      <div className="mx-auto grid max-w-6xl gap-12 px-4 pt-14 pb-16 sm:px-6 lg:grid-cols-[1fr_auto] lg:items-end lg:pt-20">
        <div className="max-w-2xl">
          <p className="inline-flex items-center gap-2 rounded-md border border-border px-2 py-1 text-[12px] text-muted-foreground">
            <span className="size-1.5 rounded-full bg-foreground" aria-hidden />
            {hero.badge}
          </p>
          <h1 className="mt-5 text-[40px] leading-[1.05] font-semibold tracking-[-0.03em] text-balance sm:text-[56px]">
            {hero.title}
          </h1>
          <p className="mt-5 max-w-xl text-[16px] leading-relaxed text-muted-foreground text-pretty sm:text-[17px]">{hero.lead}</p>
          <WaitlistForm kompakt texts={content.form} className="mt-8 max-w-lg" />
        </div>
        <div className="hidden justify-self-end lg:block">
          <Faultier koerper groesse={150} folgen beschriftung={hero.mascot} />
        </div>
      </div>
      <div className="mx-auto max-w-6xl px-4 pb-16 sm:px-6">
        <AppPreview texts={content.appPreview} />
      </div>
    </section>
  );
}

function Abschnitt({ id, titel, text, children, className }: { id: string; titel: string; text: string; children: ReactNode; className?: string }) {
  return (
    <section id={id} className={cn("border-b border-border", className)}>
      <div className="mx-auto max-w-6xl px-4 py-16 sm:px-6 sm:py-20">
        <div className="max-w-2xl">
          <h2 className="text-[28px] leading-tight font-semibold tracking-[-0.02em] text-balance sm:text-[34px]">{titel}</h2>
          <p className="mt-3 text-[15px] leading-relaxed text-muted-foreground text-pretty">{text}</p>
        </div>
        <div className="mt-10">{children}</div>
      </div>
    </section>
  );
}

function Funktionen({ content }: { content: LandingContent }) {
  const { features } = content;
  return (
    <Abschnitt id={content.ids.features} titel={features.title} text={features.text}>
      <div className="grid gap-px overflow-hidden rounded-xl border border-border bg-border sm:grid-cols-2 lg:grid-cols-3">
        {features.items.map((funktion, index) => (
          <article key={funktion.title} className="flex flex-col bg-background p-5 sm:p-6">
            <p className="font-mono text-[11px] text-muted-foreground tabular-nums">{String(index + 1).padStart(2, "0")}</p>
            <h3 className="mt-3 text-[16px] font-semibold tracking-tight">{funktion.title}</h3>
            <p className="mt-2 text-[13.5px] leading-relaxed text-muted-foreground">{funktion.text}</p>
            <ul className="mt-4 space-y-1.5 border-t border-border pt-4">
              {funktion.points.map((punkt) => (
                <li key={punkt} className="flex gap-2 text-[13px]">
                  <span className="mt-[8px] h-px w-2.5 shrink-0 bg-foreground" aria-hidden />
                  {punkt}
                </li>
              ))}
            </ul>
          </article>
        ))}
      </div>
    </Abschnitt>
  );
}

function Assistent({ content }: { content: LandingContent }) {
  const { assistant } = content;
  return (
    <Abschnitt id={content.ids.assistant} titel={assistant.title} text={assistant.text}>
      <div className="grid gap-10 lg:grid-cols-[1fr_1.1fr] lg:items-start">
        <div className="space-y-8">
          <ul className="space-y-5">
            {assistant.points.map((punkt) => (
              <li key={punkt.title} className="border-l border-foreground pl-4">
                <p className="text-[15px] font-medium">{punkt.title}</p>
                <p className="mt-1 text-[13.5px] leading-relaxed text-muted-foreground">{punkt.text}</p>
              </li>
            ))}
          </ul>
          <div className="rounded-xl border border-border p-5">
            <p className="text-[13px] font-medium">{assistant.learnsTitle}</p>
            <ol className="mt-4 grid gap-4 sm:grid-cols-3">
              {assistant.levels.map((ebene, index) => (
                <li key={ebene.title}>
                  <p className="font-mono text-[11px] text-muted-foreground">{assistant.level.replace("{n}", String(index + 1))}</p>
                  <p className="mt-1 text-[13.5px] font-medium">{ebene.title}</p>
                  <p className="mt-1 text-[12.5px] leading-relaxed text-muted-foreground">{ebene.text}</p>
                </li>
              ))}
            </ol>
          </div>
        </div>
        <AssistantPreview texts={content.assistantPreview} />
      </div>
    </Abschnitt>
  );
}

function Datenschutz({ content }: { content: LandingContent }) {
  const { privacy } = content;
  return (
    <Abschnitt id={content.ids.privacy} titel={privacy.title} text={privacy.text} className="bg-muted/40">
      <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
        {privacy.items.map((punkt) => (
          <div key={punkt.title} className="border-t border-foreground pt-4">
            <h3 className="text-[15px] font-semibold tracking-tight">{punkt.title}</h3>
            <p className="mt-2 text-[13.5px] leading-relaxed text-muted-foreground">{punkt.text}</p>
          </div>
        ))}
      </div>
    </Abschnitt>
  );
}

function Varianten({ content }: { content: LandingContent }) {
  const { editions } = content;
  return (
    <Abschnitt id={content.ids.editions} titel={editions.title} text={editions.text}>
      <div className="overflow-x-auto rounded-xl border border-border">
        <table className="w-full min-w-[560px] border-collapse text-left text-[13.5px]">
          <thead>
            <tr className="border-b border-border">
              <th scope="col" className="w-1/4 px-4 py-3 font-normal text-muted-foreground" />
              <th scope="col" className="px-4 py-3 font-semibold">
                {editions.desktop}
              </th>
              <th scope="col" className="border-l border-border px-4 py-3 font-semibold">
                {editions.server}
              </th>
            </tr>
          </thead>
          <tbody>
            {editions.rows.map(([zeile, desktop, server]) => (
              <tr key={zeile} className="border-b border-border last:border-b-0">
                <th scope="row" className="px-4 py-3 font-normal text-muted-foreground">
                  {zeile}
                </th>
                <td className="px-4 py-3">{desktop}</td>
                <td className="border-l border-border px-4 py-3">{server}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Abschnitt>
  );
}

function Warteliste({ content }: { content: LandingContent }) {
  const { waitlist } = content;
  return (
    <section id={content.ids.waitlist} className="border-b border-border">
      <div className="mx-auto grid max-w-6xl gap-10 px-4 py-16 sm:px-6 sm:py-20 lg:grid-cols-[1fr_440px] lg:gap-16">
        <div className="max-w-xl">
          <h2 className="text-[28px] leading-tight font-semibold tracking-[-0.02em] text-balance sm:text-[34px]">{waitlist.title}</h2>
          <p className="mt-3 text-[15px] leading-relaxed text-muted-foreground">{waitlist.text}</p>
          <dl className="mt-8 grid grid-cols-3 gap-4 border-t border-border pt-6">
            {waitlist.facts.map((fakt) => (
              <div key={fakt.label}>
                <dt className="text-[12px] text-muted-foreground">{fakt.label}</dt>
                <dd className="mt-1 text-[15px] font-medium">{fakt.value}</dd>
              </div>
            ))}
          </dl>
          <div className="mt-10 hidden items-center gap-3 lg:flex">
            <Faultier zustand="zuhoeren" groesse={40} folgen />
            <p className="text-[13px] text-muted-foreground">{waitlist.mascotNote}</p>
          </div>
        </div>
        <div className="rounded-xl border border-border bg-card p-5 sm:p-6">
          <WaitlistForm texts={content.form} />
        </div>
      </div>
    </section>
  );
}

function Fragen({ content }: { content: LandingContent }) {
  const { faq } = content;
  return (
    <Abschnitt id={content.ids.faq} titel={faq.title} text={faq.text}>
      <div className="divide-y divide-border rounded-xl border border-border">
        {faq.items.map((eintrag) => (
          <details key={eintrag.question} className="group px-5">
            <summary className="flex cursor-pointer items-center justify-between gap-4 py-4 text-[14.5px] font-medium">
              {eintrag.question}
              <svg viewBox="0 0 12 12" className="faq-plus size-3 shrink-0 transition-transform" aria-hidden>
                <path d="M6 1v10M1 6h10" stroke="currentColor" strokeWidth="1.4" />
              </svg>
            </summary>
            <p className="max-w-3xl pb-5 text-[13.5px] leading-relaxed text-muted-foreground">{eintrag.answer}</p>
          </details>
        ))}
      </div>
    </Abschnitt>
  );
}

function Fuss({ content }: { content: LandingContent }) {
  const { footer, ids } = content;
  const andere = otherLocale(content.locale);
  return (
    <footer>
      <div className="mx-auto flex max-w-6xl flex-col gap-4 px-4 py-10 text-[12.5px] text-muted-foreground sm:flex-row sm:items-center sm:justify-between sm:px-6">
        <p className="flex items-center gap-2">
          <Faultier groesse={18} still />
          {footer.tagline}
        </p>
        <nav aria-label={footer.navLabel} className="flex flex-wrap gap-4">
          {footer.links.map((link) => (
            <a key={link.section} href={`#${ids[link.section]}`} className="hover:text-foreground">
              {link.label}
            </a>
          ))}
          <a href={homeHref(andere)} hrefLang={andere} lang={andere} data-sprache={andere} className="hover:text-foreground">
            {andere === "en" ? "English" : "Deutsch"}
          </a>
        </nav>
      </div>
    </footer>
  );
}
