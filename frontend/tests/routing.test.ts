import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const appSource = await readFile(new URL("../src/App.tsx", import.meta.url), "utf8");
const layoutSource = await readFile(
  new URL("../src/layouts/AppLayout.tsx", import.meta.url),
  "utf8",
);
const clientSource = await readFile(
  new URL("../src/api/client.ts", import.meta.url),
  "utf8",
);
const mainSource = await readFile(new URL("../src/main.tsx", import.meta.url), "utf8");

test("the application opens through local profile initialization into the dashboard", () => {
  assert.match(appSource, /<CandidateProvider>/);
  assert.match(appSource, /<Route index element=\{<DashboardPage \/>\}/);
  assert.doesNotMatch(appSource, /LoginPage|RegisterPage|OnboardingPage|Navigate/);
});

test("the main navigation has no sign-in or sign-out flow", () => {
  assert.match(layoutSource, /label: "Dashboard"/);
  assert.match(layoutSource, /label: "Profile"/);
  assert.doesNotMatch(layoutSource, /Sign out|logout|useAuth/);
});

test("API requests do not depend on cookies or CSRF tokens", () => {
  assert.doesNotMatch(clientSource, /withCredentials|CSRF|cookie|auth:unauthorized/);
});

test("desktop API requests use an in-memory bearer capability", () => {
  assert.match(clientSource, /installApiCapability\(capability: string\)/);
  assert.match(clientSource, /headers\.common\.Authorization = `Bearer \$\{capability\}`/);
  assert.match(clientSource, /delete api\.defaults\.headers\.common\.Authorization/);
  assert.doesNotMatch(clientSource, /localStorage|sessionStorage/);
  assert.match(mainSource, /invoke<string>\("api_capability"\)/);
  assert.match(mainSource, /installApiCapability\(capability\)/);
  assert.doesNotMatch(mainSource, /sessionStorage|location\.replace/);
});
