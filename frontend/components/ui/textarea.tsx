import * as React from "react";
import { cn } from "@/lib/utils";

const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, rows = 3, ...props }, ref) => (
    <textarea
      ref={ref}
      rows={rows}
      className={cn(
        "flex w-full resize-y rounded-md border border-input bg-transparent px-3 py-2 text-[13px] leading-5 text-foreground transition-colors placeholder:text-muted-foreground focus-visible:border-foreground focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50 aria-[invalid=true]:border-foreground aria-[invalid=true]:ring-1 aria-[invalid=true]:ring-foreground",
        className,
      )}
      {...props}
    />
  ),
);
Textarea.displayName = "Textarea";

export { Textarea };
