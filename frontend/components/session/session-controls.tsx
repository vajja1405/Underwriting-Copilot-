"use client";

import { AlertTriangle, LogIn, LogOut, WifiOff } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { RoleBadge } from "@/components/session/role-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useSession } from "@/lib/auth";
import { useCurrentPath } from "@/lib/location";
import { primaryRole } from "@/lib/roles";
import { signInPageHref } from "@/lib/sign-in";
import { cn } from "@/lib/utils";

/** The dashed "DEV" mark for a session made with DEV LOGIN (never possible in a Space). */
function DevBadge() {
  return (
    <Badge variant="outline" className="border-dashed font-semibold tracking-wide">
      DEV
      <span className="sr-only"> login (local development only)</span>
    </Badge>
  );
}

/**
 * Sign-in state for the header and the mobile menu: a skeleton while loading, an "Offline preview" badge when the
 * API is unreachable, a "Server busy" / "Server error" badge when it answered with an error and nobody is known to
 * be signed in, a Sign in button when signed out, and name + role badge (+ DEV for a dev login) + Sign out when
 * signed in (also kept through a server error).
 *
 * layout: "inline" (desktop header), "stacked" (mobile menu), "compact" (phone header bar: role and DEV only; the
 * full controls are in the menu).
 */
export function SessionControls({
  layout = "inline",
  onNavigate,
}: {
  layout?: "inline" | "stacked" | "compact";
  onNavigate?: () => void;
}) {
  const { status, me, error, signOut } = useSession();
  const currentPath = useCurrentPath();
  const [signingOut, setSigningOut] = useState(false);
  const [signOutFailed, setSignOutFailed] = useState(false);
  const stacked = layout === "stacked";

  async function handleSignOut() {
    setSigningOut(true);
    setSignOutFailed(false);
    try {
      await signOut();
    } catch {
      setSignOutFailed(true);
    } finally {
      setSigningOut(false);
    }
  }

  const compact = layout === "compact";

  if (status === "loading") {
    const skeletonWidth = { inline: "w-24", stacked: "w-full", compact: "w-16" }[layout];
    return <Skeleton className={cn("h-8", skeletonWidth)} aria-label="Checking sign-in" />;
  }

  if (status === "offline") {
    return (
      <Badge variant="muted" className="h-8 gap-1.5 rounded-md px-2.5">
        <WifiOff aria-hidden="true" />
        {compact ? "Offline" : "Offline preview"}
      </Badge>
    );
  }

  if (status === "error" && !me.signed_in) {
    const label = error?.status === 429 ? "Server busy" : "Server error";
    return (
      <Badge variant="muted" className="h-8 gap-1.5 rounded-md px-2.5">
        <AlertTriangle aria-hidden="true" />
        {label}
      </Badge>
    );
  }

  if (!me.signed_in) {
    return (
      <Button asChild size="sm" variant={stacked ? "default" : "outline"} className={cn(stacked && "w-full")}>
        <Link href={signInPageHref(currentPath)} onClick={onNavigate}>
          <LogIn aria-hidden="true" />
          Sign in
        </Link>
      </Button>
    );
  }

  const role = primaryRole(me.roles);
  const name = me.display_name || me.username || "Signed in";

  if (compact) {
    // Stacked so the role and the DEV mark both fit next to the product name at 375 px.
    return (
      <div className="flex flex-col items-end gap-0.5">
        {role ? <RoleBadge role={role} /> : null}
        {me.auth_mode === "dev" ? <DevBadge /> : null}
      </div>
    );
  }

  return (
    <div className={cn("flex items-center gap-2", stacked && "flex-col items-stretch gap-3")}>
      <div className={cn("flex min-w-0 items-center gap-2", stacked && "justify-between")}>
        <span className="max-w-[10rem] truncate text-sm font-medium" title={me.username ?? undefined}>
          {name}
        </span>
        {role ? <RoleBadge role={role} /> : null}
        {me.auth_mode === "dev" ? <DevBadge /> : null}
      </div>
      <Button
        type="button"
        size="sm"
        variant={stacked ? "outline" : "ghost"}
        onClick={handleSignOut}
        disabled={signingOut}
      >
        <LogOut aria-hidden="true" />
        {signingOut ? "Signing out..." : "Sign out"}
      </Button>
      {signOutFailed ? (
        <p role="alert" className="text-xs text-conflicting">
          Sign-out failed. Try again.
        </p>
      ) : null}
    </div>
  );
}
