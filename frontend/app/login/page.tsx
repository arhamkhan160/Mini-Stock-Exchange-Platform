"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { useToast } from "@/components/Toast";
import { Button, Card, ErrorBox, Input } from "@/components/ui";
import { ApiError } from "@/lib/api";

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const nextUrl = searchParams?.get("next") || "/";
  const { login } = useAuth();
  const { push } = useToast();

  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await login(identifier, password);
      push("success", "Welcome back.");
      router.push(nextUrl);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="mx-auto max-w-sm py-10">
      <Card title="Sign in">
        <form onSubmit={handleSubmit} className="space-y-4">
          {error && <ErrorBox message={error} />}
          <Input
            label="Username or email"
            required
            autoFocus
            value={identifier}
            onChange={(e) => setIdentifier(e.target.value)}
            placeholder="trader01 or trader01@example.com"
          />
          <Input
            label="Password"
            type="password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
          />
          <Button type="submit" loading={loading} className="w-full">
            Sign in
          </Button>
        </form>
        <p className="mt-4 text-center text-sm text-muted">
          New here?{" "}
          <Link href="/register" className="text-accent hover:underline">
            Create an account
          </Link>
        </p>
      </Card>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginForm />
    </Suspense>
  );
}
