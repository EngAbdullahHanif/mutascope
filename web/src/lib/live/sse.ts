/**
 * A Server-Sent Events parser for a `fetch` body (the WHATWG "event stream" format).
 *
 * The scorer reads job streams with `fetch`, not `EventSource`: it must send `Last-Event-ID`
 * itself on every reconnect, see the status code and `Retry-After` of a refused stream, and
 * decide when to give up (ADR 0040). This is the part of `EventSource` it still needs: bytes in
 * any chunking, events out.
 */

export type SseEvent = {
  /** `event:` field; `"message"` when the server sent none. */
  event: string;
  data: string;
  /** `id:` field of this event, or null if it had none. */
  id: string | null;
};

/** Incremental: `push` each decoded chunk, get the events it completed. */
export class SseParser {
  private buffer = "";
  private event = "";
  private data: string[] = [];
  private id: string | null = null;

  push(chunk: string): SseEvent[] {
    this.buffer += chunk;
    const out: SseEvent[] = [];
    // A line ends at CRLF, LF or CR; a CR at the very end may be half of a CRLF, so it waits.
    let match: RegExpExecArray | null;
    const eol = /\r\n|\n|\r(?!$)/g;
    let start = 0;
    while ((match = eol.exec(this.buffer)) !== null) {
      const line = this.buffer.slice(start, match.index);
      start = match.index + match[0].length;
      const event = this.line(line);
      if (event) out.push(event);
    }
    this.buffer = this.buffer.slice(start);
    return out;
  }

  private line(line: string): SseEvent | null {
    if (line === "") return this.dispatch();
    if (line.startsWith(":")) return null; // a comment (keep-alive)
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") this.event = value;
    else if (field === "data") this.data.push(value);
    else if (field === "id" && !value.includes("\0")) this.id = value;
    return null;
  }

  private dispatch(): SseEvent | null {
    const event =
      this.data.length > 0
        ? { event: this.event || "message", data: this.data.join("\n"), id: this.id }
        : null;
    this.event = "";
    this.data = [];
    this.id = null;
    return event;
  }
}
