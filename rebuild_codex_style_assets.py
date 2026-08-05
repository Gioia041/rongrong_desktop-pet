"""Rebuild the standalone pet assets from the approved Codex-style sheet.

The generated v2 look/run assets drifted away from Rongrong's anatomy.  This
script uses the packaged Codex pet sheet as the visual source of truth and
creates registered frame folders for the standalone desktop app.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parent
# Optional local source used only when rebuilding the packaged frames. Keep
# this folder out of the public repository; the running app uses the packaged frames.
SOURCE = ROOT / "assets-source" / "spritesheet.webp"
OUT = ROOT / "assets-codex-style"
FRAMES = OUT / "frames"
CELL_W = 192
CELL_H = 208
CANVAS = (CELL_W, CELL_H)
FOOT_ANCHOR_X = 96
GROUND_Y = 203
TARGET_HEAD_SPAN = 80


LOOK_SLOTS = [
    (9, 0),
    (9, 1),
    (9, 2),
    (9, 3),
    (9, 4),
    (9, 5),
    (9, 6),
    (9, 7),
    (10, 0),
    (10, 1),
    (10, 2),
    (10, 3),
    (10, 4),
    (10, 5),
    (10, 6),
    (10, 7),
]


def load_sheet() -> Image.Image:
    if not SOURCE.exists():
        raise FileNotFoundError(
            "未找到 assets-source/spritesheet.webp。该源文件只用于本地重建素材，不是公开运行所需文件。"
        )
    sheet = Image.open(SOURCE).convert("RGBA")
    expected = (CELL_W * 8, CELL_H * 11)
    if sheet.size != expected:
        raise ValueError(f"Expected {expected}, got {sheet.size}")
    return sheet


def cell(sheet: Image.Image, row: int, col: int) -> Image.Image:
    return sheet.crop((col * CELL_W, row * CELL_H, (col + 1) * CELL_W, (row + 1) * CELL_H))


def has_art(image: Image.Image) -> bool:
    return image.getchannel("A").getbbox() is not None


def foot_anchor(image: Image.Image) -> tuple[float, int]:
    alpha = image.getchannel("A")
    box = alpha.getbbox()
    if not box:
        return FOOT_ANCHOR_X, GROUND_Y
    points = []
    for y in range(max(box[1], box[3] - 14), box[3]):
        for x in range(box[0], box[2]):
            value = alpha.getpixel((x, y))
            if value >= 64:
                points.append((x, value))
    if points:
        x = sum(px * value for px, value in points) / sum(value for _, value in points)
    else:
        x = (box[0] + box[2]) / 2
    return x, box[3]


def translate(image: Image.Image, dx: int, dy: int) -> Image.Image:
    if dx == 0 and dy == 0:
        return image.copy()
    canvas = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    canvas.alpha_composite(image, (dx, dy))
    return canvas


def head_span(image: Image.Image) -> int:
    pixels = image.convert("RGBA").load()
    xs = []
    box = image.getchannel("A").getbbox()
    if not box:
        return TARGET_HEAD_SPAN
    for y in range(box[1], min(box[3], 150)):
        for x in range(box[0], box[2]):
            red, green, blue, alpha = pixels[x, y]
            if alpha < 55:
                continue
            eye_pink = red >= 185 and blue >= 135 and red >= green + 18
            head_white = red >= 220 and green >= 205 and blue >= 205
            if eye_pink or head_white:
                xs.append(x)
    return max(xs) - min(xs) + 1 if xs else box[2] - box[0]


def scale_about_feet(image: Image.Image, factor: float) -> Image.Image:
    if abs(factor - 1.0) < 0.01:
        return image.copy()
    foot_x, foot_bottom = foot_anchor(image)
    width = max(1, round(CELL_W * factor))
    height = max(1, round(CELL_H * factor))
    scaled = image.resize((width, height), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    paste_x = round(foot_x - foot_x * factor)
    paste_y = round(foot_bottom - foot_bottom * factor)
    canvas.alpha_composite(scaled, (paste_x, paste_y))
    return canvas


def normalize_head_size(
    image: Image.Image,
    target: int = TARGET_HEAD_SPAN,
    minimum: float = 0.80,
    maximum: float = 1.22,
) -> Image.Image:
    span = max(1, head_span(image))
    factor = max(minimum, min(maximum, target / span))
    return scale_about_feet(image, factor)


def register_to_feet(image: Image.Image) -> Image.Image:
    x, bottom = foot_anchor(image)
    return translate(image, round(FOOT_ANCHOR_X - x), GROUND_Y - bottom)


def normalize_and_register(image: Image.Image, target: int = TARGET_HEAD_SPAN) -> Image.Image:
    return register_to_feet(normalize_head_size(image, target))


def harden_alpha(image: Image.Image, threshold: int = 36) -> Image.Image:
    result = image.copy()
    pixels = bytearray(result.tobytes())
    for pos in range(0, len(pixels), 4):
        red, green, blue, alpha = pixels[pos : pos + 4]
        if alpha and green > red + 20 and green > blue + 20:
            pixels[pos + 1] = max(red, blue) + 4
    result = Image.frombytes("RGBA", result.size, bytes(pixels))
    alpha = image.getchannel("A").point(lambda value: 255 if value >= threshold else 0)
    result.putalpha(alpha)
    return result


def write_frames(name: str, frames: list[Image.Image], register: bool = True, threshold: int = 36) -> None:
    folder = FRAMES / name
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.png"):
        old.unlink()
    for index, frame in enumerate(frames):
        frame = harden_alpha(frame, threshold)
        if register:
            # The source atlas already uses one consistent character scale.
            # Per-frame scale estimation treated a side-facing head or folded
            # antennae as a smaller/larger head and caused visible size pops.
            frame = register_to_feet(frame)
        frame.save(folder / f"{index:02d}.png")


def row_frames(sheet: Image.Image, row: int, limit: int = 8) -> list[Image.Image]:
    frames = []
    for col in range(limit):
        frame = cell(sheet, row, col)
        if has_art(frame):
            frames.append(frame)
    return frames


def preserve_running_sequence(frames: list[Image.Image]) -> list[Image.Image]:
    """Keep the approved atlas gait exactly as drawn, including its real feet."""
    return [frame.copy() for frame in frames]


def stabilise_sad_sequence(frames: list[Image.Image]) -> list[Image.Image]:
    """Keep the visible head width stable while the pose folds toward the feet."""
    if not frames:
        return []
    target = head_span(frames[0])
    return [
        normalize_head_size(frame, target, minimum=0.55, maximum=1.0)
        for frame in frames
    ]


def look_source_for_vector(sheet: Image.Image, dx: int, dy: int) -> Image.Image:
    if dx == 0 and dy == 0:
        return cell(sheet, 0, 0)
    degrees = math.degrees(math.atan2(dx, -dy)) % 360
    index = int(round(degrees / 22.5)) % 16
    row, col = LOOK_SLOTS[index]
    return cell(sheet, row, col)


def full_body_look_frame(source: Image.Image) -> Image.Image:
    # Look cells come from one coherent approved atlas.  Preserve their native
    # scale so left/right eye colour and apparent head size are not altered by
    # repeated resampling; only register the shared foot line.
    return source.copy()


def visible_area(image: Image.Image, threshold: int = 36) -> int:
    """Measure perceived sprite mass without counting transparent edge haze."""
    histogram = image.getchannel("A").histogram()
    return sum(histogram[threshold:])


def balance_horizontal_look_pairs(frames: list[Image.Image]) -> list[Image.Image]:
    """Give matching left/right poses one shared apparent character scale.

    The source sheet's right-facing family contains substantially more visible
    sprite area than the matching left-facing family.  Use the geometric mean
    of each pair as the target so neither side alone dictates the new scale.
    Scaling stays anchored at the feet and therefore cannot move the pet off
    the ground line.
    """
    balanced = [frame.copy() for frame in frames]
    for row in range(5):
        for left_col, right_col in ((0, 4), (1, 3)):
            left_index = row * 5 + left_col
            right_index = row * 5 + right_col
            left_area = max(1, visible_area(balanced[left_index]))
            right_area = max(1, visible_area(balanced[right_index]))
            target_area = math.sqrt(left_area * right_area)
            left_factor = math.sqrt(target_area / left_area)
            right_factor = math.sqrt(target_area / right_area)
            balanced[left_index] = scale_about_feet(balanced[left_index], left_factor)
            balanced[right_index] = scale_about_feet(balanced[right_index], right_factor)
    return balanced


def build_look25(sheet: Image.Image) -> list[Image.Image]:
    frames = []
    for dy in (-2, -1, 0, 1, 2):
        for dx in (-2, -1, 0, 1, 2):
            source = look_source_for_vector(sheet, dx, dy)
            frames.append(full_body_look_frame(source))
    return balance_horizontal_look_pairs(frames)


def make_contact_sheet() -> None:
    tile_w, tile_h = 220, 232
    rows = [
        ("look25", 25),
        ("running-right", 8),
        ("running-left", 8),
        ("bounce", 5),
        ("sad", 8),
        ("thinking", 4),
        ("smile", 5),
        ("startle", 6),
    ]
    total_rows = 0
    for name, count in rows:
        columns = 5 if name == "look25" else 8
        total_rows += math.ceil(count / columns)
    image = Image.new("RGB", (8 * tile_w, total_rows * tile_h), "#eeeeee")
    draw = ImageDraw.Draw(image)
    y_row = 0
    for name, count in rows:
        columns = 5 if name == "look25" else min(8, count)
        folder = FRAMES / name
        for index in range(count):
            frame = Image.open(folder / f"{index:02d}.png").convert("RGBA")
            col = index % columns
            row = y_row + index // columns
            image.paste(frame, (col * tile_w + 14, row * tile_h + 20), frame)
            draw.text((col * tile_w + 8, row * tile_h + 4), f"{name} {index:02d}", fill="#333333")
        y_row += math.ceil(count / columns)
    image.save(OUT / "contact-sheet.png")


def make_running_previews() -> None:
    for name in ("running-right", "running-left", "thinking"):
        frames = []
        for path in sorted((FRAMES / name).glob("*.png")):
            sprite = Image.open(path).convert("RGBA")
            bg = Image.new("RGB", CANVAS, "#eeeeee")
            bg.paste(sprite, (0, 0), sprite)
            frames.append(bg)
        frames[0].save(
            OUT / f"{name}-preview.gif",
            save_all=True,
            append_images=frames[1:],
            duration=180 if "running" in name else 650,
            loop=0,
        )


def main() -> None:
    sheet = load_sheet()
    FRAMES.mkdir(parents=True, exist_ok=True)

    write_frames("idle", row_frames(sheet, 0, 7))
    write_frames("look25", build_look25(sheet))
    write_frames("running-right", preserve_running_sequence(row_frames(sheet, 1, 8)), register=False)
    write_frames("running-left", preserve_running_sequence(row_frames(sheet, 2, 8)), register=False)
    write_frames("bounce", row_frames(sheet, 4, 5))
    write_frames("sad", stabilise_sad_sequence(row_frames(sheet, 5, 8)))
    write_frames("thinking", [cell(sheet, 0, 0), cell(sheet, 0, 1), cell(sheet, 0, 2), cell(sheet, 0, 1)])
    write_frames("smile", [cell(sheet, 8, 0), cell(sheet, 8, 1), cell(sheet, 8, 2), cell(sheet, 8, 1), cell(sheet, 8, 0)])
    write_frames("startle", [cell(sheet, 6, 0), cell(sheet, 6, 1), cell(sheet, 6, 2), cell(sheet, 6, 3), cell(sheet, 6, 4), cell(sheet, 6, 5)])
    make_contact_sheet()
    make_running_previews()
    print(OUT)


if __name__ == "__main__":
    main()
