/** Offline outbox: messages written while the runtime is unreachable are kept on this
 *  device and sent, in order, the moment it is reachable again. Retries reuse the same
 *  event id, and the server hands an already-landed event back instead of running the
 *  turn twice, so flushing is safe to repeat.
 */

const KEY = "nanomuse_outbox";

export interface OutboxItem {
  /** the client event id (also the server event id, so retries dedupe) */
  id: string;
  thread: string;
  text: string;
  files: string[];
  language: string;
  via: string;
  at: string;
}

function read(): OutboxItem[] {
  try {
    const raw = localStorage.getItem(KEY);
    const parsed = raw ? (JSON.parse(raw) as unknown) : [];
    return Array.isArray(parsed) ? (parsed as OutboxItem[]) : [];
  } catch {
    return [];
  }
}

function write(items: OutboxItem[]): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(items));
  } catch {
    /* storage full or unavailable: the queue just stays in memory */
  }
}

export function outbox(): OutboxItem[] {
  return read();
}

export function enqueue(item: OutboxItem): void {
  const items = read().filter((i) => i.id !== item.id);
  items.push(item);
  write(items);
}

export function dequeue(id: string): void {
  write(read().filter((i) => i.id !== id));
}

export function outboxCount(): number {
  return read().length;
}
