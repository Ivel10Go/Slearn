const API_VERSION = "v1";
const ALLOWED_METHODS = new Set(["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]);
const REQUEST_HEADERS_TO_DROP = new Set([
  "connection",
  "content-length",
  "expect",
  "host",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
]);
const RESPONSE_HEADERS_TO_DROP = new Set([
  "access-control-allow-credentials",
  "access-control-allow-headers",
  "access-control-allow-methods",
  "access-control-allow-origin",
  "connection",
  "content-encoding",
  "content-length",
  "content-security-policy",
  "keep-alive",
  "location",
  "permissions-policy",
  "referrer-policy",
  "refresh",
  "strict-transport-security",
  "transfer-encoding",
  "upgrade",
  "x-content-type-options",
  "x-frame-options",
]);

function getUpstreamBase() {
  const configuredUrl = process.env.BACKEND_INTERNAL_URL ?? "http://localhost:8000";
  let base: URL;
  try {
    base = new URL(configuredUrl);
  } catch {
    return null;
  }
  if ((base.protocol !== "http:" && base.protocol !== "https:") || base.username || base.password) {
    return null;
  }
  base.pathname = base.pathname.replace(/\/+$/, "");
  base.search = "";
  base.hash = "";
  return base;
}

function isSafeSegment(segment: string) {
  if (!segment || segment === "." || segment === ".." || segment.includes("/") || segment.includes("\\") || segment.includes("\0")) {
    return false;
  }
  if (/%2f|%5c|%00/i.test(segment)) {
    return false;
  }
  let decoded = segment;
  try {
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const next = decodeURIComponent(decoded);
      if (next === decoded) {
        break;
      }
      decoded = next;
      if (decoded === "." || decoded === ".." || decoded.includes("/") || decoded.includes("\\") || decoded.includes("\0")) {
        return false;
      }
    }
    return decoded !== "." && decoded !== ".." && !decoded.includes("/") && !decoded.includes("\\") && !decoded.includes("\0");
  } catch {
    return false;
  }
}

function getPathSegments(value: string | string[] | undefined) {
  if (Array.isArray(value)) {
    return value;
  }
  if (typeof value !== "string") {
    return [];
  }
  const normalized = value.replace(/^\/+|\/+$/g, "");
  return normalized ? normalized.split("/") : [];
}

export function isValidProxyPath(pathSegments: string[]) {
  return pathSegments[0] === API_VERSION && pathSegments.every(isSafeSegment);
}

export function getUpstreamUrl(pathSegments: string[], requestUrl: URL) {
  if (!isValidProxyPath(pathSegments)) {
    return null;
  }
  const base = getUpstreamBase();
  if (!base) {
    return null;
  }
  const suffix = pathSegments.slice(1).map((segment) => encodeURIComponent(segment)).join("/");
  const basePath = base.pathname === "/" ? "" : base.pathname;
  const upstream = new URL(`${base.origin}${basePath}/api/${API_VERSION}${suffix ? `/${suffix}` : ""}`);
  upstream.search = requestUrl.search;
  return upstream;
}

export function getForwardRequestHeaders(request: Request) {
  const headers = new Headers();
  request.headers.forEach((value, key) => {
    if (!REQUEST_HEADERS_TO_DROP.has(key.toLowerCase())) {
      headers.set(key, value);
    }
  });
  return headers;
}

export function getForwardResponseHeaders(response: Response) {
  const headers = new Headers();
  const setCookie = typeof response.headers.getSetCookie === "function" ? response.headers.getSetCookie() : [];
  response.headers.forEach((value, key) => {
    const normalizedKey = key.toLowerCase();
    if (!RESPONSE_HEADERS_TO_DROP.has(normalizedKey) && (normalizedKey !== "set-cookie" || setCookie.length === 0)) {
      headers.set(key, value);
    }
  });
  for (const cookie of setCookie) {
    headers.append("set-cookie", cookie);
  }
  headers.set("Cache-Control", "no-store");
  return headers;
}

export function isAllowedMethod(method: string) {
  return ALLOWED_METHODS.has(method.toUpperCase());
}

export function getPathSegmentsFromRequest(path: string | string[] | undefined) {
  return getPathSegments(path);
}
