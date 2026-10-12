import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { authRedirect, isAuthRoute } from "../src/utils/authRouting.ts";

describe("isAuthRoute", () => {
  it("recognizes the login and register paths", () => {
    assert.equal(isAuthRoute("/login"), true);
    assert.equal(isAuthRoute("/register"), true);
  });

  it("rejects application paths", () => {
    assert.equal(isAuthRoute("/"), false);
    assert.equal(isAuthRoute("/jobs"), false);
    assert.equal(isAuthRoute("/onboarding"), false);
  });
});

describe("authRedirect", () => {
  it("never redirects while the session is loading", () => {
    assert.equal(authRedirect("loading", "/"), null);
    assert.equal(authRedirect("loading", "/login"), null);
  });

  it("sends authenticated users away from auth routes", () => {
    assert.equal(authRedirect("authenticated", "/login"), "/");
    assert.equal(authRedirect("authenticated", "/register"), "/");
  });

  it("keeps authenticated users on application routes", () => {
    assert.equal(authRedirect("authenticated", "/"), null);
    assert.equal(authRedirect("authenticated", "/jobs/123"), null);
  });

  it("sends unauthenticated users to login", () => {
    assert.equal(authRedirect("unauthenticated", "/"), "/login");
    assert.equal(authRedirect("unauthenticated", "/jobs"), "/login");
  });

  it("lets unauthenticated users reach the auth routes", () => {
    assert.equal(authRedirect("unauthenticated", "/login"), null);
    assert.equal(authRedirect("unauthenticated", "/register"), null);
  });
});
