import type { ReactNode } from "react";
import { localeMetadata, RootDocument } from "@/components/root-document";

export { viewport } from "@/components/root-document";
export const metadata = localeMetadata("de");

export default function GermanLayout({ children }: Readonly<{ children: ReactNode }>) {
  return <RootDocument locale="de">{children}</RootDocument>;
}
