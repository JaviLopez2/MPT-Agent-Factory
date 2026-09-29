"""Create a small offline MPT job using a generated test card. No extra packages."""
import json
import struct
import zlib
from pathlib import Path


def main():
    folder = Path(__file__).resolve().parents[1] / "data" / "smoke-inputs"
    folder.mkdir(parents=True, exist_ok=True)
    width, height = 720, 1280
    palette = [(25, 52, 76), (31, 95, 121), (67, 140, 157), (246, 196, 96)]
    rows = b"".join(b"\0" + bytes(palette[min(3, y * 4 // height)]) * width for y in range(height))

    def chunk(kind, value):
        return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value))

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")
    image = folder / "test-card.png"
    image.write_bytes(png)
    job = folder / "local-smoke.json"
    job.write_text(json.dumps({"params": {
        "video_subject": "Prueba técnica local de MPT Agent Factory",
        "video_script": "Esta es una prueba técnica local.",
        "video_source": "local", "video_materials": [{"provider": "local", "url": str(image)}],
        "voice_name": "no-voice", "subtitle_enabled": False, "bgm_type": "", "video_count": 1,
        "video_aspect": "9:16", "video_concat_mode": "sequential", "video_clip_duration": 3,
        "n_threads": 2}, "required_services": []}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(job)


if __name__ == "__main__":
    main()
