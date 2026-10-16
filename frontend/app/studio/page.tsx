import type { Metadata } from "next";

import { StudioHome } from "@/components/studio/studio-home";

export const metadata: Metadata = {
  title: "Founder Studio",
  description: "Plan a business location and prepare a loan-ready file where every number shows its evidence.",
};

export default function StudioPage() {
  return <StudioHome />;
}
