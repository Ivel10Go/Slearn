import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex w-fit items-center gap-1.5 border px-2.5 py-1 text-[11px] font-bold tracking-wide transition-colors",
  {
    variants: {
      variant: {
        default: "border-primary bg-primary text-primary-foreground",
        secondary: "border-muted bg-muted text-foreground",
        outline: "border-border bg-transparent text-muted-foreground",
        success: "border-primary bg-primary text-primary-foreground",
        warning: "border-foreground/25 bg-foreground/10 text-foreground",
        info: "border-border bg-card text-foreground",
        accent: "border-border bg-muted text-foreground",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  },
);

export interface BadgeProps extends React.HTMLAttributes<HTMLDivElement>, VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
