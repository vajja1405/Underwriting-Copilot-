import type { Metadata } from "next";

import { PlanWizard } from "@/components/studio/wizard/plan-wizard";

export const metadata: Metadata = {
  title: "Start a plan",
  description: "City, business type, idea, money and up to three candidate sites.",
};

export default function NewPlanPage() {
  return <PlanWizard />;
}
