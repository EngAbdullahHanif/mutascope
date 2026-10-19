"use client";

import { CircleAlert, CircleCheck, Info, LoaderCircle, RefreshCw, Square } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { formatCount } from "@/lib/explorer/landscape";
import { ApiError, LiveClient, type JobResponse, type Ready } from "@/lib/live/client";
import { followJob, type Connection } from "@/lib/live/follow";
import { applyRows, emptyGrid, gridFrom } from "@/lib/live/landscape";
import { formatWait, friendlyError } from "@/lib/live/messages";
import { EXAMPLES, checkSequence, preciseLengthError } from "@/lib/live/sequence";
import { wake } from "@/lib/live/wake";

import { LiveResult, type LiveResultData } from "./live-result";

type Mode = "fast" | "precise";

type Server =
  | { state: "checking" }
  | { state: "waking" }
  | { state: "ready"; ready: Ready }
  | { state: "unreachable"; message: string };

type Job = { id: string; status: JobResponse["status"]; connection: Connection };

type Notice = { tone: "error" | "info"; message: string; retryUntil: number | null };

/** A fast request still unanswered after this is probably waiting for an instance to start. */
const SLOW_MS = 3000;

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

/** Re-renders every second while `active`, for the Retry-After countdown. */
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const tick = () => setNow(Date.now());
    const first = setTimeout(tick, 0);
    const timer = setInterval(tick, 1000);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [active]);
  return now;
}

/**
 * Score any sequence live (ADR 0031, 0040): fast mode is one request, precise mode a job
 * followed over Server-Sent Events. The sequence stays in this component's state: it is sent
 * only in a request body, never put in the URL, storage or a log, and the textarea has no
 * `name`, so even a form submitted without JavaScript carries nothing.
 */
