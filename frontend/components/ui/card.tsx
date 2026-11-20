import * as React from "react";
import { cn } from "@/lib/utils";

/*
 * Eine "Karte" ist ein Stueck Gesicht auf dem Fell: weisse Flaeche
 * (`bg-card`) mit weicher 18px-Rundung auf dem grauen Seitengrund, ohne
 * Schatten. Die Haarlinie haelt sie im Dunkeln und auf hellen Bildschirmen
 * sichtbar. `overflow-hidden` laesst Zeilen-Hover sauber an der Rundung enden.
 */
const Card = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(({ className, ...props }, ref) => (
  <div ref={ref} className={cn("overflow-hidden rounded-xl border border-border bg-card text-card-foreground", className)} {...props} />
));
Card.displayName = "Card";

const CardHeader = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(({ className, ...props }, ref) => (
  <div ref={ref} className={cn("flex flex-col space-y-1 p-4", className)} {...props} />
));
CardHeader.displayName = "CardHeader";

const CardTitle = React.forwardRef<HTMLHeadingElement, React.HTMLAttributes<HTMLHeadingElement>>(({ className, ...props }, ref) => (
  <h2 ref={ref} className={cn("text-[17px] font-semibold leading-6 tracking-tight", className)} {...props} />
));
CardTitle.displayName = "CardTitle";

/*
 * Erklaersaetze unter Abschnittstiteln sind nur noch fuer Screenreader da.
 * Sichtbar unter jedem Titel machten sie jede Seite zu einer Textwand; wer
 * etwas wissen will, fragt den Assistenten.
 */
const CardDescription = React.forwardRef<HTMLParagraphElement, React.HTMLAttributes<HTMLParagraphElement>>(({ className, ...props }, ref) => (
  <p ref={ref} className={cn("sr-only", className?.split(" ").filter((name) => !/^(text|leading|mt|mb|max-w)-/.test(name)).join(" "))} {...props} />
));
CardDescription.displayName = "CardDescription";

const CardContent = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(({ className, ...props }, ref) => (
  <div ref={ref} className={cn("p-4 pt-0", className)} {...props} />
));
CardContent.displayName = "CardContent";

const CardFooter = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(({ className, ...props }, ref) => (
  <div ref={ref} className={cn("flex items-center p-4 pt-0", className)} {...props} />
));
CardFooter.displayName = "CardFooter";

export { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter };
