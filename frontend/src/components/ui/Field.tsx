import type {
  ComponentPropsWithRef,
  ReactNode,
} from "react";

export const controlClass =
  "block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 placeholder-slate-400 shadow-sm transition-colors focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-500";

export function Field({
  label,
  hint,
  required = false,
  htmlFor,
  children,
}: {
  label: string;
  hint?: string;
  required?: boolean;
  htmlFor?: string;
  children: ReactNode;
}) {
  return (
    <div>
      <label
        htmlFor={htmlFor}
        className="mb-1.5 block text-sm font-medium text-slate-700"
      >
        {label}
        {required ? <span className="ml-0.5 text-rose-500">*</span> : null}
      </label>
      {children}
      {hint ? <p className="mt-1.5 text-xs text-slate-500">{hint}</p> : null}
    </div>
  );
}

export function TextInput({
  className = "",
  ...rest
}: ComponentPropsWithRef<"input">) {
  return <input className={`${controlClass} ${className}`} {...rest} />;
}

export function TextArea({
  className = "",
  ...rest
}: ComponentPropsWithRef<"textarea">) {
  return <textarea className={`${controlClass} resize-y ${className}`} {...rest} />;
}

export function Select({
  className = "",
  children,
  ...rest
}: ComponentPropsWithRef<"select">) {
  return (
    <select className={`${controlClass} ${className}`} {...rest}>
      {children}
    </select>
  );
}
