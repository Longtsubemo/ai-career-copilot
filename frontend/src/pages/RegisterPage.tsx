import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { AuthLayout } from "../components/auth/AuthLayout";
import { Button } from "../components/ui/Button";
import { ErrorAlert } from "../components/ui/ErrorAlert";
import { Field, TextInput } from "../components/ui/Field";
import { useAsync } from "../hooks/useAsync";
import { useAuth } from "../hooks/useAuth";

export function RegisterPage() {
  const { register } = useAuth();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);
  const { run, pending, error } = useAsync(register, {
    onSuccess: () => navigate("/", { replace: true }),
  });

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const trimmedEmail = email.trim();
    if (!trimmedEmail) {
      setLocalError("Enter your email address.");
      return;
    }
    if (password.length < 8) {
      setLocalError("Password must be at least 8 characters.");
      return;
    }
    if (password !== confirm) {
      setLocalError("Passwords do not match.");
      return;
    }
    const trimmedName = name.trim();
    setLocalError(null);
    void run({
      email: trimmedEmail,
      password,
      name: trimmedName ? trimmedName : null,
    });
  }

  const message = localError ?? error?.message ?? null;

  return (
    <AuthLayout
      title="Create your account"
      subtitle="One account, one profile — used for matching and cover letters."
      footer={
        <>
          Already registered?{" "}
          <Link
            to="/login"
            className="font-medium text-indigo-600 hover:text-indigo-500"
          >
            Sign in
          </Link>
        </>
      }
    >
      <form onSubmit={handleSubmit} className="space-y-4" noValidate>
        {message ? <ErrorAlert message={message} /> : null}

        <Field label="Name" htmlFor="register-name" hint="Optional — you can add it later.">
          <TextInput
            id="register-name"
            autoComplete="name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Ada Lovelace"
            disabled={pending}
          />
        </Field>

        <Field label="Email" required htmlFor="register-email">
          <TextInput
            id="register-email"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="you@example.com"
            disabled={pending}
          />
        </Field>

        <Field
          label="Password"
          required
          htmlFor="register-password"
          hint="At least 8 characters."
        >
          <TextInput
            id="register-password"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="At least 8 characters"
            disabled={pending}
          />
        </Field>

        <Field label="Confirm password" required htmlFor="register-confirm">
          <TextInput
            id="register-confirm"
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={(event) => setConfirm(event.target.value)}
            placeholder="Repeat your password"
            disabled={pending}
          />
        </Field>

        <Button type="submit" loading={pending} className="w-full">
          Create account
        </Button>
      </form>
    </AuthLayout>
  );
}
