import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EffectLegend, formatSigned } from "./effect-legend";

describe("EffectLegend", () => {
  it("labels both ends and the neutral midpoint, symmetric around 0", () => {
    render(<EffectLegend maxAbs={1.5} title="ESM-2 650M score" decimals={1} />);
    for (const text of ["−1.5", "0", "+1.5", "Harmful", "Neutral", "Tolerated"]) {
      expect(screen.getByText(text)).toBeInTheDocument();
    }
    expect(screen.getByRole("img")).toHaveAccessibleName(
      "Colour scale for ESM-2 650M score: blue is harmful (−1.5), white is neutral (0), red is tolerated (+1.5).",
    );
    expect(screen.queryByText("Not measured")).toBeNull();
  });

  it("shows the missing swatch on request", () => {
    render(<EffectLegend maxAbs={1} title="DMS score" showMissing />);
    expect(screen.getByText("Not measured")).toBeInTheDocument();
  });
});

describe("EffectLegend around a centre", () => {
  it("labels the centre and clamped ends without a sign, with the given words", () => {
    render(
      <EffectLegend
        maxAbs={0.5}
        center={1.25}
        title="Measured DMS score"
        clamped
        ends={["Less fit", "Assay median", "Fitter"]}
        showWildType
      />,
    );
    for (const text of [
      "≤ 0.75",
      "1.25",
      "≥ 1.75",
      "Less fit",
      "Assay median",
      "Fitter",
      "Wild type",
    ]) {
      expect(screen.getByText(text)).toBeInTheDocument();
    }
    expect(screen.getByRole("img")).toHaveAccessibleName(
      "Colour scale for Measured DMS score: blue is less fit (≤ 0.75), white is assay median (1.25), red is fitter (≥ 1.75).",
    );
  });
});

describe("formatSigned", () => {
  it("uses a true minus and never signs zero", () => {
    expect(formatSigned(-0.5, 2)).toBe("−0.50");
    expect(formatSigned(0.5, 2)).toBe("+0.50");
    expect(formatSigned(-0.001, 2)).toBe("0.00");
    expect(formatSigned(0, 2)).toBe("0.00");
  });
});
