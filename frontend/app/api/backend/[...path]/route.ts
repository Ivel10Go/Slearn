import {
  getForwardRequestHeaders,
  getForwardResponseHeaders,
  getUpstreamUrl,
  getPathSegmentsFromRequest,
  isAllowedMethod,
  isValidProxyPath,
} from "@/lib/proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type RouteContext = {
  params: Promise<{ path?: string[] }>;
};

function jsonError(message: string, status: number) {
  return Response.json({ detail: message }, { status, headers: { "Cache-Control": "no-store" } });
}

async function proxyRequest(request: Request, context: RouteContext) {
  if (!isAllowedMethod(request.method)) {
    return jsonError("Diese HTTP-Methode wird nicht unterstützt.", 405);
  }

  const params = await context.params;
  const path = getPathSegmentsFromRequest(params.path);
  if (!isValidProxyPath(path)) {
    return jsonError("Der angeforderte API-Pfad ist ungültig.", 400);
  }
  const upstreamUrl = getUpstreamUrl(path, new URL(request.url));
  if (!upstreamUrl) {
    return jsonError("Die Backend-Konfiguration ist ungültig.", 503);
  }

  const method = request.method.toUpperCase();
  const body = method === "GET" || method === "HEAD" ? undefined : await request.arrayBuffer();

  try {
    const upstreamResponse = await fetch(upstreamUrl, {
      method,
      headers: getForwardRequestHeaders(request),
      body,
      cache: "no-store",
      redirect: "manual",
      signal: request.signal,
    });

    return new Response(upstreamResponse.body, {
      status: upstreamResponse.status,
      statusText: upstreamResponse.statusText,
      headers: getForwardResponseHeaders(upstreamResponse),
    });
  } catch {
    return jsonError("Der Slearn-Backend ist vorübergehend nicht erreichbar.", 503);
  }
}

export async function GET(request: Request, context: RouteContext) {
  return proxyRequest(request, context);
}

export async function HEAD(request: Request, context: RouteContext) {
  return proxyRequest(request, context);
}

export async function POST(request: Request, context: RouteContext) {
  return proxyRequest(request, context);
}

export async function PUT(request: Request, context: RouteContext) {
  return proxyRequest(request, context);
}

export async function PATCH(request: Request, context: RouteContext) {
  return proxyRequest(request, context);
}

export async function DELETE(request: Request, context: RouteContext) {
  return proxyRequest(request, context);
}

export async function OPTIONS(request: Request, context: RouteContext) {
  return proxyRequest(request, context);
}
