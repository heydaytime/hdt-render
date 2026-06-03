import math
import struct
import wave
from pathlib import Path


def main() -> None:
    out = Path("tmp/test-narration.wav")
    out.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 24_000
    duration = 4.0
    samples = int(sample_rate * duration)
    with wave.open(str(out), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        for index in range(samples):
            value = int(math.sin(index / sample_rate * math.tau * 220) * 12_000)
            wav.writeframes(struct.pack("<h", value))
    print(out)


if __name__ == "__main__":
    main()
