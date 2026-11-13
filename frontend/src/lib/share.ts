/* What a share sheet sent to /share (the manifest's share_target, #52), as
   one string for /api/items to find the link in. Apps differ: some put the
   link in `url`, Instagram's puts it in `text`, a few only in `title`. */
export function sharedText(params: Pick<URLSearchParams, "get">): string {
  return ["url", "text", "title"]
    .map((k) => params.get(k)?.trim())
    .filter(Boolean)
    .join(" ");
}
