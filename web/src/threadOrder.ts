import type { ThreadMeta } from "./types";

/**
 * The order a chat list is read in: the newest message either person wrote first.
 *
 * `updated_at` alone is not enough — an older import stamped a document with the moment it
 * was imported, which put empty shells at the top — so a chat's own newest message
 * (`last_message_at`, from the server) decides, and a thread with no messages at all comes
 * after every chat somebody actually spoke in.
 */
export function byNewestMessage(a: ThreadMeta, b: ThreadMeta): number {
  const am = a.last_message_at || "";
  const bm = b.last_message_at || "";
  if (!!am !== !!bm) return am ? -1 : 1;
  const ak = am || a.updated_at || "";
  const bk = bm || b.updated_at || "";
  return bk.localeCompare(ak);
}
