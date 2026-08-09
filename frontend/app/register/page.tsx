"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { useToast } from "@/components/Toast";
import { Button, Card, ErrorBox, Input } from "@/components/ui";
import { ApiError } from "@/lib/api";

const USERNAME_RE = /^[a-zA-Z0-9_]{3,32}$/;

export default function RegisterPage() {
  const router = useRouter();
  const { register } = useAuth();
  const { push } = useToast();

  const [form, setForm] = useState({ username: "", email: "", password: "", full_name: "" });
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  function set<K extends keyof typeof form>(key: K, value: string) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  function validate(): string | null {
    if (!USERNAME_RE.test(form.username)) return "Username must be 3-32 characters: letters, digits, underscore";
    if (form.password.length < 8) return "Password must be at least 8 characters";
    return null;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const validation = validate();
    if (validation) {
      setError(validation);
      return;
    }
    setError(null);
    setLoading(true);
    try {
      await register({
        username: form.username,
        email: form.email,
        password: form.password,
        full_name: form.full_name || undefined,
      });
      push("success", "Account created — welcome to the exchange.");
      router.push("/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Registration failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="mx-auto max-w-sm py-10">
      <Card title="Create an account">
        <form onSubmit={handleSubmit} className="space-y-4">
          {error && <ErrorBox message={error} />}
          <Input
            label="Username"
            required
            autoFocus
            value={form.username}
            onChange={(e) => set("username", e.target.value)}
            placeholder="trader01"
          />
          <Input
            label="Email"
            type="email"
            required
            value={form.email}
            onChange={(e) => set("email", e.target.value)}
            placeholder="trader01@example.com"
          />
          <Input
            label="Full name (optional)"
            value={form.full_name}
            onChange={(e) => set("full_name", e.target.value)}
            placeholder="Ada Trader"
          />
          <Input
            label="Password"
            type="password"
            required
            value={form.password}
            onChange={(e) => set("password", e.target.value)}
            placeholder="At least 8 characters"
          />
          <Button type="submit" loading={loading} className="w-full">
            Create account
          </Button>
        </form>
        <p className="mt-4 text-center text-sm text-muted">
          Already have an account?{" "}
          <Link href="/login" className="text-accent hover:underline">
            Sign in
          </Link>
        </p>
      </Card>
    </div>
  );
}
