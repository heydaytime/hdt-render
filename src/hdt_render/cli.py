import argparse
from pathlib import Path

from .renderer import render_mp4


def main() -> None:
    parser = argparse.ArgumentParser(description="Render an HDT News MP4 from a headline and narration WAV.")
    parser.add_argument("--headline", required=True)
    parser.add_argument("--narration", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--no-nvenc", action="store_true")
    args = parser.parse_args()

    render_mp4(
        headline=args.headline,
        narration_path=args.narration,
        output_path=args.output,
        width=args.width,
        height=args.height,
        fps=args.fps,
        use_nvenc=not args.no_nvenc,
    )


if __name__ == "__main__":
    main()

