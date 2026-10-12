import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { AVAILABILITY_META, CRAWL_STATUS_META } from "../src/utils/labels.ts";
import type { AvailabilityStatus } from "../src/types/api.ts";

describe("AVAILABILITY_META", () => {
  it("labels every availability state", () => {
    const states: AvailabilityStatus[] = [
      "unknown",
      "available",
      "stale",
      "closed",
    ];
    for (const state of states) {
      assert.ok(AVAILABILITY_META[state].label.length > 0);
    }
  });

  it("never presents unknown or stale as confirmed available", () => {
    assert.equal(AVAILABILITY_META.unknown.tone, "muted");
    assert.equal(AVAILABILITY_META.available.tone, "success");
    assert.equal(AVAILABILITY_META.stale.tone, "warning");
    assert.notEqual(AVAILABILITY_META.stale.tone, "success");
  });

  it("marks a closed posting as a danger state", () => {
    assert.equal(AVAILABILITY_META.closed.tone, "danger");
  });
});

describe("CRAWL_STATUS_META", () => {
  it("still describes the technical crawl outcome", () => {
    assert.equal(CRAWL_STATUS_META.scraped.label, "Scraped");
    assert.equal(CRAWL_STATUS_META.failed.tone, "danger");
  });
});
