import { act, render, screen } from "@testing-library/react";
import { useRef } from "react";

import { useInView } from "./use-in-view";

type Callback = (entries: { isIntersecting: boolean }[]) => void;

let observers: { callback: Callback; disconnected: boolean }[] = [];

class FakeObserver {
  private readonly record: { callback: Callback; disconnected: boolean };
  constructor(callback: Callback) {
    this.record = { callback, disconnected: false };
    observers.push(this.record);
  }
  observe() {}
  disconnect() {
    this.record.disconnected = true;
  }
}

function Probe() {
  const ref = useRef<HTMLDivElement>(null);
  const seen = useInView(ref);
  return <div ref={ref}>{seen ? "seen" : "not yet"}</div>;
}

describe("useInView", () => {
  beforeEach(() => {
    observers = [];
    vi.stubGlobal("IntersectionObserver", FakeObserver);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("turns true when the element is in view, then stays true and stops observing", () => {
    render(<Probe />);
    expect(screen.getByText("not yet")).toBeInTheDocument();
    act(() => observers[0]!.callback([{ isIntersecting: false }]));
    expect(screen.getByText("not yet")).toBeInTheDocument();
    act(() => observers[0]!.callback([{ isIntersecting: true }]));
    expect(screen.getByText("seen")).toBeInTheDocument();
    expect(observers[0]!.disconnected).toBe(true);
    expect(observers).toHaveLength(1);
  });

  it("counts as in view where IntersectionObserver does not exist", async () => {
    vi.stubGlobal("IntersectionObserver", undefined);
    render(<Probe />);
    expect(await screen.findByText("seen")).toBeInTheDocument();
  });
});
