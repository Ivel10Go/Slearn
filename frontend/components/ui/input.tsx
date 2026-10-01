import * as React from "react";
import { cn } from "@/lib/utils";

const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(({ className, type, ...props }, ref) => (
  <input
    ref={ref}
    type={type}
    className={cn(
      "flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1.5 text-[13px] text-foreground transition-colors file:border-0 file:bg-transparent file:text-[13px] file:font-medium placeholder:text-muted-foreground focus-visible:border-foreground focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50 aria-[invalid=true]:border-foreground aria-[invalid=true]:ring-1 aria-[invalid=true]:ring-foreground",
      className,
    )}
    {...props}
  />
));
Input.displayName = "Input";

export { Input };
