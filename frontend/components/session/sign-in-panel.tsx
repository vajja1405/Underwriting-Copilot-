"use client";

import { AlertCircle, ExternalLink, Landmark, LogIn, Store } from "lucide-react";
import Link from "next/link";

import { BrandMark } from "@/components/layout/brand-mark";
import { ApiOfflineNotice } from "@/components/session/api-offline-notice";
import { DevLoginForm } from "@/components/session/dev-login-form";
import { RoleBadge } from "@/components/session/role-badge";
import { SessionErrorNotice } from "@/components/session/session-error-notice";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useSession } from "@/lib/auth";
import { useQueryParam } from "@/lib/location";
import { primaryRole } from "@/lib/roles";
import { hfLoginHref, safeNextPath, signInErrorMessage, type SignInErrorMessage } from "@/lib/sign-in";
import { useIsFramed } from "@/lib/use-is-framed";

/** "Open in a new tab": the Space's direct address, outside the Hugging Face iframe. */
function NewTabLink({ href }: { href: string }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-1 font-semibold text-accent underline underline-offset-4"
    >
      Open in a new tab
      <ExternalLink className="size-3.5" aria-hidden="true" />
    </a>
  );
}

/**
 * Shown when the page runs inside the Hugging Face iframe, where the sign-in cookie may be blocked.
 * `directUrl` is the Space's own address (from /api/config `space_url`) when the server knows it.
 */
function FramedNotice({ directUrl }: { directUrl: string }) {
  return (
    <Alert variant="accent">
      <ExternalLink aria-hidden="true" />
      <AlertTitle>Sign-in not sticking?</AlertTitle>
      <AlertDescription className="text-foreground/85">
        <p>
          This page is shown inside the Hugging Face page, and some browsers block sign-in cookies there. Open it in a
          new tab and sign in again.
        </p>
        <p>
          <NewTabLink href={directUrl} />
        </p>
      </AlertDescription>
    </Alert>
  );
}

/** Why the last sign-in failed (from /signin?error=<code>, set by the server's OAuth callback). */
function SignInErrorAlert({ message, directUrl }: { message: SignInErrorMessage; directUrl: string }) {
  return (
    <Alert variant="destructive" role="alert">
      <AlertCircle aria-hidden="true" />
      <AlertTitle>{message.title}</AlertTitle>
      <AlertDescription className="text-foreground/85">
        <p>{message.body}</p>
        {message.offerNewTab ? (
          <p>
            <NewTabLink href={directUrl} />
          </p>
        ) : null}
      </AlertDescription>
    </Alert>
  );
}

/** The sign-in page at the Space's direct address (https://<owner>-<space>.hf.space/signin/), else this page. */
function directSignInUrl(spaceUrl: string | null | undefined): string {
  if (!spaceUrl) {
    return "/signin/";
  }
  return `${spaceUrl.replace(/\/+$/, "")}/signin/`;
}

/**
 * The sign-in page body: Hugging Face sign-in, why the last attempt failed, the iframe fallback, the signed-in
 * state and (dev only) DEV LOGIN. `?next=/studio` is passed on so sign-in returns to the page the user came from.
 */
