import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  APPLICATION_WORKFLOW_STEPS,
  hasRecordedSubmission,
  requiresAppliedConfirmation,
} from "../src/utils/applicationWorkflow.ts";

describe("requiresAppliedConfirmation", () => {
  it("asks for confirmation when moving into applied", () => {
    assert.equal(requiresAppliedConfirmation("draft", "applied"), true);
    assert.equal(requiresAppliedConfirmation("interview", "applied"), true);
    assert.equal(requiresAppliedConfirmation("offer", "applied"), true);
  });

  it("does not re-confirm when already applied", () => {
    assert.equal(requiresAppliedConfirmation("applied", "applied"), false);
  });

  it("never asks when moving away from applied", () => {
    assert.equal(requiresAppliedConfirmation("draft", "interview"), false);
    assert.equal(requiresAppliedConfirmation("applied", "interview"), false);
    assert.equal(requiresAppliedConfirmation("applied", "withdrawn"), false);
  });
});

describe("hasRecordedSubmission", () => {
  it("is true only when a server timestamp exists", () => {
    assert.equal(hasRecordedSubmission("2026-01-02T03:04:05Z"), true);
    assert.equal(hasRecordedSubmission(null), false);
  });
});

describe("APPLICATION_WORKFLOW_STEPS", () => {
  it("describes preparing, applying and recording in that order", () => {
    assert.equal(APPLICATION_WORKFLOW_STEPS.length, 3);
    assert.match(APPLICATION_WORKFLOW_STEPS[1].detail, /never submits/i);
    assert.match(APPLICATION_WORKFLOW_STEPS[2].detail, /Nothing is marked/i);
  });
});
