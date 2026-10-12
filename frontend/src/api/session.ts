export const CSRF_COOKIE_NAME = "career_copilot_csrf";
export const CSRF_HEADER_NAME = "X-CSRF-Token";

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS", "TRACE"]);

export function readCookie(cookieString: string, name: string): string | null {
  if (!cookieString) {
    return null;
  }
  for (const part of cookieString.split(";")) {
    const separator = part.indexOf("=");
    if (separator < 0) {
      continue;
    }
    if (part.slice(0, separator).trim() !== name) {
      continue;
    }
    const raw = part.slice(separator + 1).trim();
    try {
      return decodeURIComponent(raw);
    } catch {
      return raw;
    }
  }
  return null;
}

export function isUnsafeMethod(method?: string): boolean {
  if (!method) {
    return false;
  }
  return !SAFE_METHODS.has(method.toUpperCase());
}

export function csrfHeader(
  cookieString: string,
  method?: string,
  cookieName: string = CSRF_COOKIE_NAME,
): Record<string, string> {
  if (!isUnsafeMethod(method)) {
    return {};
  }
  const token = readCookie(cookieString, cookieName);
  return token ? { [CSRF_HEADER_NAME]: token } : {};
}
