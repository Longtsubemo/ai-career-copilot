import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { hasProfileEvidence, isProfileIncomplete } from "../src/utils/profile.ts";

const EMPTY = {
  headline: null,
  seniority: null,
  years_of_experience: null,
  skills: null,
  summary: null,
};

describe("hasProfileEvidence", () => {
  it("is false when every evidence field is empty", () => {
    assert.equal(hasProfileEvidence(EMPTY), false);
  });

  it("is false when fields only contain whitespace", () => {
    assert.equal(
      hasProfileEvidence({ ...EMPTY, headline: "   ", summary: "\n\t" }),
      false,
    );
  });

  it("is false for an empty skills list", () => {
    assert.equal(hasProfileEvidence({ ...EMPTY, skills: [] }), false);
    assert.equal(hasProfileEvidence({ ...EMPTY, skills: ["  "] }), false);
  });

  it("treats zero years of experience as evidence", () => {
    assert.equal(hasProfileEvidence({ ...EMPTY, years_of_experience: 0 }), true);
  });

  it("accepts any single populated field", () => {
    assert.equal(hasProfileEvidence({ ...EMPTY, headline: "Engineer" }), true);
    assert.equal(hasProfileEvidence({ ...EMPTY, seniority: "senior" }), true);
    assert.equal(hasProfileEvidence({ ...EMPTY, years_of_experience: 5 }), true);
    assert.equal(hasProfileEvidence({ ...EMPTY, skills: ["python"] }), true);
    assert.equal(hasProfileEvidence({ ...EMPTY, summary: "Builds things." }), true);
  });
});

describe("isProfileIncomplete", () => {
  it("treats a missing candidate as incomplete", () => {
    assert.equal(isProfileIncomplete(null), true);
  });

  it("mirrors hasProfileEvidence for a present candidate", () => {
    assert.equal(isProfileIncomplete(EMPTY), true);
    assert.equal(isProfileIncomplete({ ...EMPTY, summary: "Hi" }), false);
  });
});
