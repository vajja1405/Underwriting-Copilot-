import { splitFactorReason } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * One SiteIQ reason, as sent by the server, with its factor code shown as words:
 * "renter_households: 15,409 renter households ..." reads "Renter households: 15,409 renter households ...".
 * The explanation and its numbers are the server's text, unchanged.
 */
export function ReasonText({ reason, lead, className }: { reason: string; lead?: string; className?: string }) {
  const { factor, text } = splitFactorReason(reason);
  return (
    <p className={cn("text-xs leading-relaxed", className)}>
      {lead ? <span className="font-semibold">{lead} </span> : null}
      {factor ? <span className="font-semibold">{factor}: </span> : null}
      {text}
    </p>
  );
}
