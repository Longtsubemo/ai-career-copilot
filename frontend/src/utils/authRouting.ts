export type AuthStatus = "loading" | "authenticated" | "unauthenticated";

export const LOGIN_PATH = "/login";
export const REGISTER_PATH = "/register";

export function isAuthRoute(pathname: string): boolean {
  return pathname === LOGIN_PATH || pathname === REGISTER_PATH;
}

export function authRedirect(status: AuthStatus, pathname: string): string | null {
  if (status === "loading") {
    return null;
  }
  if (status === "authenticated") {
    return isAuthRoute(pathname) ? "/" : null;
  }
  return isAuthRoute(pathname) ? null : LOGIN_PATH;
}
