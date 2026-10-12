import { useState } from "react";
import { Link, NavLink, Outlet } from "react-router-dom";

import { useAuth } from "../hooks/useAuth";
import { useCandidate } from "../hooks/useCandidate";
import { Icon, type IconName } from "../components/ui/Icon";

interface NavItem {
  to: string;
  label: string;
  icon: IconName;
  end?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { to: "/", label: "Dashboard", icon: "home", end: true },
  { to: "/jobs", label: "Jobs", icon: "search" },
  { to: "/applications", label: "Applications", icon: "briefcase" },
  { to: "/resume", label: "Resume", icon: "file" },
  { to: "/profile", label: "Profile", icon: "user" },
];

function navClasses(isActive: boolean, mobile = false): string {
  const base = "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors";
  if (mobile) {
    return [
      base,
      isActive
        ? "bg-indigo-50 text-indigo-700"
        : "text-slate-700 hover:bg-slate-100",
    ].join(" ");
  }
  return [
    base,
    isActive
      ? "bg-slate-800 text-white"
      : "text-slate-300 hover:bg-slate-800/70 hover:text-white",
  ].join(" ");
}

function Brand({ dark = true }: { dark?: boolean }) {
  return (
    <div className="flex items-center gap-2.5">
      <span
        className={`flex h-8 w-8 items-center justify-center rounded-lg text-sm font-bold ${
          dark ? "bg-indigo-500 text-white" : "bg-indigo-600 text-white"
        }`}
      >
        C
      </span>
      <span className="flex flex-col leading-tight">
        <span
          className={`text-sm font-semibold ${dark ? "text-white" : "text-slate-900"}`}
        >
          Career Copilot
        </span>
        <span
          className={`text-xs ${dark ? "text-slate-400" : "text-slate-500"}`}
        >
          AI job search
        </span>
      </span>
    </div>
  );
}

export function AppLayout() {
  const [menuOpen, setMenuOpen] = useState(false);
  const { candidate } = useCandidate();
  const { user, logout } = useAuth();

  return (
    <div className="min-h-screen">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 flex-col bg-slate-900 lg:flex">
        <div className="px-5 py-6">
          <Link to="/" aria-label="Career Copilot home">
            <Brand />
          </Link>
        </div>

        <nav className="flex-1 space-y-1 px-3">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) => navClasses(isActive)}
            >
              <Icon name={item.icon} className="h-5 w-5 shrink-0" />
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="border-t border-slate-800 px-5 py-4">
          <p className="truncate text-sm font-medium text-white">
            {candidate ? candidate.name : (user?.email ?? "Your profile")}
          </p>
          <p className="truncate text-xs text-slate-400">
            {candidate?.headline ?? (user?.email ?? "Single-user workspace")}
          </p>
          <button
            type="button"
            onClick={() => void logout()}
            className="mt-3 flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-xs font-medium text-slate-300 transition-colors hover:bg-slate-800 hover:text-white"
          >
            <Icon name="logout" className="h-4 w-4" />
            Sign out
          </button>
        </div>
      </aside>

      <div className="lg:pl-64">
        <header className="sticky top-0 z-20 border-b border-slate-200 bg-white/90 backdrop-blur lg:hidden">
          <div className="flex items-center justify-between px-4 py-3">
            <Link to="/" aria-label="Career Copilot home">
              <Brand dark={false} />
            </Link>
            <button
              type="button"
              onClick={() => setMenuOpen((open) => !open)}
              className="rounded-lg p-2 text-slate-600 hover:bg-slate-100"
              aria-label={menuOpen ? "Close menu" : "Open menu"}
              aria-expanded={menuOpen}
            >
              <Icon name={menuOpen ? "close" : "menu"} />
            </button>
          </div>
          {menuOpen ? (
            <nav className="space-y-1 border-t border-slate-200 px-3 py-3">
              {NAV_ITEMS.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.end}
                  onClick={() => setMenuOpen(false)}
                  className={({ isActive }) => navClasses(isActive, true)}
                >
                  <Icon name={item.icon} className="h-5 w-5 shrink-0" />
                  {item.label}
                </NavLink>
              ))}
              <button
                type="button"
                onClick={() => {
                  setMenuOpen(false);
                  void logout();
                }}
                className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-100"
              >
                <Icon name="logout" className="h-5 w-5 shrink-0" />
                Sign out
              </button>
            </nav>
          ) : null}
        </header>

        <main className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:px-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
