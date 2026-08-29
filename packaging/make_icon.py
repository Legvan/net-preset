"""Draw the application icon and write assets/net-preset.ico.

This script is the source of the artwork, not a copy of it. Pillow will not load
on the development machine -- Smart App Control blocks its DLL -- so the icon is
rasterised here with the standard library: flat rectangles supersampled four
times and box-downsampled, which is all a shape this simple needs.

The drawing is an RJ45 plug on a rounded plate. The plug is the author's own
design, its rectangles carried over from the Inkscape original unchanged; the
plate exists because the plug is black and Windows 11's taskbar is dark, and a
black-on-dark icon is one nobody can see.

Two drawings, not one. From 32 pixels up the plug keeps all eight contacts. At
24 and below they land under two pixels each and merge into a smear, so those
sizes get a simpler plug with four wider contacts. An ICO is a container of
independent images, and this is what that is for.

Run it after changing anything here:

    uv run python packaging/make_icon.py
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TARGET = REPO / "assets" / "net-preset.ico"

SUPERSAMPLE = 4

PLATE = (32, 148, 148, 255)  # teal: legible on a dark taskbar and on a light one
BODY = (0, 0, 0, 255)
CONTACT = (240, 196, 60, 255)  # gold rather than pure yellow, which vibrates on black

# The author's rectangles, in the coordinate system of the original drawing.
PLUG_BOX = (209.11104, 236.29312, 251.22368, 184.83328)
PLUG_BODY = (209.11104, 264.29312, 251.22368, 156.83328)
PLUG_LATCH = (259.93924, 236.29312, 149.56729, 156.83328)
PLUG_CONTACTS = [
    (x, 344.354, 12.90465, 63.128086)
    for x in (
        230.52382,
        258.45148,
        286.37909,
        314.30676,
        342.23441,
        370.16205,
        398.08966,
        426.01727,
    )
]

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
DETAILED_FROM = 32


class Canvas:
    """An RGBA raster with a scanline polygon fill, drawn oversized."""

    def __init__(self, size: int) -> None:
        self.scale = size * SUPERSAMPLE
        self.pixels = [[(0, 0, 0, 0)] * self.scale for _ in range(self.scale)]

    def _blend(self, x: int, y: int, colour: tuple[int, int, int, int]) -> None:
        if not (0 <= x < self.scale and 0 <= y < self.scale):
            return
        red, green, blue, alpha = colour
        if alpha == 0:
            return
        was_red, was_green, was_blue, was_alpha = self.pixels[y][x]
        front = alpha / 255
        self.pixels[y][x] = (
            round(red * front + was_red * (1 - front)),
            round(green * front + was_green * (1 - front)),
            round(blue * front + was_blue * (1 - front)),
            round(alpha + was_alpha * (1 - front)),
        )

    def polygon(self, points, colour) -> None:
        scaled = [(x * SUPERSAMPLE, y * SUPERSAMPLE) for x, y in points]
        all_y = [point[1] for point in scaled]
        for y in range(max(0, int(min(all_y))), min(self.scale, int(max(all_y)) + 1)):
            crossings = []
            for index in range(len(scaled)):
                x1, y1 = scaled[index]
                x2, y2 = scaled[(index + 1) % len(scaled)]
                if (y1 <= y < y2) or (y2 <= y < y1):
                    crossings.append(x1 + (y - y1) * (x2 - x1) / (y2 - y1))
            crossings.sort()
            for pair in range(0, len(crossings) - 1, 2):
                for x in range(int(crossings[pair]), int(crossings[pair + 1]) + 1):
                    self._blend(x, y, colour)

    def rect(self, x, y, width, height, colour, radius: float = 0) -> None:
        if radius <= 0:
            self.polygon(
                [(x, y), (x + width, y), (x + width, y + height), (x, y + height)], colour
            )
            return
        corners = (
            (x + width - radius, y + radius, -90),
            (x + width - radius, y + height - radius, 0),
            (x + radius, y + height - radius, 90),
            (x + radius, y + radius, 180),
        )
        points = []
        for centre_x, centre_y, start in corners:
            for step in range(13):
                angle = math.radians(start + step * 90 / 12)
                points.append(
                    (centre_x + radius * math.cos(angle), centre_y + radius * math.sin(angle))
                )
        self.polygon(points, colour)

    def rows(self, size: int):
        out = []
        for y in range(size):
            row = []
            for x in range(size):
                total = [0, 0, 0, 0]
                for down in range(SUPERSAMPLE):
                    for across in range(SUPERSAMPLE):
                        pixel = self.pixels[y * SUPERSAMPLE + down][x * SUPERSAMPLE + across]
                        for channel in range(4):
                            total[channel] += pixel[channel]
                row.append(tuple(value // (SUPERSAMPLE * SUPERSAMPLE) for value in total))
            out.append(row)
        return out


def plate(canvas: Canvas, size: int) -> None:
    inset = size * 0.055
    canvas.rect(inset, inset, size - 2 * inset, size - 2 * inset, PLATE, radius=size * 0.215)


def detailed_plug(canvas: Canvas, size: int) -> None:
    """The author's drawing, scaled onto the plate."""
    box_x, box_y, box_width, box_height = PLUG_BOX
    factor = size * 0.66 / box_width
    left = (size - box_width * factor) / 2
    top = (size - box_height * factor) / 2
    shapes = [(PLUG_BODY, BODY), (PLUG_LATCH, BODY)]
    shapes += [(contact, CONTACT) for contact in PLUG_CONTACTS]
    for (x, y, width, height), colour in shapes:
        canvas.rect(
            left + (x - box_x) * factor,
            top + (y - box_y) * factor,
            width * factor,
            height * factor,
            colour,
        )


