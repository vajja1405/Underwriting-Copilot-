"use client";

import { LogIn } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useCurrentPath } from "@/lib/location";
import { signInPageHref } from "@/lib/sign-in";

/**
 * Shown on signed-in areas when nobody is signed in. The link remembers this page (`/signin?next=...`), so
 * sign-in returns here. The title is an h2: the card sits directly under the page's h1.
 */
export function SignInPrompt({ area, reason }: { area: string; reason: string }) {
  const currentPath = useCurrentPath();

  return (
    <Card>
      <CardHeader>
        <CardTitle as="h2">Sign in to open {area}</CardTitle>
        <CardDescription>{reason}</CardDescription>
      </CardHeader>
      <CardContent>
        <Button asChild>
          <Link href={signInPageHref(currentPath)}>
            <LogIn aria-hidden="true" />
            Sign in
          </Link>
        </Button>
      </CardContent>
    </Card>
  );
}
