import * as React from "react";
import { cn } from "@/lib/utils";

// `skeleton-shimmer` traegt die Bewegung. `animate-pulse` nicht dazu - zwei
// ueberlagerte Animationen auf derselben Flaeche wirken unruhig und kosten
// doppelte Compositing-Arbeit.
function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("skeleton-shimmer bg-muted", className)} {...props} />;
}

export { Skeleton };