def simple_plug(canvas: Canvas, size: int) -> None:
    """The same plug with four contacts, for sizes where eight would smear."""
    unit = size / 16
    canvas.rect(3.0 * unit, 6.0 * unit, 10.0 * unit, 6.0 * unit, BODY)
    canvas.rect(5.5 * unit, 4.0 * unit, 5.0 * unit, 2.6 * unit, BODY)
    for index in range(4):
        canvas.rect((4.2 + index * 2.1) * unit, 8.0 * unit, 1.2 * unit, 3.0 * unit, CONTACT)


def draw(size: int):
    canvas = Canvas(size)
    plate(canvas, size)
    (detailed_plug if size >= DETAILED_FROM else simple_plug)(canvas, size)
    return canvas.rows(size)


def png(rows) -> bytes:
    height, width = len(rows), len(rows[0])
    raw = b"".join(
        b"\x00" + b"".join(struct.pack("BBBB", *pixel) for pixel in row) for row in rows
    )

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def dib(rows) -> bytes:
    """One icon entry in the older BITMAPINFOHEADER form.

    Only 128 and 256 are stored as PNG below. Everything smaller goes out as a
    device-independent bitmap because that is what an icon file has always held
    and what every toolchain reads without complaint: GDI+ on this machine
    refuses ToBitmap on a PNG entry at some sizes while reading the same entry
    happily at others, and an icon that a tool declines is worse than a larger
    file.

    The header claims twice the real height. An icon carries two stacked
    images, colour over mask, and the height field covers both. Rows run bottom
    to top. The mask is left all zeros: at 32 bits a pixel the alpha channel
    already says what is transparent, but the mask has to be there and has to be
    the right size.
    """
    height, width = len(rows), len(rows[0])
    header = struct.pack(
        "<IiiHHIIiiII", 40, width, height * 2, 1, 32, 0, width * height * 4, 0, 0, 0, 0
    )
    colour = b"".join(
        b"".join(struct.pack("BBBB", pixel[2], pixel[1], pixel[0], pixel[3]) for pixel in row)
        for row in reversed(rows)
    )
    mask_stride = ((width + 31) // 32) * 4
    return header + colour + b"\x00" * (mask_stride * height)


def main() -> None:
    images = {
        size: (png(draw(size)) if size >= 128 else dib(draw(size))) for size in SIZES
    }
    directory, blobs = [], []
    offset = 6 + 16 * len(images)
    for size in SIZES:
        blob = images[size]
        # Zero means 256 in an icon directory: the field is a single byte.
        stored = 0 if size >= 256 else size
        directory.append(
            struct.pack("<BBBBHHII", stored, stored, 0, 0, 1, 32, len(blob), offset)
        )
        blobs.append(blob)
        offset += len(blob)

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    with TARGET.open("wb") as handle:
        handle.write(struct.pack("<HHH", 0, 1, len(images)))
        for entry in directory:
            handle.write(entry)
        for blob in blobs:
            handle.write(blob)
    print(f"{TARGET}  {TARGET.stat().st_size} bytes  sizes {', '.join(map(str, SIZES))}")


if __name__ == "__main__":
    main()
