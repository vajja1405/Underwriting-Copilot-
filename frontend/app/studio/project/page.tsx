import type { Metadata } from "next";

import { ProjectDashboard } from "@/components/studio/project/project-dashboard";

export const metadata: Metadata = {
  title: "Plan results",
  description: "Site scores, simulated revenue and cash ranges, and the path to approval for one plan.",
};

/** /studio/project/?id=<plan id>: a query string, because a static export cannot pre-render unknown ids. */
export default function ProjectPage() {
  return <ProjectDashboard />;
}
