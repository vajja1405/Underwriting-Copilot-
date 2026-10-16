import type { Metadata } from "next";

import { LenderFileView } from "@/components/lender/file-view";

export const metadata: Metadata = {
  title: "Demo file",
  description: "A SYNTHETIC demo loan file at the human checkpoint: findings, calculations, screening and the memo.",
};

/** /lender/file/?id=<file id>: a query string, because a static export cannot pre-render unknown ids. */
export default function LenderFilePage() {
  return <LenderFileView />;
}
