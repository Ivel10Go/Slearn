import { afterEach, describe, expect, it } from "vitest";
import {
  getForwardRequestHeaders,
  getForwardResponseHeaders,
  getPathSegmentsFromRequest,
  getUpstreamUrl,
  isAllowedMethod,
} from "./proxy";

const originalBackendUrl = process.env.BACKEND_INTERNAL_URL;

afterEach(() => {
  if (originalBackendUrl === undefined) {
    delete process.env.BACKEND_INTERNAL_URL;
  } else {
    process.env.BACKEND_INTERNAL_URL = originalBackendUrl;
  }
});

describe("Backend-Proxy", () => {
  it("bildet den API-Pfad serverseitig auf das Backend ab", () => {
    process.env.BACKEND_INTERNAL_URL = "http://backend.internal:9000/";
    const upstream = getUpstreamUrl(getPathSegmentsFromRequest(["v1", "dashboard"]), new URL("https://app.test/api/backend/v1/dashboard?view=today"));
    expect(upstream?.toString()).toBe("http://backend.internal:9000/api/v1/dashboard?view=today");
  });

  it("weist Traversal und ungültige Konfiguration ab", () => {
    process.env.BACKEND_INTERNAL_URL = "http://backend.internal:9000";
    expect(getUpstreamUrl(["v1", "..", "dashboard"], new URL("https://app.test/api/backend/v1/dashboard"))).toBeNull();
    expect(getUpstreamUrl(["v1", "%2e%2e", "dashboard"], new URL("https://app.test/api/backend/v1/dashboard"))).toBeNull();
    expect(getUpstreamUrl(["v1", "%252e%252e", "dashboard"], new URL("https://app.test/api/backend/v1/dashboard"))).toBeNull();
    expect(getUpstreamUrl(getPathSegmentsFromRequest("v1//dashboard"), new URL("https://app.test/api/backend/v1//dashboard"))).toBeNull();
    process.env.BACKEND_INTERNAL_URL = "file:///tmp/backend";
    expect(getUpstreamUrl(["v1", "dashboard"], new URL("https://app.test/api/backend/v1/dashboard"))).toBeNull();
  });

  it("entfernt Kontrollheader und bewahrt Session-Cookies", () => {
    const request = new Request("https://app.test/api/backend/v1/auth/login", {
      headers: {
        cookie: "session=abc",
        connection: "keep-alive",
        host: "app.test",
        "content-length": "12",
      },
    });
    const forwarded = getForwardRequestHeaders(request);
    expect(forwarded.get("cookie")).toBe("session=abc");
    expect(forwarded.get("connection")).toBeNull();
    expect(forwarded.get("host")).toBeNull();
    expect(forwarded.get("content-length")).toBeNull();
  });

  it("entfernt Redirects und setzt API-Antworten auf no-store", () => {
    const response = new Response("{}", {
      status: 302,
      headers: [
        ["location", "https://other.example/"],
        ["refresh", "0; url=https://other.example/"],
        ["content-security-policy", "default-src *"],
        ["set-cookie", "a=1"],
        ["set-cookie", "b=2"],
        ["x-request-id", "request-1"],
      ],
    });
    const headers = getForwardResponseHeaders(response);
    expect(headers.get("location")).toBeNull();
    expect(headers.get("refresh")).toBeNull();
    expect(headers.get("content-security-policy")).toBeNull();
    expect(headers.getSetCookie()).toEqual(["a=1", "b=2"]);
    expect(headers.get("cache-control")).toBe("no-store");
    expect(headers.get("x-request-id")).toBe("request-1");
  });

  it("erlaubt nur sichere API-Methoden", () => {
    expect(isAllowedMethod("GET")).toBe(true);
    expect(isAllowedMethod("post")).toBe(true);
    expect(isAllowedMethod("TRACE")).toBe(false);
  });
});