export function LiveScorer() {
  const client = useMemo(() => new LiveClient(), []);
  const [server, setServer] = useState<Server>({ state: "checking" });
  const [wakeCount, setWakeCount] = useState(0);
  const [text, setText] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [apiInputError, setApiInputError] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("fast");
  const [busy, setBusy] = useState<Mode | null>(null);
  const [slow, setSlow] = useState(false);
  const [result, setResult] = useState<LiveResultData | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const run = useRef<AbortController | null>(null);
  const activeJob = useRef<string | null>(null);

  // Ping the API as soon as the page loads: a cold start (~20 s) overlaps the reader's typing.
  useEffect(() => {
    const ctl = new AbortController();
    wake(client, ctl.signal, { onWaking: () => setServer({ state: "waking" }) })
      .then((ready) => setServer({ state: "ready", ready }))
      .catch((error: unknown) => {
        if (ctl.signal.aborted) return;
        const message =
          error instanceof ApiError
            ? friendlyError(error).message
            : "The scoring server did not answer.";
        setServer({ state: "unreachable", message });
      });
    return () => ctl.abort();
  }, [client, wakeCount]);

  // Leaving the page cancels a running job: it would hold the client's one job slot, and it
  // cannot advance without an open stream anyway. Best effort (keepalive outlives the page).
  useEffect(() => {
    const leave = () => {
      const id = activeJob.current;
      if (id) client.cancelJob(id, { keepalive: true }).catch(() => undefined);
      activeJob.current = null;
      run.current?.abort();
    };
    window.addEventListener("pagehide", leave);
    return () => {
      window.removeEventListener("pagehide", leave);
      leave();
    };
  }, [client]);

  const check = useMemo(() => checkSequence(text), [text]);
  const limit = server.state === "ready" ? server.ready.precise_max_length : null;
  const preciseError =
    mode === "precise" && check.ok && limit !== null
      ? preciseLengthError(check.sequence.length, limit)
      : null;
  const inputError =
    apiInputError ?? (submitted && !check.ok ? check.message : null) ?? preciseError;

  const now = useNow(notice?.retryUntil != null);
  const waitLeft =
    notice?.retryUntil != null ? Math.max(0, Math.ceil((notice.retryUntil - now) / 1000)) : 0;

  const fail = useCallback((error: unknown) => {
    if (error instanceof ApiError) {
      const f = friendlyError(error);
      if (f.inputError) {
        setApiInputError(f.inputError);
        return;
      }
      setNotice({
        tone: "error",
        message: f.message,
        retryUntil: f.retryAfter !== null ? Date.now() + f.retryAfter * 1000 : null,
      });
      return;
    }
    setNotice({
      tone: "error",
      message: `Something went wrong: ${error instanceof Error ? error.message : String(error)}`,
      retryUntil: null,
    });
  }, []);

  const runFast = async (sequence: string, signal: AbortSignal) => {
    const timer = setTimeout(() => setSlow(true), SLOW_MS);
    try {
      const r = await client.score(sequence, signal);
      if (!r.scores) throw new Error("the server returned no landscape");
      setResult({
        sequence: r.target_seq,
        model: r.model,
        method: r.method,
        grid: gridFrom(r.scores, r.length),
        done: r.length,
        total: r.length,
        complete: true,
      });
    } finally {
      clearTimeout(timer);
      setSlow(false);
    }
  };

  const runPrecise = async (sequence: string, signal: AbortSignal) => {
    const created = await client.createJob(sequence, signal);
    const base = {
      sequence: created.target_seq,
      model: created.model,
      method: created.method,
      total: created.total,
    };
    setJob({ id: created.id, status: created.status, connection: { state: "connecting" } });
    if (created.status === "succeeded" && created.scores) {
      // An identical earlier job: the server answered from its cache, nothing to follow.
      setResult({
        ...base,
        grid: gridFrom(created.scores, created.length),
        done: created.total,
        complete: true,
      });
      return;
    }
    setResult({ ...base, grid: emptyGrid(created.length), done: 0, complete: false });
    activeJob.current = created.id;
    const final = await followJob(
      client,
      created.id,
      {
        onStatus: (j) => setJob((cur) => cur && { ...cur, status: j.status }),
        onProgress: (p) =>
          setResult(
            (cur) =>
              cur && { ...cur, grid: applyRows(cur.grid, p.rows), done: p.done, total: p.total },
          ),
        onConnection: (connection) => setJob((cur) => cur && { ...cur, connection }),
      },
      signal,
    );
    activeJob.current = null;
    setJob((cur) => cur && { ...cur, status: final.status });
    if (final.status === "succeeded" && final.scores) {
      const scores = final.scores;
      setResult((cur) => ({
        ...(cur ?? base),
        grid: gridFrom(scores, final.length),
        done: final.total,
        total: final.total,
        complete: true,
      }));
    } else if (final.status === "cancelled") {
      setNotice(cancelledNotice(final));
    } else {
      setNotice({
        tone: "error",
        message: `The job failed on the server${final.error ? `: ${final.error}` : "."}`,
        retryUntil: null,
      });
    }
  };

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setSubmitted(true);
    setApiInputError(null);
    if (!check.ok || preciseError || busy) return;
    run.current?.abort();
    const ctl = new AbortController();
    run.current = ctl;
    setNotice(null);
    setResult(null);
    setJob(null);
    setBusy(mode);
    try {
      await (mode === "fast" ? runFast : runPrecise)(check.sequence, ctl.signal);
    } catch (error) {
      if (!isAbort(error)) fail(error);
    } finally {
      if (run.current === ctl) {
        run.current = null;
        activeJob.current = null;
        setBusy(null);
      }
    }
  };

  const cancel = async () => {
    if (!job) return;
    setCancelling(true);
    try {
      const j = await client.cancelJob(job.id);
      activeJob.current = null;
      run.current?.abort();
      setJob((cur) => cur && { ...cur, status: j.status });
      setNotice(cancelledNotice(j));
    } catch (error) {
      // 409: it finished first; its stream delivers the result.
      if (!(error instanceof ApiError && error.status === 409)) fail(error);
    } finally {
      setCancelling(false);
    }
  };

  const loadExample = (sequence: string) => {
    setText(sequence);
    setApiInputError(null);
  };

  const ready = server.state === "ready";
  const blocked = !ready || busy !== null || waitLeft > 0;
  const submitLabel =
    busy !== null
      ? "Scoring…"
      : server.state === "waking" || server.state === "checking"
        ? "Waking up the server…"
        : waitLeft > 0
          ? `Try again in ${waitLeft} s`
          : mode === "fast"
            ? "Score (fast)"
            : "Start precise job";

  return (
    <div className="flex flex-col gap-8">
      <ServerStatus
        server={server}
        onRetry={() => {
          setServer({ state: "checking" });
          setWakeCount((n) => n + 1);
        }}
      />

      <form onSubmit={onSubmit} className="flex max-w-3xl flex-col gap-4" noValidate>
        <div className="flex flex-col gap-1.5">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <label htmlFor="sequence" className="font-medium">
              Protein sequence
            </label>
            <div className="flex flex-wrap items-center gap-1.5 text-sm">
              <span className="text-muted-foreground">Examples:</span>
              {EXAMPLES.map((ex) => (
                <Button
                  key={ex.assayId}
                  type="button"
                  variant="outline"
                  size="xs"
                  onClick={() => loadExample(ex.sequence)}
                  disabled={busy !== null}
                >
                  {ex.name}
                </Button>
              ))}
            </div>
          </div>
          <textarea
            id="sequence"
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              setApiInputError(null);
            }}
            rows={6}
            autoComplete="off"
            autoCorrect="off"
            autoCapitalize="characters"
            spellCheck={false}
            placeholder="MQIFVKTLTGKTITLEVE… (one-letter codes; a FASTA header line is fine)"
            aria-invalid={inputError ? true : undefined}
            aria-describedby="sequence-help sequence-error"
            className="w-full rounded-md border bg-background p-3 font-mono text-sm break-all outline-none focus-visible:ring-2 focus-visible:ring-ring aria-invalid:border-destructive"
            disabled={busy !== null}
          />
          <p id="sequence-help" className="text-xs text-muted-foreground">
            {check.ok
              ? `${formatCount(check.sequence.length)} residues.`
              : "The 20 canonical amino acids, 1–1,022 residues."}{" "}
            Whitespace and a FASTA header are removed, and letters are uppercased. The sequence is
            sent only to the scoring server, and is not stored in the page address or logged.
          </p>
          <p
            id="sequence-error"
            role="alert"
            className="flex items-start gap-1.5 text-sm text-destructive empty:hidden"
            data-testid="sequence-error"
          >
            {inputError ? (
              <>
                <CircleAlert aria-hidden className="mt-0.5 size-4 shrink-0" />
                {inputError}
              </>
            ) : null}
          </p>
        </div>

        <fieldset className="flex flex-col gap-2" disabled={busy !== null}>
          <legend className="mb-1 font-medium">Mode</legend>
          <ModeOption
            value="fast"
            current={mode}
            onChange={setMode}
            title="Fast"
            text="Wild-type marginal: one forward pass, a few seconds. Any length up to 1,022."
          />
          <ModeOption
            value="precise"
            current={mode}
            onChange={setMode}
            title="Precise"
            text={`Masked marginal: one pass per residue, filled in as it runs (a few minutes). ${
              limit !== null
                ? `Up to ${formatCount(limit)} residues on this server.`
                : "Length limit: asking the server…"
            }`}
          />
        </fieldset>

        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" disabled={blocked} data-testid="score-button">
            {busy !== null || server.state === "waking" ? (
              <LoaderCircle aria-hidden className="animate-spin" />
            ) : null}
            {submitLabel}
          </Button>
          {busy === "precise" && job && job.status !== "cancelled" ? (
            <Button
              type="button"
              variant="outline"
              onClick={cancel}
              disabled={cancelling}
              data-testid="cancel-button"
            >
              <Square aria-hidden />
              {cancelling ? "Cancelling…" : "Cancel"}
            </Button>
          ) : null}
          {busy === "fast" && slow ? (
            <span className="text-sm text-muted-foreground" role="status">
              Still scoring: the server may be waking up (about 20 seconds).
            </span>
          ) : null}
        </div>
      </form>

      {notice ? <NoticeBox notice={notice} waitLeft={waitLeft} /> : null}

      {busy === "precise" && job && result ? <Progress job={job} result={result} /> : null}

      {result ? <LiveResult data={result} /> : null}
    </div>
  );
}

