import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { AuthLayout } from "../components/auth/AuthLayout";
import { Button } from "../components/ui/Button";
import { ErrorAlert } from "../components/ui/ErrorAlert";
import { Field, TextInput } from "../components/ui/Field";
import { useAsync } from "../hooks/useAsync";
import { useAuth } from "../hooks/useAuth";

export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);
  const { run, pending, error } = useAsync(login, {
    onSuccess: () => navigate("/", { replace: true }),
  });

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const trimmedEmail = email.trim();
    if (!trimmedEmail) {
      setLocalError("Enter your email address.");
      return;
    }
    if (!password) {
      setLocalError("Enter your password.");
      return;
    }
    setLocalError(null);
    void run({ email: trimmedEmail, password });
  }

  const message = localError ?? error?.message ?? null;

  return (
    <AuthLayout
      title="Sign in"
      subtitle="Welcome back. Sign in to continue."
      footer={
        <>
          New here?{" "}
          <Link
            to="/register"
            className="font-medium text-indigo-600 hover:text-indigo-500"
          >
            Create an account
          </Link>
        </>
      }
    >
      <form onSubmit={handleSubmit} className="space-y-4" noValidate>
        {message ? <ErrorAlert message={message} /> : null}

        <Field label="Email" required htmlFor="login-email">
          <TextInput
            id="login-email"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="you@example.com"
            disabled={pending}
          />
        </Field>

        <Field label="Password" required htmlFor="login-password">
          <TextInput
            id="login-password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="Your password"
            disabled={pending}
          />
        </Field>

        <Button type="submit" loading={pending} className="w-full">
          Sign in
        </Button>
      </form>
    </AuthLayout>
  );
}
