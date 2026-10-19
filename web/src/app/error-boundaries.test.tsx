import { fireEvent, render, screen } from "@testing-library/react";

import ErrorPage, { digestOf } from "./error";
import GlobalError from "./global-error";

describe("error.tsx", () => {
  it("says what happened and retries on request", () => {
    const retry = vi.fn();
    render(<ErrorPage error={new Error("boom")} retry={retry} />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Something went wrong");
    // The thrown message is never shown: in production it can carry server details.
    expect(screen.queryByText(/boom/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(retry).toHaveBeenCalledOnce();
  });

  it("shows a server error's digest for matching with the server log", () => {
    const error = Object.assign(new Error("x"), { digest: "123abc" });
    render(<ErrorPage error={error} retry={() => {}} />);
    expect(screen.getByText("123abc")).toBeInTheDocument();
  });

  it("reads a digest only when there is a string one", () => {
    expect(digestOf("thrown string")).toBeNull();
    expect(digestOf(null)).toBeNull();
    expect(digestOf({ digest: 42 })).toBeNull();
    expect(digestOf({ digest: "d" })).toBe("d");
  });
});

describe("global-error.tsx", () => {
  it("renders its own document with a retry and a link home", () => {
    const retry = vi.fn();
    // It renders <html> itself; React allows that inside the test container with a warning.
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    render(<GlobalError error={new Error("root")} retry={retry} />);
    spy.mockRestore();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Something went wrong");
    expect(screen.getByRole("link", { name: "Start page" })).toHaveAttribute("href", "/");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(retry).toHaveBeenCalledOnce();
  });
});
