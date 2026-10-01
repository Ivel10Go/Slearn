import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex h-5 w-fit shrink-0 items-center gap-1 whitespace-nowrap rounded-sm border px-1.5 text-[11px] font-medium leading-none transition-colors [&_svg]:size-3",
  {
    variants: {
      variant: {
        default: "border-primary bg-primary text-primary-foreground",
        secondary: "border-transparent bg-secondary text-muted-foreground",
        outline: "border-border bg-transparent text-muted-foreground",
        success: "border-transparent bg-foreground text-background",
        warning: "border-foreground bg-transparent font-semibold text-foreground",
        info: "border-border bg-transparent text-foreground",
        accent: "border-transparent bg-accent text-foreground",
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
