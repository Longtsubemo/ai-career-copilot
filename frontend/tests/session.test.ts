import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  CSRF_COOKIE_NAME,
  CSRF_HEADER_NAME,
  csrfHeader,
  isUnsafeMethod,
  readCookie,
} from "../src/api/session.ts";

describe("readCookie", () => {
  it("returns the value for a matching cookie name", () => {
    assert.equal(readCookie(`${CSRF_COOKIE_NAME}=abc123`, CSRF_COOKIE_NAME), "abc123");
  });

  it("ignores surrounding cookies and whitespace", () => {
    const cookie = `other=1; ${CSRF_COOKIE_NAME}= token%20value ; third=x`;
    assert.equal(readCookie(cookie, CSRF_COOKIE_NAME), "token value");
  });

  it("decodes percent-encoded values", () => {
    assert.equal(readCookie("csrf=a%3Db%26c", "csrf"), "a=b&c");
  });

  it("returns null when the cookie is absent", () => {
    assert.equal(readCookie("a=1; b=2", CSRF_COOKIE_NAME), null);
    assert.equal(readCookie("", CSRF_COOKIE_NAME), null);
  });

  it("matches the full cookie name, not a prefix", () => {
    assert.equal(readCookie(`${CSRF_COOKIE_NAME}_value=1`, CSRF_COOKIE_NAME), null);
  });
});

describe("isUnsafeMethod", () => {
  it("treats reads as safe regardless of case", () => {
    assert.equal(isUnsafeMethod("GET"), false);
    assert.equal(isUnsafeMethod("head"), false);
    assert.equal(isUnsafeMethod("Options"), false);
  });

  it("treats mutations as unsafe regardless of case", () => {
    assert.equal(isUnsafeMethod("POST"), true);
    assert.equal(isUnsafeMethod("patch"), true);
    assert.equal(isUnsafeMethod("DELETE"), true);
  });

  it("returns false when no method is present", () => {
    assert.equal(isUnsafeMethod(undefined), false);
    assert.equal(isUnsafeMethod(""), false);
  });
});

describe("csrfHeader", () => {
  it("adds the token only for unsafe methods", () => {
    const cookie = `${CSRF_COOKIE_NAME}=abc`;
    assert.deepEqual(csrfHeader(cookie, "GET"), {});
    assert.deepEqual(csrfHeader(cookie, "POST"), { [CSRF_HEADER_NAME]: "abc" });
  });

  it("returns no header when the token is missing", () => {
    assert.deepEqual(csrfHeader("other=1", "POST"), {});
  });

  it("supports an alternative cookie name", () => {
    assert.deepEqual(csrfHeader("my_csrf=token", "PATCH", "my_csrf"), {
      [CSRF_HEADER_NAME]: "token",
    });
  });
});
