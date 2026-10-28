import { render } from "@testing-library/react";

import { countsVisits, GOATCOUNTER } from "@/lib/site";

import { VisitCounter } from "./visit-counter";

const nav = vi.hoisted(() => ({ pathname: "/" }));
vi.mock("next/navigation", () => ({ usePathname: () => nav.pathname }));
// next/script loads nothing in jsdom: render its props, and let the test fire onLoad.
const script = vi.hoisted(() => ({ props: {} as Record<string, unknown> }));
vi.mock("next/script", () => ({
  default: (props: Record<string, unknown>) => {
    script.props = props;
    return null;
  },
}));

describe("VisitCounter", () => {
  afterEach(() => {
    delete window.goatcounter;
    nav.pathname = "/";
  });

  it("loads GoatCounter's pinned script with its own count switched off", () => {
    render(<VisitCounter />);
    expect(script.props.src).toBe(GOATCOUNTER.script);
    expect(script.props.integrity).toMatch(/^sha384-/);
    expect(script.props.crossOrigin).toBe("anonymous");
    expect(script.props["data-goatcounter"]).toBe(GOATCOUNTER.endpoint);
    expect(JSON.parse(script.props["data-goatcounter-settings"] as string)).toEqual({
      no_onload: true,
    });
  });

  it("counts the first page once the script loads, then each new path once", () => {
    nav.pathname = "/explore";
    const view = render(<VisitCounter />);
    const count = vi.fn();
    window.goatcounter = { count };
    (script.props.onLoad as () => void)();
    expect(count).toHaveBeenLastCalledWith({ path: "/explore" });

    nav.pathname = "/score";
    view.rerender(<VisitCounter />);
    expect(count).toHaveBeenLastCalledWith({ path: "/score" });
    view.rerender(<VisitCounter />); // same path again: not counted twice
    expect(count).toHaveBeenCalledTimes(2);
  });

  it("sends the path only, never a query string", () => {
    nav.pathname = "/score";
    render(<VisitCounter />);
    const count = vi.fn();
    window.goatcounter = { count };
    (script.props.onLoad as () => void)();
    const [{ path }] = count.mock.calls[0] as [{ path: string }];
    expect(path).toBe("/score");
    expect(path).not.toContain("?");
  });

  it("counts nothing before the script has loaded", () => {
    expect(() => render(<VisitCounter />)).not.toThrow();
  });
});

describe("countsVisits", () => {
  it("is on only for Vercel's production build", () => {
    expect(countsVisits({ VERCEL_ENV: "production" })).toBe(true);
    expect(countsVisits({ VERCEL_ENV: "preview" })).toBe(false);
    expect(countsVisits({ VERCEL_ENV: "development" })).toBe(false);
    expect(countsVisits({})).toBe(false);
  });
});
