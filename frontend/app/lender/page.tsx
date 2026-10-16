import type { Metadata } from "next";

import { LenderHome } from "@/components/lender/lender-home";

export const metadata: Metadata = {
  title: "Lender Desk",
  description: "Review small-business loan files with the evidence beside every figure. A person always decides.",
};

export default function LenderPage() {
  return <LenderHome />;
}
