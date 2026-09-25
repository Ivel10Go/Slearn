import { afterEach, describe, expect, it } from "vitest";
import { ApiError, canUseDemoFallback } from "./api";

const originalDemoFlag = process.env.NEXT_PUBLIC_ENABLE_DEMO_FALLBACK;

afterEach(() => {
  if (originalDemoFlag === undefined) {
    delete process.env.NEXT_PUBLIC_ENABLE_DEMO_FALLBACK;
  } else {
    process.env.NEXT_PUBLIC_ENABLE_DEMO_FALLBACK = originalDemoFlag;
  }
});

describe("Demo-Fallback", () => {
  it("ist nur für Netzwerkfehler und 503 aktiviert", () => {
    process.env.NEXT_PUBLIC_ENABLE_DEMO_FALLBACK = "true";
    expect(canUseDemoFallback(new ApiError("offline", 0))).toBe(true);
    expect(canUseDemoFallback(new ApiError("nicht verfügbar", 503))).toBe(true);
    expect(canUseDemoFallback(new ApiError(" unauthorized", 401))).toBe(false);
    expect(canUseDemoFallback(new ApiError("ungültig", 422))).toBe(false);
  });

  it("bleit bei deaktivierter Demo aus", () => {
    process.env.NEXT_PUBLIC_ENABLE_DEMO_FALLBACK = "false";
    expect(canUseDemoFallback(new ApiError("offline", 0))).toBe(false);
    expect(canUseDemoFallback(new Error("schema"))).toBe(false);
  });
});
