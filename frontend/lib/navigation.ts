export function normalizeHash(hash: string) {
  return hash.startsWith("#") ? hash.slice(1) : hash;
}

export function isNavigationHrefActive(pathname: string, href: string, currentHash: string) {
  const [hrefPath, hrefHash = "overview"] = href.split("#");
  if (pathname !== hrefPath) {
    return false;
  }
  return normalizeHash(currentHash || "#overview") === hrefHash;
}
