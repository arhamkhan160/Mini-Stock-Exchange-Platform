"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { Spinner } from "./ui";

/** Wrap any page that requires a login. Redirects to /login?next=<path>. */
export default function Protected({ children }: { children: React.ReactNode }) {
  const { user, ready } = useAuth();
  const router = useRouter();
  const path = usePathname();

  useEffect(() => {
    if (ready && !user) router.replace(`/login?next=${encodeURIComponent(path)}`);
  }, [ready, user, router, path]);

  if (!ready) return <Spinner label="Restoring session…" />;
  if (!user) return <Spinner label="Redirecting to sign in…" />;
  return <>{children}</>;
}
