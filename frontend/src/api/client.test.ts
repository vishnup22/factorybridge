import { ApiError, errorMessage } from "./client";
import type { Problem } from "./types";

const problem = (extra: Record<string, unknown>): Problem =>
  ({ type: "about:blank", title: "Validation failed", status: 422, detail: "Resolution rejected", code: "X", extra }) as Problem;

test("errorMessage surfaces artifact validation findings with their codes", () => {
  const e = new ApiError(
    problem({ validation: { findings: [{ code: "QC_CHECK_STALE", message: "expects 24 but parts specify 48", field: "checks[0].expected" }] } }),
  );
  expect(errorMessage(e)).toBe("Resolution rejected — checks[0].expected: QC_CHECK_STALE: expects 24 but parts specify 48");
});

test("errorMessage prefers change-set issues", () => {
  const e = new ApiError(problem({ issues: [{ field_path: "changes[0]", code: "LINE_NOT_FOUND", message: "no such line" }] }));
  expect(errorMessage(e)).toBe("Resolution rejected — changes[0]: LINE_NOT_FOUND: no such line");
});
