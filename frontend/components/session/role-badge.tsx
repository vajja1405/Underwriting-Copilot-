import { Landmark, ShieldCheck, Store, type LucideIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { Role } from "@/lib/api";
import { ROLE_LABELS } from "@/lib/roles";
import { cn } from "@/lib/utils";

const ROLE_ICONS: Record<Role, LucideIcon> = {
  founder: Store,
  lender_reviewer: Landmark,
  lender_senior: Landmark,
  admin: ShieldCheck,
};

/** Shows the user's role in words (with an icon); the server decides what the role may do. */
export function RoleBadge({ role, className }: { role: Role; className?: string }) {
  const Icon = ROLE_ICONS[role];
  return (
    <Badge variant="soft" className={cn("font-medium", className)}>
      <Icon aria-hidden="true" />
      <span className="sr-only">Role: </span>
      {ROLE_LABELS[role]}
    </Badge>
  );
}
