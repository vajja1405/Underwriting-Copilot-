import type { Metadata } from "next";

import { SignInPanel } from "@/components/session/sign-in-panel";

export const metadata: Metadata = {
  title: "Sign in",
  description: "Sign in with your Hugging Face account.",
};

export default function SignInPage() {
  return <SignInPanel />;
}
