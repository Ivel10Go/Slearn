import type { ReactNode } from "react";
import { localeMetadata, RootDocument } from "@/components/root-document";

export { viewport } from "@/components/root-document";
export const metadata = localeMetadata("en");

export default function EnglishLayout({ children }: Readonly<{ children: ReactNode }>) {
  return <RootDocument locale="en">{children}</RootDocument>;
}