export function SignInPanel() {
  const { status, me, config, productName } = useSession();
  const framed = useIsFramed();
  const errorMessage = signInErrorMessage(useQueryParam("error"));
  const nextPath = safeNextPath(useQueryParam("next"));
  const role = primaryRole(me.roles);
  // Older servers do not send `hf_oauth`; only an explicit false turns the button off.
  const hfSignInAvailable = config?.hf_oauth !== false;
  const directUrl = directSignInUrl(config?.space_url);
  // "Not set up" is already explained next to the disabled button; do not say it twice.
  const showError = errorMessage !== null && !me.signed_in && hfSignInAvailable;
  const showFramedNotice = framed && !me.signed_in && !(showError && errorMessage.offerNewTab);

  return (
    <div className="mx-auto flex w-full max-w-md flex-col gap-6 px-4 py-12 sm:py-16">
      {showError ? <SignInErrorAlert message={errorMessage} directUrl={directUrl} /> : null}

      <Card>
        <CardHeader className="items-center gap-3 text-center">
          <BrandMark className="size-10" />
          <CardTitle as="h1" className="font-serif text-2xl">
            {me.signed_in ? "You are signed in" : `Sign in to ${productName}`}
          </CardTitle>
          <CardDescription className="text-sm leading-relaxed">
            {me.signed_in
              ? `Signed in as ${me.display_name || me.username}.`
              : `Use your Hugging Face account. ${productName} never sees your password.`}
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {status === "loading" ? <Skeleton className="h-11 w-full" /> : null}

          {status === "offline" || status === "error" ? (
            <>
              <Button type="button" size="lg" disabled className="w-full" aria-describedby="signin-unavailable-note">
                <LogIn aria-hidden="true" />
                Sign in with Hugging Face
              </Button>
              <p id="signin-unavailable-note" className="text-center text-xs text-muted-foreground">
                {status === "offline"
                  ? "Sign-in needs the server, which is not reachable right now."
                  : "Sign-in needs the server, which answered with an error. Try again in a moment."}
              </p>
            </>
          ) : null}

          {status === "ready" && !me.signed_in && !hfSignInAvailable ? (
            <>
              <Button type="button" size="lg" disabled className="w-full" aria-describedby="signin-hf-off-note">
                <LogIn aria-hidden="true" />
                Sign in with Hugging Face
              </Button>
              <p id="signin-hf-off-note" className="text-center text-xs leading-relaxed text-muted-foreground">
                Hugging Face sign-in is not set up on this server. It needs the sign-in settings a Hugging Face Space
                provides, and it has not been tested end to end yet
                {config?.dev_auth ? ". For local development, use DEV LOGIN below." : "."}
              </p>
            </>
          ) : null}

          {status === "ready" && !me.signed_in && hfSignInAvailable ? (
            <>
              <Button asChild size="lg" className="w-full">
                {framed ? (
                  // The Hugging Face docs ask for target=_blank inside the Space iframe (cookies may be blocked there).
                  <a href={hfLoginHref(nextPath)} target="_blank" rel="noopener">
                    <LogIn aria-hidden="true" />
                    Sign in with Hugging Face
                    <ExternalLink aria-hidden="true" />
                    <span className="sr-only">(opens in a new tab)</span>
                  </a>
                ) : (
                  <a href={hfLoginHref(nextPath)}>
                    <LogIn aria-hidden="true" />
                    Sign in with Hugging Face
                  </a>
                )}
              </Button>
              <p className="text-center text-xs leading-relaxed text-muted-foreground">
                {framed ? "Sign-in opens in a new tab; keep using that tab afterwards. " : null}
                {productName} asks Hugging Face only for your basic profile (such as your username and display name).
                Every signed-in user starts as a Founder; lender roles are granted by the project owners.
              </p>
            </>
          ) : null}

          {status === "ready" && me.signed_in ? (
            <>
              {role ? <RoleBadge role={role} className="self-center" /> : null}
              <div className="grid gap-2 sm:grid-cols-2">
                <Button asChild variant="outline">
                  <Link href="/studio">
                    <Store aria-hidden="true" />
                    Founder Studio
                  </Link>
                </Button>
                <Button asChild variant="outline">
                  <Link href="/lender">
                    <Landmark aria-hidden="true" />
                    Lender Desk
                  </Link>
                </Button>
              </div>
            </>
          ) : null}
        </CardContent>
      </Card>

      {status === "offline" ? <ApiOfflineNotice /> : null}

      {status === "error" ? <SessionErrorNotice /> : null}

      {showFramedNotice ? <FramedNotice directUrl={directUrl} /> : null}

      {status === "ready" && config?.dev_auth && !me.signed_in ? <DevLoginForm nextPath={nextPath} /> : null}

      <p className="text-center text-xs leading-relaxed text-muted-foreground">
        Do not enter Social Security numbers, tax returns or bank account numbers anywhere in this prototype.
      </p>
    </div>
  );
}
