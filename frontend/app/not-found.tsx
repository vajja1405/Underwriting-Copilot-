import { ArrowLeft } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";

import { EvidenceChip } from "@/components/evidence/evidence-chip";
import { Button } from "@/components/ui/button";

export const metadata: Metadata = {
  title: "Page not found",
};

/** 404 page (exported as out/404.html; the FastAPI app serves it for unknown paths). */
export default function NotFound() {
  return (
    <div className="mx-auto flex w-full max-w-xl flex-col items-start gap-5 px-4 py-20 sm:py-28">
      <EvidenceChip status="UNKNOWN" size="md" />
      <h1 className="font-serif text-4xl font-semibold tracking-tight text-foreground">Page not found</h1>
      <p className="text-base leading-relaxed text-muted-foreground">
        There is nothing at this address. Rather than guess what you meant, here is the way back.
      </p>
      <div className="flex flex-col gap-3 sm:flex-row">
        <Button asChild>
          <Link href="/">
            <ArrowLeft aria-hidden="true" />
            Back to the home page
          </Link>
        </Button>
        <Button asChild variant="outline">
          <Link href="/about">About and data sources</Link>
        </Button>
      </div>
    </div>
  );
}
