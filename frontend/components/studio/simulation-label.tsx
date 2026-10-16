import { Sigma } from "lucide-react";

import { SIMULATION_LABEL } from "@/lib/studio-types";
import { cn } from "@/lib/utils";

/**
 * The words "simulated, based on stated assumptions" (Studio addendum rule E). Placed on every block that shows a
 * simulated number. `text` lets the server's own label win when the API sends one.
 */
export function SimulationLabel({ text = SIMULATION_LABEL, className }: { text?: string | null; className?: string }) {
  return (
    <p className={cn("inline-flex items-center gap-1.5 text-xs font-medium text-estimate", className)}>
      <Sigma className="size-3.5 shrink-0" aria-hidden="true" />
      <span>{text || SIMULATION_LABEL}</span>
    </p>
  );
}
