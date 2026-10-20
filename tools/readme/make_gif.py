"""Turn the Explorer frames (``web/media/explorer-gif.spec.ts``) into the README's GIF.

Usage (``make readme-gif`` runs both steps)::

    uv run python tools/readme/make_gif.py web/.media-frames docs/img/explorer.gif

The frames are captured at 2x and scaled down to ``--width``, so the text stays sharp. All
frames share one 256-colour palette, built from a mosaic of several of them: a palette from a
single frame lacks the colours the others need. Maximum coverage, without dithering, keeps
the effect scale's gradients smooth (median cut banded its red end) and the file small.
"""

import argparse
from pathlib import Path

from PIL import Image


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("frames", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--width", type=int, default=900)
    parser.add_argument("--ms", type=int, default=110, help="per frame")
    parser.add_argument("--hold-ms", type=int, default=1500, help="last frame, before the loop")
    args = parser.parse_args()

    paths = sorted(args.frames.glob("*.png"))
    if not paths:
        raise SystemExit(f"no frames in {args.frames}")
    frames: list[Image.Image] = []
    for path in paths:
        with Image.open(path) as image:
            size = (args.width, round(image.height * args.width / image.width))
            frames.append(image.convert("RGB").resize(size, Image.Resampling.LANCZOS))
    height = frames[0].height

    samples = frames[:: max(1, len(frames) // 6)]
    mosaic = Image.new("RGB", (args.width, height * len(samples)))
    for i, frame in enumerate(samples):
        mosaic.paste(frame, (0, i * height))
    palette = mosaic.quantize(colors=256, method=Image.Quantize.MAXCOVERAGE)
    quantized = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]

    durations = [args.ms] * (len(quantized) - 1) + [args.hold_ms]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    quantized[0].save(
        args.out,
        save_all=True,
        append_images=quantized[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=1,
    )


if __name__ == "__main__":
    main()
