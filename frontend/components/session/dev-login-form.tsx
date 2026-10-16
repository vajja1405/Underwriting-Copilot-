"use client";

import { Wrench } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ApiError, type Role } from "@/lib/api";
import { useSession } from "@/lib/auth";
import { hasLenderRole, isRole, ROLE_LABELS, ROLES } from "@/lib/roles";

/**
 * DEV LOGIN: pick a demo user and role without Hugging Face. Rendered only when /api/config says dev_auth is on
 * (UWC_DEV_AUTH=1 on a local server). The server refuses dev login inside a Hugging Face Space.
 * After signing in it goes to `nextPath` (the page that sent the user to sign in) or the role's home area.
 */
export function DevLoginForm({ nextPath = null }: { nextPath?: string | null }) {
  const { devLogin } = useSession();
  const router = useRouter();
  const [username, setUsername] = useState("demo-founder");
  const [role, setRole] = useState<Role>("founder");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await devLogin(username.trim(), role);
      router.push(nextPath ?? (hasLenderRole([role]) ? "/lender" : "/studio"));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Dev login failed.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section
      aria-labelledby="dev-login-title"
      className="flex flex-col gap-4 rounded-xl border-2 border-dashed border-stated-line bg-stated-soft/60 p-5 sm:p-6"
    >
      <div className="flex flex-col gap-1.5">
        <h2 id="dev-login-title" className="flex items-center gap-2 text-sm font-bold uppercase tracking-[0.12em] text-stated">
          <Wrench className="size-4" aria-hidden="true" />
          Dev login
        </h2>
        <p className="text-sm text-foreground">
          Local development only. The server turns this off inside a Hugging Face Space. No password; pick a demo user
          and a role.
        </p>
      </div>
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <div className="flex flex-col gap-2">
          <Label htmlFor="dev-username">Username</Label>
          <Input
            id="dev-username"
            name="username"
            autoComplete="off"
            required
            minLength={2}
            maxLength={64}
            value={username}
            onChange={(event) => setUsername(event.target.value)}
          />
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="dev-role">Role</Label>
          <Select value={role} onValueChange={(value) => (isRole(value) ? setRole(value) : null)}>
            <SelectTrigger id="dev-role">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {ROLES.map((value) => (
                <SelectItem key={value} value={value}>
                  {ROLE_LABELS[value]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        {error ? (
          <p role="alert" className="text-sm font-medium text-conflicting">
            {error}
          </p>
        ) : null}
        <Button type="submit" variant="outline" disabled={submitting || username.trim().length < 2} className="self-start">
          {submitting ? "Signing in..." : "Sign in (dev)"}
        </Button>
      </form>
    </section>
  );
}
