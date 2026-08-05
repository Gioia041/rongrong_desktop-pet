import ctypes
import json
import math
import random
import time
import tkinter as tk
from collections import deque
from pathlib import Path

from PIL import Image, ImageDraw, ImageGrab, ImageStat, ImageTk


APP_DIR = Path(__file__).resolve().parent
CODEX_STYLE_ASSET_DIR = APP_DIR / "assets-codex-style" / "frames"
SETTINGS_PATH = APP_DIR / "settings.json"
TRANSPARENT = "#00ff66"
TICK_MS = 85

# Every source image is placed on this same canvas.  The point is roughly the
# middle of 绒绒's head, rather than the centre of the body or tail.
CANVAS_SIZE = (192, 208)
HEAD_ANCHOR = (96, 101)
GROUND_Y = 203


def active_window_title():
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value.strip()
    except Exception:
        return ""


def read_settings():
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def read_scale():
    value = float(read_settings().get("scale", 1.0))
    return max(0.55, min(1.65, value))


def save_scale(scale):
    try:
        settings = read_settings()
        settings["scale"] = round(scale, 2)
        SETTINGS_PATH.write_text(json.dumps(settings, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def save_bubble_offset(x, y):
    try:
        settings = read_settings()
        settings["bubble_offset"] = [round(x), round(y)]
        SETTINGS_PATH.write_text(json.dumps(settings, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


class RongrongApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("绒绒")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=TRANSPARENT)
        self.root.wm_attributes("-transparentcolor", TRANSPARENT)

        self.win_w = 360
        self.win_h = 330
        # Standing actions share a floor line.  Only jumping lifts this line.
        self.body_window_x = 180
        self.ground_window_y = 315
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        start_x = max(20, screen_w - self.win_w - 70)
        start_y = max(20, screen_h - self.win_h - 90)
        self.root.geometry(f"{self.win_w}x{self.win_h}+{start_x}+{start_y}")

        self.scale = read_scale()
        saved_offset = read_settings().get("bubble_offset", [0, 0])
        self.bubble_offset_x = int(saved_offset[0]) if len(saved_offset) >= 2 else 0
        self.bubble_offset_y = int(saved_offset[1]) if len(saved_offset) >= 2 else 0
        self.photo_cache = {}
        self.frames = {}
        self.load_frames()

        self.bubble = tk.Label(
            self.root,
            text="",
            bg="#fff3fb",
            fg="#8d4772",
            font=("Microsoft YaHei UI", 10),
            justify="left",
            wraplength=260,
            padx=12,
            pady=8,
            bd=0,
        )
        self.bubble.place_forget()
        self.bubble.bind("<ButtonPress-1>", self.on_bubble_press)
        self.bubble.bind("<B1-Motion>", self.on_bubble_motion)
        self.bubble.bind("<ButtonRelease-1>", self.on_bubble_release)
        self.bubble.bind("<Double-Button-1>", self.reset_bubble_position)

        self.sprite = tk.Label(self.root, bg=TRANSPARENT, bd=0, highlightthickness=0)
        self.sprite.bind("<ButtonPress-1>", self.on_press)
        self.sprite.bind("<B1-Motion>", self.on_motion)
        self.sprite.bind("<ButtonRelease-1>", self.on_release)
        self.sprite.bind("<Button-3>", lambda _event: self.root.destroy())
        self.sprite.bind("<MouseWheel>", self.on_mousewheel)
        self.sprite.bind("<Button-4>", lambda _event: self.change_scale(0.1))
        self.sprite.bind("<Button-5>", lambda _event: self.change_scale(-0.1))

        self.frame_index = 0
        self.last_frame_change_at = time.time()
        self.action = None
        self.action_frames = []
        self.action_offsets = []
        self.speaking_until = 0
        self.dizzy_until = 0
        self.last_dizzy_at = 0
        self.dragging = False
        self.drag_started = False
        self.drag_direction = "right"
        self.press_pointer = (0, 0)
        self.press_window = (0, 0)
        self.pointer_history = deque(maxlen=8)
        self.next_comment_at = time.time() + random.randint(18, 35)
        self.look_index = len(self.frames["look"]) // 2
        self.last_mouse_move_at = time.time()
        self.last_pointer_position = self.root.winfo_pointerxy()

        self.say("我出来啦。把鼠标放在我身上滚动，就能调节我的大小。", force=True)
        self.root.after(TICK_MS, self.tick)

    def load_frames(self):
        if not CODEX_STYLE_ASSET_DIR.exists():
            raise FileNotFoundError("缺少 assets-codex-style/frames，请确认完整下载项目文件。")
        self.use_redrawn = True
        self.use_registered_assets = True
        for key in (
            "idle",
            "smile",
            "startle",
            "sad",
            "thinking",
            "bounce",
            "running-left",
            "running-right",
        ):
            self.frames[key] = self.load_folder(CODEX_STYLE_ASSET_DIR / key, key)
        self.frames["look"] = self.load_folder(CODEX_STYLE_ASSET_DIR / "look25", "look")
        self.frames["dizzy"] = self.make_dizzy_frames(self.frames["idle"])

    def load_folder(self, folder, kind):
        frames = []
        for file in sorted(folder.glob("*.png")):
            frames.append(self.prepare_image(Image.open(file), kind, len(frames)))
        return frames

    def prepare_image(self, image, kind, index=0):
        image = self.remove_green_fringe(image.convert("RGBA"))
        if image.size != CANVAS_SIZE:
            image = self.fit_to_canvas(image)
        image = self.normalise_action_size(image, kind, index)
        if getattr(self, "use_registered_assets", False):
            return self.harden_alpha(image)
        # Redrawn jump frames already contain their vertical path.  The stable
        # drag frames also already share one registered upper body; aligning
        # either set frame-by-frame would reintroduce visible body movement.
        if kind in {"bounce", "running-left", "running-right"} and getattr(self, "use_redrawn", False):
            return self.harden_alpha(image)
        return self.harden_alpha(self.align_standing_frame(image))

    @staticmethod
    def fit_to_canvas(image):
        box = image.getchannel("A").getbbox()
        if not box:
            return Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
        art = image.crop(box)
        factor = min(198 / art.height, 164 / art.width)
        art = art.resize((round(art.width * factor), round(art.height * factor)), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
        canvas.alpha_composite(art, ((CANVAS_SIZE[0] - art.width) // 2, GROUND_Y - art.height))
        return canvas

    @staticmethod
    def align_standing_frame(image):
        """Keep the feet at one screen point while the head performs the pose."""
        box = image.getchannel("A").getbbox()
        if not box:
            return image
        alpha = image.getchannel("A")
        # Use the lowest twelve rows as the foot anchor.  This keeps lean,
        # bowing and startled poses grounded without recentering their heads.
        foot_pixels = []
        for y in range(max(box[1], box[3] - 12), box[3]):
            for x in range(box[0], box[2]):
                value = alpha.getpixel((x, y))
                if value >= 64:
                    foot_pixels.append((x, value))
        if foot_pixels:
            foot_x = sum(x * value for x, value in foot_pixels) / sum(value for _, value in foot_pixels)
        else:
            foot_x = (box[0] + box[2]) / 2
        shift_x = round(HEAD_ANCHOR[0] - foot_x)
        shift_y = GROUND_Y - box[3]
        if shift_x == 0 and shift_y == 0:
            return image
        canvas = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
        canvas.alpha_composite(image, (shift_x, shift_y))
        return canvas

    @staticmethod
    def harden_alpha(image, threshold=72):
        """Remove translucent pixels that Windows' chroma window turns green."""
        alpha = image.getchannel("A").point(lambda value: 255 if value >= threshold else 0)
        result = image.copy()
        result.putalpha(alpha)
        return result

    @staticmethod
    def remove_green_fringe(image):
        """Neutralise the chroma-key green retained in semi-transparent edges."""
        pixels = bytearray(image.tobytes())
        for pos in range(0, len(pixels), 4):
            red, green, blue, alpha = pixels[pos : pos + 4]
            if alpha < 18:
                pixels[pos + 3] = 0
                continue
            # There is no intended green in 绒绒's pale-pink palette.  Replacing
            # only excess green preserves the soft white ear highlights.
            if green > red + 14 and green > blue + 12:
                pixels[pos + 1] = min(green, max(red, blue) + 4)
        return Image.frombytes("RGBA", image.size, bytes(pixels))

    @staticmethod
    def action_scale(kind, index):
        # The production sheets already share one head scale and canvas.
        return 1.0

    def normalise_action_size(self, image, kind, index):
        factor = self.action_scale(kind, index)
        if factor == 1.0:
            return image
        width = max(1, round(image.width * factor))
        height = max(1, round(image.height * factor))
        scaled = image.resize((width, height), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
        # Scale about the head anchor rather than the canvas or a tail/foot.
        paste_x = round(HEAD_ANCHOR[0] - HEAD_ANCHOR[0] * factor)
        paste_y = round(HEAD_ANCHOR[1] - HEAD_ANCHOR[1] * factor)
        canvas.alpha_composite(scaled, (paste_x, paste_y))
        return canvas

    @staticmethod
    def make_dizzy_frames(source_frames):
        result = []
        for index, source in enumerate(source_frames):
            image = source.copy()
            draw = ImageDraw.Draw(image)
            wobble = math.sin(index * 1.4) * 3
            for center_x in (70 + wobble, 122 - wobble):
                center_y = 102
                for radius in (15, 10, 6):
                    box = (center_x - radius, center_y - radius, center_x + radius, center_y + radius)
                    draw.arc(box, start=25 + index * 35, end=300 + index * 35, fill=(255, 95, 174, 245), width=3)
                draw.ellipse((center_x - 3, center_y - 3, center_x + 3, center_y + 3), fill=(255, 255, 255, 255))
            result.append(image)
        return result

    def photo_for(self, image):
        cache_key = (id(image), round(self.scale, 2))
        photo = self.photo_cache.get(cache_key)
        if photo is None:
            width = max(1, round(image.width * self.scale))
            height = max(1, round(image.height * self.scale))
            resized = image.resize((width, height), Image.Resampling.LANCZOS)
            # LANCZOS creates fresh translucent edge pixels.  Tk blends those
            # against the transparent-colour green unless alpha is hardened
            # after the final resize.
            resized = self.harden_alpha(self.remove_green_fringe(resized))
            photo = ImageTk.PhotoImage(resized)
            self.photo_cache[cache_key] = photo
        return photo

    def tick(self):
        now = time.time()
        pointer_x, pointer_y = self.root.winfo_pointerxy()
        self.update_pointer_speed(pointer_x, pointer_y, now)
        self.update_look_direction(pointer_x, pointer_y, now)
        self.maybe_comment_on_screen(now)

        if self.speaking_until and now >= self.speaking_until:
            self.speaking_until = 0
            self.bubble.place_forget()

        frame, offset_y = self.next_frame(now)
        photo = self.photo_for(frame)
        self.sprite.configure(image=photo)
        self.sprite.image = photo
        x = round(self.body_window_x - (CANVAS_SIZE[0] / 2) * self.scale)
        y = round(self.ground_window_y - GROUND_Y * self.scale + offset_y * self.scale)
        self.sprite.place(x=x, y=y)
        if self.bubble.winfo_ismapped():
            self.bubble.lift()
        self.root.after(TICK_MS, self.tick)

    def update_pointer_speed(self, x, y, now):
        old_pointer_x, old_pointer_y = self.last_pointer_position
        if abs(x - old_pointer_x) + abs(y - old_pointer_y) > 1:
            self.last_mouse_move_at = now
            self.last_pointer_position = (x, y)
        self.pointer_history.append((x, y, now))
        if len(self.pointer_history) < 3:
            return
        old_x, old_y, old_time = self.pointer_history[0]
        elapsed = max(now - old_time, 0.001)
        speed = math.hypot(x - old_x, y - old_y) / elapsed
        if speed > 2600 and now - self.last_dizzy_at > 3 and not self.dragging:
            self.dizzy_until = now + 1.7
            self.last_dizzy_at = now
            self.frame_index = 0
            self.last_frame_change_at = now

    def update_look_direction(self, pointer_x, pointer_y, now):
        # If the mouse rests, 绒绒 returns to a friendly front-facing pose.
        if now - self.last_mouse_move_at >= 1.0:
            self.look_index = len(self.frames["look"]) // 2
            return
        head_x = self.root.winfo_x() + self.body_window_x
        head_y = self.root.winfo_y() + self.ground_window_y - (GROUND_Y - HEAD_ANCHOR[1]) * self.scale
        dx = pointer_x - head_x
        dy = pointer_y - head_y
        if math.hypot(dx, dy) < 10:
            return
        if len(self.frames["look"]) == 25:
            # Five screen-coordinate levels on each axis.  The generated sheet
            # is already ordered left-to-right and up-to-down, so no inversion.
            column = 0 if dx < -150 else (1 if dx < -55 else (2 if dx <= 55 else (3 if dx <= 150 else 4)))
            row = 0 if dy < -140 else (1 if dy < -50 else (2 if dy <= 50 else (3 if dy <= 140 else 4)))
            self.look_index = row * 5 + column
        else:
            column = 0 if dx < -45 else (2 if dx > 45 else 1)
            row = 0 if dy < -40 else (2 if dy > 40 else 1)
            self.look_index = row * 3 + column

    def next_frame(self, now):
        offset_y = 0
        if self.dragging:
            frames = self.frames[f"running-{self.drag_direction}"]
            return self.loop_frame(frames, now, 0.14), offset_y

        if self.speaking_until:
            frames = self.frames["thinking"]
            return self.loop_frame(frames, now, 0.80), offset_y

        if self.action:
            frame = self.action_frames[self.frame_index]
            offset_y = self.action_offsets[self.frame_index]
            intervals = {"smile": 0.20, "startle": 0.18, "bounce": 0.16, "sad": 0.22}
            interval = intervals.get(self.action, 0.20)
            if now - self.last_frame_change_at >= interval:
                self.frame_index += 1
                self.last_frame_change_at = now
                if self.frame_index >= len(self.action_frames):
                    self.action = None
                    self.frame_index = 0
            return frame, offset_y

        if now < self.dizzy_until:
            frames = self.frames["dizzy"]
            return self.loop_frame(frames, now, 0.18), offset_y

        frames = self.frames["look"]
        return frames[self.look_index % len(frames)], offset_y

    def loop_frame(self, frames, now, interval):
        frame = frames[self.frame_index % len(frames)]
        if now - self.last_frame_change_at >= interval:
            self.frame_index = (self.frame_index + 1) % len(frames)
            self.last_frame_change_at = now
        return frame

    def on_press(self, _event):
        self.press_pointer = (self.root.winfo_pointerx(), self.root.winfo_pointery())
        self.last_drag_pointer = self.press_pointer
        self.press_window = (self.root.winfo_x(), self.root.winfo_y())
        self.drag_started = False

    def on_motion(self, _event):
        pointer_x, pointer_y = self.root.winfo_pointerxy()
        start_x, start_y = self.press_pointer
        dx = pointer_x - start_x
        dy = pointer_y - start_y
        if abs(dx) + abs(dy) > 5:
            if not self.dragging:
                self.dragging = True
                self.drag_started = True
                self.frame_index = 0
                self.last_frame_change_at = time.time()
                self.action = None
            last_x, last_y = self.last_drag_pointer
            turn_dx = pointer_x - last_x
            if turn_dx < -1:
                self.drag_direction = "left"
            elif turn_dx > 1:
                self.drag_direction = "right"
            self.last_drag_pointer = (pointer_x, pointer_y)
            window_x, window_y = self.press_window
            self.root.geometry(f"+{window_x + dx}+{window_y + dy}")

    def on_release(self, _event):
        was_dragging = self.drag_started
        self.dragging = False
        self.drag_started = False
        if not was_dragging:
            self.random_click_action()

    def on_mousewheel(self, event):
        self.change_scale(0.1 if event.delta > 0 else -0.1)

    def change_scale(self, change):
        new_scale = round(max(0.55, min(1.65, self.scale + change)), 2)
        if new_scale == self.scale:
            return
        self.scale = new_scale
        self.photo_cache.clear()
        save_scale(self.scale)
        self.bubble.configure(text=f"现在是 {round(self.scale * 100)}% 大小。")
        self.place_bubble()
        self.speaking_until = time.time() + 1.3
        self.frame_index = 0

    def random_click_action(self):
        choice = random.choice(["smile", "startle", "bounce", "sad"])
        if choice == "bounce":
            offsets = [0, -10, -32, -18, 0]
            self.start_action("bounce", self.frames["bounce"], offsets[: len(self.frames["bounce"])])
            return
        self.start_action(choice, self.frames[choice])

    def start_action(self, name, frames, offsets=None):
        self.action = name
        self.action_frames = frames
        self.action_offsets = offsets or [0] * len(frames)
        self.frame_index = 0
        self.last_frame_change_at = time.time()
        self.dizzy_until = 0

    def say(self, text, force=False):
        if self.dragging or (self.speaking_until and not force):
            return
        self.bubble.configure(text=text)
        self.place_bubble()
        self.speaking_until = time.time() + min(9.0, 3.2 + len(text) * 0.12)
        self.frame_index = 0
        self.last_frame_change_at = time.time()

    def place_bubble(self):
        """Restore the original caption location plus the user's saved offset."""
        self.root.update_idletasks()
        bubble_width = self.bubble.winfo_reqwidth()
        bubble_height = self.bubble.winfo_reqheight()
        x = 34 + self.bubble_offset_x
        y = 18 + self.bubble_offset_y
        x = max(4, min(self.win_w - bubble_width - 4, x))
        y = max(4, min(self.win_h - bubble_height - 4, y))
        self.bubble.place(x=x, y=y)
        self.bubble.lift()

    def on_bubble_press(self, _event):
        self.bubble_drag_pointer = self.root.winfo_pointerxy()
        self.bubble_drag_offset = (self.bubble_offset_x, self.bubble_offset_y)

    def on_bubble_motion(self, _event):
        pointer_x, pointer_y = self.root.winfo_pointerxy()
        start_x, start_y = self.bubble_drag_pointer
        offset_x, offset_y = self.bubble_drag_offset
        self.bubble_offset_x = offset_x + pointer_x - start_x
        self.bubble_offset_y = offset_y + pointer_y - start_y
        self.place_bubble()

    def on_bubble_release(self, _event):
        save_bubble_offset(self.bubble_offset_x, self.bubble_offset_y)

    def reset_bubble_position(self, _event=None):
        self.bubble_offset_x = 0
        self.bubble_offset_y = 0
        save_bubble_offset(0, 0)
        self.place_bubble()

    def maybe_comment_on_screen(self, now):
        if now < self.next_comment_at or self.speaking_until or self.action or self.dragging:
            return
        self.next_comment_at = now + random.randint(38, 85)
        try:
            shot = ImageGrab.grab(all_screens=True).resize((64, 36))
            red, green, blue = ImageStat.Stat(shot.convert("RGB")).mean
            brightness = (red + green + blue) / 3
            saturation = max(red, green, blue) - min(red, green, blue)
        except Exception:
            brightness, saturation = 128, 20

        title = active_window_title()
        comments = []
        if title:
            low = title.lower()
            if "codex" in low:
                comments.append("你又在和 Codex 工作呀。我会安静一点，偷偷看。")
            elif "browser" in low or "chrome" in low or "edge" in low:
                comments.append("网页好多层，我差点在标签页里迷路。")
            elif "word" in low or "doc" in low:
                comments.append("这页字好多，像一片会排队的小雪。")
            else:
                comments.append(f"我看到你在看《{title[:18]}》。唔……像是在认真施法。")
        if brightness < 70:
            comments.append("屏幕有点暗，我的眼睛快要进入小夜灯模式了。")
        elif brightness > 205:
            comments.append("这里好亮，绒绒差点被屏幕晒成棉花糖。")
        if saturation > 80:
            comments.append("颜色好多，我先挑一块最粉的地方盯着。")
        comments.extend(
            [
                "我路过看一眼：你现在看起来像是在做很厉害的事。",
                "如果屏幕突然很复杂，绒绒会假装自己完全懂了。",
                "嗷呜，我批准这块屏幕继续存在三分钟。",
            ]
        )
        self.say(random.choice(comments))


if __name__ == "__main__":
    app = RongrongApp()
    app.root.mainloop()