function cancelledNotice(job: JobResponse): Notice {
  return {
    tone: "info",
    message:
      job.done > 0
        ? `Cancelled after ${formatCount(job.done)} of ${formatCount(job.total)} residues. The rows scored so far are shown; the rest stay grey.`
        : "Cancelled before any residue was scored.",
    retryUntil: null,
  };
}

function ModeOption({
  value,
  current,
  onChange,
  title,
  text,
}: {
  value: Mode;
  current: Mode;
  onChange: (m: Mode) => void;
  title: string;
  text: string;
}) {
  return (
    <label className="flex cursor-pointer items-start gap-2 rounded-md border p-3 text-sm has-checked:border-primary has-focus-visible:ring-2 has-focus-visible:ring-ring">
      <input
        type="radio"
        name="mode"
        value={value}
        checked={current === value}
        onChange={() => onChange(value)}
        className="mt-1 size-4 accent-primary"
      />
      <span>
        <span className="font-medium">{title}</span>
        <span className="block text-muted-foreground">{text}</span>
      </span>
    </label>
  );
}

function ServerStatus({ server, onRetry }: { server: Server; onRetry: () => void }) {
  const box = "flex max-w-3xl items-start gap-2 rounded-md border p-3 text-sm";
  return (
    <div data-testid="server-status" data-state={server.state} role="status" aria-live="polite">
      {server.state === "checking" ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <LoaderCircle aria-hidden className="size-4 animate-spin" />
          Contacting the scoring server…
        </p>
      ) : server.state === "waking" ? (
        <p className={`${box} bg-muted/50`}>
          <LoaderCircle aria-hidden className="mt-0.5 size-4 shrink-0 animate-spin" />
          <span>
            <span className="font-medium">Waking up the server…</span> It sleeps when nobody is
            using it, and starting takes about 20 seconds. You can paste a sequence meanwhile.
          </span>
        </p>
      ) : server.state === "ready" ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <CircleCheck aria-hidden className="size-4 text-primary" />
          Server ready.
        </p>
      ) : (
        <div className={`${box} border-destructive/50`}>
          <CircleAlert aria-hidden className="mt-0.5 size-4 shrink-0 text-destructive" />
          <div className="flex flex-col items-start gap-2">
            <span>{server.message}</span>
            <Button type="button" variant="outline" size="xs" onClick={onRetry}>
              <RefreshCw aria-hidden />
              Try again
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

function NoticeBox({ notice, waitLeft }: { notice: Notice; waitLeft: number }) {
  const error = notice.tone === "error";
  return (
    <div
      role={error ? "alert" : "status"}
      data-testid="live-notice"
      className={`flex max-w-3xl items-start gap-2 rounded-md border p-3 text-sm ${error ? "border-destructive/50" : "bg-muted/50"}`}
    >
      {error ? (
        <CircleAlert aria-hidden className="mt-0.5 size-4 shrink-0 text-destructive" />
      ) : (
        <Info aria-hidden className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
      )}
      <span>
        {notice.message}
        {notice.retryUntil !== null && waitLeft > 0 ? (
          <span className="text-muted-foreground"> ({formatWait(waitLeft)})</span>
        ) : null}
      </span>
    </div>
  );
}

function Progress({ job, result }: { job: Job; result: LiveResultData }) {
  const pct = result.total > 0 ? (100 * result.done) / result.total : 0;
  const c = job.connection;
  return (
    <div className="flex max-w-3xl flex-col gap-1.5" data-testid="job-progress">
      <div className="flex flex-wrap justify-between gap-2 text-sm">
        <span className="font-medium">
          {job.status === "queued"
            ? "Queued behind another job…"
            : job.status === "running"
              ? "Scoring residue by residue…"
              : "Starting…"}
        </span>
        <span className="tabular font-mono text-muted-foreground">
          {formatCount(result.done)} / {formatCount(result.total)} residues
        </span>
      </div>
      <div
        role="progressbar"
        aria-label="Residues scored"
        aria-valuemin={0}
        aria-valuemax={result.total}
        aria-valuenow={result.done}
        className="h-2 overflow-hidden rounded-sm bg-muted"
      >
        <div className="h-full bg-primary transition-[width]" style={{ width: `${pct}%` }} />
      </div>
      <p
        className="text-xs text-muted-foreground"
        role="status"
        data-testid="connection"
        data-state={c.state}
      >
        {c.state === "reconnecting"
          ? `Connection lost. Reconnecting (attempt ${c.attempt}) in ${Math.max(1, Math.round(c.delayMs / 1000))} s… The job only advances while connected.`
          : c.state === "open"
            ? "Connected. Keep this page open: the job advances only while it is."
            : "Connecting…"}
      </p>
    </div>
  );
}
