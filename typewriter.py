#!/usr/bin/env python3
"""
etyper - Minimal e-paper typewriter for Orange Pi Zero 2W.

Features:
  - Portrait mode (300x400 effective, display rotated 90 CCW)
  - Opens last document on startup
  - USB keyboard input via evdev
  - Autosave every 10 seconds
  - Partial refresh for fast typing (~0.5s per update)
  - Full refresh every 5 minutes (or Ctrl+R) to clean ghosting
  - Arrow key cursor movement, insert/delete at any position
  - Word wrap with monospace font

Keyboard shortcuts:
  Ctrl+N  - New document
  Ctrl+S  - Manual save
  Ctrl+R  - Force full refresh (clean ghosting)
  Ctrl+F  - Toggle file server (download docs via browser)
  Ctrl+K  - Choose keyboard layout (arrow keys + Enter)
  Ctrl+Q  - Sleep / wake

Usage:
  sudo python3 typewriter.py
"""

import os
import sys
import ssl
import time
import signal
import select
import textwrap
import subprocess
import threading
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import quote, unquote

try:
    import dbus
    import dbus.service
    import dbus.mainloop.glib
    from gi.repository import GLib
    HAS_DBUS = True
except ImportError:
    HAS_DBUS = False

from PIL import Image, ImageDraw, ImageFont

from epd42_driver import EPD42

# --- Configuration ---

DOCS_DIR = os.path.expanduser("~/etyper_docs")
LAST_DOC_FILE = os.path.join(DOCS_DIR, ".last_doc")
LAYOUT_CONFIG_FILE = os.path.join(DOCS_DIR, ".layout")
AUTOSAVE_INTERVAL = 10  # seconds

# Portrait dimensions (display is 400x300, rotated 90 CCW)
PORTRAIT_W = 400
PORTRAIT_H = 300

# Text layout
MARGIN_X = 8
MARGIN_Y = 10

# Font settings
FONT_SIZE = 16
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_PATHS = [
    os.path.join(SCRIPT_DIR, "fonts", "AtkinsonHyperlegibleMono-Medium.ttf"),
    os.path.join(SCRIPT_DIR, "fonts", "AtkinsonHyperlegibleMono-Regular.ttf"),
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
]

# --- Keyboard mapping (evdev keycodes -> characters) ---

try:
    from evdev import InputDevice, ecodes, list_devices
    HAS_EVDEV = True
except ImportError:
    HAS_EVDEV = False

# Keyboard layouts: name -> {keycode: (normal, shifted)}
LAYOUTS = {}
LAYOUT_NAMES = []

if HAS_EVDEV:
    LAYOUTS["US QWERTY"] = {
        ecodes.KEY_A: ("a", "A"), ecodes.KEY_B: ("b", "B"), ecodes.KEY_C: ("c", "C"),
        ecodes.KEY_D: ("d", "D"), ecodes.KEY_E: ("e", "E"), ecodes.KEY_F: ("f", "F"),
        ecodes.KEY_G: ("g", "G"), ecodes.KEY_H: ("h", "H"), ecodes.KEY_I: ("i", "I"),
        ecodes.KEY_J: ("j", "J"), ecodes.KEY_K: ("k", "K"), ecodes.KEY_L: ("l", "L"),
        ecodes.KEY_M: ("m", "M"), ecodes.KEY_N: ("n", "N"), ecodes.KEY_O: ("o", "O"),
        ecodes.KEY_P: ("p", "P"), ecodes.KEY_Q: ("q", "Q"), ecodes.KEY_R: ("r", "R"),
        ecodes.KEY_S: ("s", "S"), ecodes.KEY_T: ("t", "T"), ecodes.KEY_U: ("u", "U"),
        ecodes.KEY_V: ("v", "V"), ecodes.KEY_W: ("w", "W"), ecodes.KEY_X: ("x", "X"),
        ecodes.KEY_Y: ("y", "Y"), ecodes.KEY_Z: ("z", "Z"),
        ecodes.KEY_1: ("1", "!"), ecodes.KEY_2: ("2", "@"), ecodes.KEY_3: ("3", "#"),
        ecodes.KEY_4: ("4", "$"), ecodes.KEY_5: ("5", "%"), ecodes.KEY_6: ("6", "^"),
        ecodes.KEY_7: ("7", "&"), ecodes.KEY_8: ("8", "*"), ecodes.KEY_9: ("9", "("),
        ecodes.KEY_0: ("0", ")"),
        ecodes.KEY_MINUS: ("-", "_"), ecodes.KEY_EQUAL: ("=", "+"),
        ecodes.KEY_LEFTBRACE: ("[", "{"), ecodes.KEY_RIGHTBRACE: ("]", "}"),
        ecodes.KEY_SEMICOLON: (";", ":"), ecodes.KEY_APOSTROPHE: ("'", '"'),
        ecodes.KEY_GRAVE: ("`", "~"), ecodes.KEY_BACKSLASH: ("\\", "|"),
        ecodes.KEY_COMMA: (",", "<"), ecodes.KEY_DOT: (".", ">"),
        ecodes.KEY_SLASH: ("/", "?"),
        ecodes.KEY_SPACE: (" ", " "), ecodes.KEY_TAB: ("    ", "    "),
    }

    LAYOUTS["UK QWERTY"] = {
        **LAYOUTS["US QWERTY"],
        ecodes.KEY_2: ("2", '"'),
        ecodes.KEY_3: ("3", "\u00a3"),   # £
        ecodes.KEY_APOSTROPHE: ("'", "@"),
        ecodes.KEY_BACKSLASH: ("#", "~"),
        ecodes.KEY_GRAVE: ("`", "\u00ac"),  # ¬
    }

    LAYOUTS["DE QWERTZ"] = {
        **LAYOUTS["US QWERTY"],
        # Y and Z are swapped on German keyboards
        ecodes.KEY_Y: ("z", "Z"),
        ecodes.KEY_Z: ("y", "Y"),
        # Umlauts
        ecodes.KEY_SEMICOLON: ("\u00f6", "\u00d6"),   # ö Ö
        ecodes.KEY_APOSTROPHE: ("\u00e4", "\u00c4"),  # ä Ä
        ecodes.KEY_LEFTBRACE: ("\u00fc", "\u00dc"),   # ü Ü
        # Other German-specific symbols
        ecodes.KEY_MINUS: ("\u00df", "?"),             # ß
        ecodes.KEY_EQUAL: ("\u00b4", "`"),             # ´
        ecodes.KEY_RIGHTBRACE: ("+", "*"),
        ecodes.KEY_BACKSLASH: ("#", "'"),
        ecodes.KEY_GRAVE: ("^", "\u00b0"),             # °
        ecodes.KEY_COMMA: (",", ";"),
        ecodes.KEY_DOT: (".", ":"),
        ecodes.KEY_SLASH: ("-", "_"),
    }

    LAYOUTS["US DVORAK"] = {
        # Letters (physical QWERTY key → Dvorak character)
        ecodes.KEY_Q: ("'", '"'),  ecodes.KEY_W: (",", "<"),
        ecodes.KEY_E: (".", ">"),  ecodes.KEY_R: ("p", "P"),
        ecodes.KEY_T: ("y", "Y"),  ecodes.KEY_Y: ("f", "F"),
        ecodes.KEY_U: ("g", "G"),  ecodes.KEY_I: ("c", "C"),
        ecodes.KEY_O: ("r", "R"),  ecodes.KEY_P: ("l", "L"),
        ecodes.KEY_A: ("a", "A"),  ecodes.KEY_S: ("o", "O"),
        ecodes.KEY_D: ("e", "E"),  ecodes.KEY_F: ("u", "U"),
        ecodes.KEY_G: ("i", "I"),  ecodes.KEY_H: ("d", "D"),
        ecodes.KEY_J: ("h", "H"),  ecodes.KEY_K: ("t", "T"),
        ecodes.KEY_L: ("n", "N"),  ecodes.KEY_SEMICOLON: ("s", "S"),
        ecodes.KEY_APOSTROPHE: ("-", "_"),
        ecodes.KEY_Z: (";", ":"),  ecodes.KEY_X: ("q", "Q"),
        ecodes.KEY_C: ("j", "J"),  ecodes.KEY_V: ("k", "K"),
        ecodes.KEY_B: ("x", "X"),  ecodes.KEY_N: ("b", "B"),
        ecodes.KEY_M: ("m", "M"),
        ecodes.KEY_COMMA: ("w", "W"), ecodes.KEY_DOT: ("v", "V"),
        ecodes.KEY_SLASH: ("z", "Z"),
        # Symbols
        ecodes.KEY_LEFTBRACE: ("/", "?"),  ecodes.KEY_RIGHTBRACE: ("=", "+"),
        ecodes.KEY_MINUS: ("[", "{"),      ecodes.KEY_EQUAL: ("]", "}"),
        ecodes.KEY_GRAVE: ("`", "~"),      ecodes.KEY_BACKSLASH: ("\\", "|"),
        # Digits unchanged
        ecodes.KEY_1: ("1", "!"), ecodes.KEY_2: ("2", "@"), ecodes.KEY_3: ("3", "#"),
        ecodes.KEY_4: ("4", "$"), ecodes.KEY_5: ("5", "%"), ecodes.KEY_6: ("6", "^"),
        ecodes.KEY_7: ("7", "&"), ecodes.KEY_8: ("8", "*"), ecodes.KEY_9: ("9", "("),
        ecodes.KEY_0: ("0", ")"),
        ecodes.KEY_SPACE: (" ", " "), ecodes.KEY_TAB: ("    ", "    "),
    }

    # FR AZERTY — physical QWERTY key positions produce French AZERTY characters
    LAYOUTS["FR AZERTY"] = {
        # Number row: digits require Shift; unshifted gives French symbols
        ecodes.KEY_1: ("&", "1"),   ecodes.KEY_2: ("\u00e9", "2"),  # é
        ecodes.KEY_3: ('"', "3"),   ecodes.KEY_4: ("'", "4"),
        ecodes.KEY_5: ("(", "5"),   ecodes.KEY_6: ("-", "6"),
        ecodes.KEY_7: ("\u00e8", "7"),  # è
        ecodes.KEY_8: ("_", "8"),
        ecodes.KEY_9: ("\u00e7", "9"),  # ç
        ecodes.KEY_0: ("\u00e0", "0"),  # à
        ecodes.KEY_MINUS: (")", "\u00b0"),   # °
        ecodes.KEY_EQUAL: ("=", "+"),
        # Top letter row: A and Q swapped, Z and W swapped
        ecodes.KEY_Q: ("a", "A"),   ecodes.KEY_W: ("z", "Z"),
        ecodes.KEY_E: ("e", "E"),   ecodes.KEY_R: ("r", "R"),
        ecodes.KEY_T: ("t", "T"),   ecodes.KEY_Y: ("y", "Y"),
        ecodes.KEY_U: ("u", "U"),   ecodes.KEY_I: ("i", "I"),
        ecodes.KEY_O: ("o", "O"),   ecodes.KEY_P: ("p", "P"),
        ecodes.KEY_LEFTBRACE: ("^", "\u00a8"),   # ¨
        ecodes.KEY_RIGHTBRACE: ("$", "\u00a3"),  # £
        # Middle row
        ecodes.KEY_A: ("q", "Q"),   ecodes.KEY_S: ("s", "S"),
        ecodes.KEY_D: ("d", "D"),   ecodes.KEY_F: ("f", "F"),
        ecodes.KEY_G: ("g", "G"),   ecodes.KEY_H: ("h", "H"),
        ecodes.KEY_J: ("j", "J"),   ecodes.KEY_K: ("k", "K"),
        ecodes.KEY_L: ("l", "L"),
        ecodes.KEY_SEMICOLON: ("m", "M"),
        ecodes.KEY_APOSTROPHE: ("\u00f9", "%"),  # ù
        ecodes.KEY_BACKSLASH: ("*", "\u00b5"),   # µ
        ecodes.KEY_GRAVE: ("\u00b2", ""),         # ²
        # Bottom row: Z→W swap handled above
        ecodes.KEY_Z: ("w", "W"),   ecodes.KEY_X: ("x", "X"),
        ecodes.KEY_C: ("c", "C"),   ecodes.KEY_V: ("v", "V"),
        ecodes.KEY_B: ("b", "B"),   ecodes.KEY_N: ("n", "N"),
        ecodes.KEY_M: (",", "?"),
        ecodes.KEY_COMMA: (";", "."),
        ecodes.KEY_DOT: (":", "/"),
        ecodes.KEY_SLASH: ("!", "\u00a7"),  # §
        ecodes.KEY_SPACE: (" ", " "), ecodes.KEY_TAB: ("    ", "    "),
    }

    # ES QWERTY — Spanish, adds ñ and rearranges some symbols
    LAYOUTS["ES QWERTY"] = {
        **LAYOUTS["US QWERTY"],
        ecodes.KEY_2: ("2", '"'),
        ecodes.KEY_3: ("3", "\u00b7"),     # · middle dot
        ecodes.KEY_6: ("6", "&"),
        ecodes.KEY_7: ("7", "/"),
        ecodes.KEY_8: ("8", "("),
        ecodes.KEY_9: ("9", ")"),
        ecodes.KEY_0: ("0", "="),
        ecodes.KEY_MINUS: ("'", "?"),
        ecodes.KEY_EQUAL: ("\u00a1", "\u00bf"),  # ¡ ¿
        ecodes.KEY_LEFTBRACE: ("`", "^"),
        ecodes.KEY_RIGHTBRACE: ("+", "*"),
        ecodes.KEY_SEMICOLON: ("\u00f1", "\u00d1"),  # ñ Ñ
        ecodes.KEY_APOSTROPHE: ("`", "^"),
        ecodes.KEY_GRAVE: ("\u00ba", "\u00aa"),      # º ª
        ecodes.KEY_BACKSLASH: ("\u00e7", "\u00c7"),  # ç Ç
        ecodes.KEY_COMMA: (",", ";"),
        ecodes.KEY_DOT: (".", ":"),
        ecodes.KEY_SLASH: ("-", "_"),
    }

    # SE QWERTY — Swedish/Finnish, adds å ä ö
    LAYOUTS["SE QWERTY"] = {
        **LAYOUTS["US QWERTY"],
        ecodes.KEY_LEFTBRACE: ("\u00e5", "\u00c5"),   # å Å
        ecodes.KEY_SEMICOLON: ("\u00f6", "\u00d6"),   # ö Ö
        ecodes.KEY_APOSTROPHE: ("\u00e4", "\u00c4"),  # ä Ä
        ecodes.KEY_RIGHTBRACE: ("~", "^"),
        ecodes.KEY_MINUS: ("+", "?"),
        ecodes.KEY_EQUAL: ("`", "`"),
        ecodes.KEY_GRAVE: ("\u00a7", "\u00bd"),        # § ½
        ecodes.KEY_BACKSLASH: ("'", "*"),
        ecodes.KEY_COMMA: (",", ";"),
        ecodes.KEY_DOT: (".", ":"),
        ecodes.KEY_SLASH: ("-", "_"),
    }

    # NO/DK QWERTY — Norwegian/Danish, adds å æ ø
    LAYOUTS["NO/DK QWERTY"] = {
        **LAYOUTS["US QWERTY"],
        ecodes.KEY_LEFTBRACE: ("\u00e5", "\u00c5"),   # å Å
        ecodes.KEY_SEMICOLON: ("\u00f8", "\u00d8"),   # ø Ø
        ecodes.KEY_APOSTROPHE: ("\u00e6", "\u00c6"),  # æ Æ
        ecodes.KEY_RIGHTBRACE: ("~", "^"),
        ecodes.KEY_MINUS: ("+", "?"),
        ecodes.KEY_EQUAL: ("`", "`"),
        ecodes.KEY_GRAVE: ("|", "\u00a7"),             # | §
        ecodes.KEY_BACKSLASH: ("'", "*"),
        ecodes.KEY_COMMA: (",", ";"),
        ecodes.KEY_DOT: (".", ":"),
        ecodes.KEY_SLASH: ("-", "_"),
    }

    # IT QWERTY — Italian, adds à è é ì ò ù
    LAYOUTS["IT QWERTY"] = {
        **LAYOUTS["US QWERTY"],
        ecodes.KEY_2: ("2", '"'),
        ecodes.KEY_3: ("3", "\u00a3"),             # £
        ecodes.KEY_6: ("6", "&"),
        ecodes.KEY_7: ("7", "/"),
        ecodes.KEY_8: ("8", "("),
        ecodes.KEY_9: ("9", ")"),
        ecodes.KEY_0: ("0", "="),
        ecodes.KEY_MINUS: ("'", "?"),
        ecodes.KEY_EQUAL: ("\u00ec", "^"),          # ì
        ecodes.KEY_LEFTBRACE: ("\u00e8", "\u00e9"), # è é
        ecodes.KEY_RIGHTBRACE: ("+", "*"),
        ecodes.KEY_SEMICOLON: ("\u00f2", "\u00e7"), # ò ç
        ecodes.KEY_APOSTROPHE: ("\u00e0", "\u00b0"), # à °
        ecodes.KEY_GRAVE: ("\\", "|"),
        ecodes.KEY_BACKSLASH: ("\u00f9", "\u00a7"), # ù §
        ecodes.KEY_COMMA: (",", ";"),
        ecodes.KEY_DOT: (".", ":"),
        ecodes.KEY_SLASH: ("-", "_"),
    }

    # Colemak — popular ergonomic layout, great for prose writing
    # Only 17 keys differ from QWERTY; hands stay on home row much more
    LAYOUTS["Colemak"] = {
        **LAYOUTS["US QWERTY"],
        ecodes.KEY_E: ("f", "F"),    ecodes.KEY_R: ("p", "P"),
        ecodes.KEY_T: ("g", "G"),    ecodes.KEY_Y: ("j", "J"),
        ecodes.KEY_U: ("l", "L"),    ecodes.KEY_I: ("u", "U"),
        ecodes.KEY_O: ("y", "Y"),    ecodes.KEY_P: (";", ":"),
        ecodes.KEY_S: ("r", "R"),    ecodes.KEY_D: ("s", "S"),
        ecodes.KEY_F: ("t", "T"),    ecodes.KEY_G: ("d", "D"),
        ecodes.KEY_J: ("n", "N"),    ecodes.KEY_K: ("e", "E"),
        ecodes.KEY_L: ("i", "I"),    ecodes.KEY_SEMICOLON: ("o", "O"),
        ecodes.KEY_N: ("k", "K"),
    }

    LAYOUT_NAMES = list(LAYOUTS.keys())

# Default/fallback keymap (US QWERTY)
KEYMAP = LAYOUTS.get("US QWERTY", {})


if HAS_DBUS:
    class _BtAutoAcceptAgent(dbus.service.Object):
        """Bluetooth agent that auto-accepts all pairing and service requests."""

        @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
        def Release(self):
            pass

        @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
        def AuthorizeService(self, device, uuid):
            print(f"  BT: authorized service {uuid}")

        @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="s")
        def RequestPinCode(self, device):
            return "0000"

        @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="u")
        def RequestPasskey(self, device):
            return dbus.UInt32(0)

        @dbus.service.method("org.bluez.Agent1", in_signature="ouq", out_signature="")
        def DisplayPasskey(self, device, passkey, entered):
            pass

        @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
        def DisplayPinCode(self, device, pincode):
            pass

        @dbus.service.method("org.bluez.Agent1", in_signature="ou", out_signature="")
        def RequestConfirmation(self, device, passkey):
            print(f"  BT: confirmed pairing ({passkey})")

        @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="")
        def RequestAuthorization(self, device):
            pass

        @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
        def Cancel(self):
            pass


class EtyperApp:
    """Main typewriter application with cursor movement."""

    def __init__(self):
        self.text = ""
        self.cursor = 0  # character index in self.text
        self.doc_path = None
        self.running = False
        self.dirty = False
        self.last_save_time = time.time()
        self.epd = None
        self.keyboard = None
        self.font = None
        self.shift_held = False
        self.ctrl_held = False
        self.active_layout = "US QWERTY"
        self.keymap = KEYMAP
        self.chars_per_line = 30
        self.lines_per_page = 20
        self.needs_display_update = True
        self.scroll_offset = 0  # first visible wrapped-line index
        self._bt_agent = None  # reusable D-Bus BT agent
        self._bt_bus = None  # reusable D-Bus system bus
        self._dbus_mainloop_set = False  # ensure mainloop set only once

    def _find_font(self):
        """Find a suitable monospace font."""
        for path in FONT_PATHS:
            if os.path.exists(path):
                return ImageFont.truetype(path, FONT_SIZE)
        return ImageFont.load_default()

    def _calc_text_metrics(self):
        """Calculate how many chars/lines fit on screen using proper font metrics."""
        ascent, descent = self.font.getmetrics()
        char_w = int(self.font.getlength("M"))

        usable_w = PORTRAIT_W - 2 * MARGIN_X
        usable_h = PORTRAIT_H - 2 * MARGIN_Y

        self.char_w = char_w
        self.cell_h = ascent + descent          # full character cell (22px)
        self.line_h = int(FONT_SIZE * 1.5)      # WCAG line height (24px)
        self.chars_per_line = max(1, usable_w // char_w)
        self.lines_per_page = max(1, usable_h // self.line_h) - 1  # reserve status bar

    def _find_keyboard(self):
        """Find a USB keyboard device via evdev."""
        if not HAS_EVDEV:
            return None

        devices = [InputDevice(path) for path in list_devices()]
        for dev in devices:
            caps = dev.capabilities(verbose=False)
            if ecodes.EV_KEY in caps:
                keys = caps[ecodes.EV_KEY]
                if ecodes.KEY_A in keys and ecodes.KEY_ENTER in keys:
                    print(f"Keyboard found: {dev.name} ({dev.path})")
                    return dev

        print("WARNING: No keyboard found. Waiting for connection...")
        return None

    # --- Layout management ---

    def _load_layout_pref(self):
        """Load saved keyboard layout preference from disk."""
        self._ensure_docs_dir()
        if os.path.exists(LAYOUT_CONFIG_FILE):
            name = open(LAYOUT_CONFIG_FILE).read().strip()
            if name in LAYOUTS:
                self.active_layout = name
                self.keymap = LAYOUTS[name]
                print(f"Layout: {name}")
                return
        self.active_layout = "US QWERTY"
        self.keymap = LAYOUTS.get("US QWERTY", KEYMAP)

    def _save_layout_pref(self):
        """Save current keyboard layout preference to disk."""
        self._ensure_docs_dir()
        with open(LAYOUT_CONFIG_FILE, "w") as f:
            f.write(self.active_layout)

    def _show_layout_picker(self):
        """Show a full-screen layout picker on the e-paper display.

        Navigate with Up/Down, confirm with Enter, cancel with Escape or Ctrl+K.
        """
        if not LAYOUT_NAMES:
            return

        selected = LAYOUT_NAMES.index(self.active_layout) if self.active_layout in LAYOUT_NAMES else 0

        def render_picker(sel_idx):
            img = Image.new("1", (PORTRAIT_W, PORTRAIT_H), 255)
            draw = ImageDraw.Draw(img)

            title = "-- Keyboard Layout --"
            tw = int(self.font.getlength(title))
            draw.text(((PORTRAIT_W - tw) // 2, MARGIN_Y + 4), title, font=self.font, fill=0)
            draw.line([(MARGIN_X, MARGIN_Y + self.line_h + 6),
                       (PORTRAIT_W - MARGIN_X, MARGIN_Y + self.line_h + 6)], fill=0)

            y = MARGIN_Y + self.line_h + 14
            for i, name in enumerate(LAYOUT_NAMES):
                label = f"> {name}" if i == sel_idx else f"  {name}"
                if i == sel_idx:
                    draw.rectangle(
                        [MARGIN_X - 2, y - 1,
                         PORTRAIT_W - MARGIN_X + 2, y + self.cell_h],
                        fill=0,
                    )
                    draw.text((MARGIN_X + 2, y), label, font=self.font, fill=1)
                else:
                    draw.text((MARGIN_X + 2, y), label, font=self.font, fill=0)
                y += self.line_h + 4

            hint = "Enter=select  Esc=cancel"
            hw = int(self.font.getlength(hint))
            hy = PORTRAIT_H - MARGIN_Y - self.cell_h - 2
            draw.line([(MARGIN_X, hy - 2), (PORTRAIT_W - MARGIN_X, hy - 2)], fill=0)
            draw.text(((PORTRAIT_W - hw) // 2, hy), hint, font=self.font, fill=0)

            return img.rotate(0)

        # Show picker with full refresh
        self.epd.init()
        self.epd.display(list(render_picker(selected).tobytes()))
        self.epd.init_partial()

        # Input loop
        ctrl_held = False
        while self.running:
            if self.keyboard is None:
                self.keyboard = self._find_keyboard()
                if self.keyboard is None:
                    time.sleep(1)
                    continue
            try:
                r, _, _ = select.select([self.keyboard.fd], [], [], 1.0)
                if not r:
                    continue
                for event in self.keyboard.read():
                    if event.type != ecodes.EV_KEY or event.value == 0:
                        continue
                    code = event.code

                    if code in (ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL):
                        ctrl_held = event.value != 0
                        continue

                    if code == ecodes.KEY_UP:
                        selected = (selected - 1) % len(LAYOUT_NAMES)
                        self.epd.display_image_partial(render_picker(selected))

                    elif code == ecodes.KEY_DOWN:
                        selected = (selected + 1) % len(LAYOUT_NAMES)
                        self.epd.display_image_partial(render_picker(selected))

                    elif code == ecodes.KEY_ENTER:
                        self.active_layout = LAYOUT_NAMES[selected]
                        self.keymap = LAYOUTS[self.active_layout]
                        self._save_layout_pref()
                        print(f"Layout changed to: {self.active_layout}")
                        self._resume_typewriter_display()
                        return

                    elif code == ecodes.KEY_ESC or (code == ecodes.KEY_K and ctrl_held):
                        # Cancel — restore typewriter without changing layout
                        self._resume_typewriter_display()
                        return

            except OSError:
                self.keyboard = None
                time.sleep(1)

    # --- Document management ---

    def _ensure_docs_dir(self):
        os.makedirs(DOCS_DIR, exist_ok=True)

    def _get_last_doc_path(self):
        if os.path.exists(LAST_DOC_FILE):
            path = open(LAST_DOC_FILE).read().strip()
            if os.path.exists(path):
                return path
        return None

    def _set_last_doc(self, path):
        with open(LAST_DOC_FILE, "w") as f:
            f.write(path)

    def _new_doc_path(self):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return os.path.join(DOCS_DIR, f"doc_{ts}.txt")

    def load_document(self, path=None):
        self._ensure_docs_dir()

        if path and os.path.exists(path):
            self.doc_path = path
        else:
            self.doc_path = self._get_last_doc_path()

        if self.doc_path and os.path.exists(self.doc_path):
            with open(self.doc_path, "r") as f:
                self.text = f.read()
            print(f"Opened: {self.doc_path}")
        else:
            self.doc_path = self._new_doc_path()
            self.text = ""
            print(f"New document: {self.doc_path}")

        self.cursor = len(self.text)  # cursor at end
        self._set_last_doc(self.doc_path)
        self.dirty = False

    def save_document(self):
        if self.doc_path:
            with open(self.doc_path, "w") as f:
                f.write(self.text)
            self.dirty = False
            self.last_save_time = time.time()

    def new_document(self):
        self.save_document()
        self.doc_path = self._new_doc_path()
        self.text = ""
        self.cursor = 0
        self.scroll_offset = 0
        self._set_last_doc(self.doc_path)
        self.dirty = False
        self.needs_display_update = True

    def _list_docs(self):
        """Return sorted list of all .txt document paths in the docs directory."""
        self._ensure_docs_dir()
        docs = sorted(
            f for f in os.listdir(DOCS_DIR)
            if f.endswith(".txt") and f.startswith("doc_")
        )
        return [os.path.join(DOCS_DIR, f) for f in docs]

    def _switch_document(self, direction):
        """Switch to the next (+1) or previous (-1) document."""
        self.save_document()
        docs = self._list_docs()
        if not docs:
            return

        try:
            idx = docs.index(self.doc_path)
        except ValueError:
            idx = len(docs) - 1

        new_idx = idx + direction
        if new_idx < 0 or new_idx >= len(docs):
            return  # already at first/last document

        self.load_document(docs[new_idx])
        self.needs_display_update = True
        print(f"Switched to: {os.path.basename(self.doc_path)} "
              f"({new_idx + 1}/{len(docs)})")

    # --- Text wrapping with cursor tracking ---

    def _wrap_with_cursor(self):
        """Word-wrap text and track which wrapped line/column the cursor is on.

        Returns:
            (lines, cursor_line, cursor_col) where lines is a list of strings,
            cursor_line is the 0-based index into lines, cursor_col is the
            character offset within that line.
        """
        cpl = self.chars_per_line
        lines = []
        char_to_pos = {}
        text = self.text

        line_idx = 0
        para_start = 0
        paragraphs = text.split("\n")

        for p_idx, para in enumerate(paragraphs):
            if para == "":
                lines.append("")
                # Map the \n after this empty paragraph to start of this line
                if p_idx < len(paragraphs) - 1:
                    char_to_pos[para_start] = (line_idx, 0)
                line_idx += 1
                para_start += 1  # empty string + \n
                continue

            # Wrap this paragraph
            wrapped = textwrap.wrap(para, width=cpl,
                                    break_long_words=True,
                                    break_on_hyphens=False)
            if not wrapped:
                wrapped = [""]

            # Map character positions within paragraph to wrapped lines
            para_char = 0
            for w_line in wrapped:
                for col, ch in enumerate(w_line):
                    char_to_pos[para_start + para_char] = (line_idx, col)
                    para_char += 1
                # Account for the space that was consumed by wrapping
                if para_char < len(para) and para[para_char] == " ":
                    char_to_pos[para_start + para_char] = (line_idx, len(w_line))
                    para_char += 1
                lines.append(w_line)
                line_idx += 1

            # Map the \n that ends this paragraph (cursor at \n = end of line)
            if p_idx < len(paragraphs) - 1:
                newline_pos = para_start + len(para)
                char_to_pos[newline_pos] = (line_idx - 1, len(lines[-1]))

            para_start += len(para) + 1  # +1 for \n

        # Handle empty document
        if not lines:
            lines = [""]

        # Find cursor position
        if self.cursor >= len(text):
            # Cursor at end of text
            if lines:
                cursor_line = len(lines) - 1
                cursor_col = len(lines[-1])
            else:
                cursor_line = 0
                cursor_col = 0
        elif self.cursor in char_to_pos:
            cursor_line, cursor_col = char_to_pos[self.cursor]
        else:
            # Fallback: cursor at end
            cursor_line = len(lines) - 1
            cursor_col = len(lines[-1])

        return lines, cursor_line, cursor_col

    # --- Rendering ---

    def render(self):
        """Render the current text to a PIL Image in portrait orientation."""
        img = Image.new("1", (PORTRAIT_W, PORTRAIT_H), 255)
        draw = ImageDraw.Draw(img)

        lines, cursor_line, cursor_col = self._wrap_with_cursor()

        visible = self.lines_per_page

        # Auto-scroll to keep cursor visible
        if cursor_line < self.scroll_offset:
            self.scroll_offset = cursor_line
        elif cursor_line >= self.scroll_offset + visible:
            self.scroll_offset = cursor_line - visible + 1

        display_lines = lines[self.scroll_offset:self.scroll_offset + visible]

        # Draw text lines
        y = MARGIN_Y
        for line in display_lines:
            draw.text((MARGIN_X, y), line, font=self.font, fill=0)
            y += self.line_h

        # Draw cursor block (full cell height to cover ascenders and descenders)
        vis_cursor_line = cursor_line - self.scroll_offset
        if 0 <= vis_cursor_line < visible:
            cx = MARGIN_X + cursor_col * self.char_w
            cy = MARGIN_Y + vis_cursor_line * self.line_h

            if cx + self.char_w <= PORTRAIT_W - MARGIN_X:
                draw.rectangle(
                    [cx, cy, cx + self.char_w - 1, cy + self.cell_h - 1],
                    fill=0
                )
                # Draw the character under cursor in white (inverted)
                if cursor_line < len(lines) and cursor_col < len(lines[cursor_line]):
                    ch = lines[cursor_line][cursor_col]
                    draw.text((cx, cy), ch, font=self.font, fill=1)

        # Status bar
        status_y = PORTRAIT_H - MARGIN_Y - self.cell_h
        draw.line([(MARGIN_X, status_y - 2), (PORTRAIT_W - MARGIN_X, status_y - 2)], fill=0)

        doc_name = os.path.basename(self.doc_path) if self.doc_path else "untitled"
        save_indicator = "*" if self.dirty else ""
        line_num = cursor_line + 1
        col_num = cursor_col + 1
        status = f"{save_indicator}{doc_name}"
        draw.text((MARGIN_X, status_y), status, font=self.font, fill=0)

        # Rotate for landscape display
        img_landscape = img.rotate(0)
        return img_landscape

    # --- Cursor movement helpers ---

    def _cursor_up(self):
        """Move cursor up one visual line."""
        lines, cur_line, cur_col = self._wrap_with_cursor()
        if cur_line == 0:
            return  # already at top

        target_line = cur_line - 1
        target_col = min(cur_col, len(lines[target_line]))
        self.cursor = self._pos_from_line_col(lines, target_line, target_col)

    def _cursor_down(self):
        """Move cursor down one visual line."""
        lines, cur_line, cur_col = self._wrap_with_cursor()
        if cur_line >= len(lines) - 1:
            return  # already at bottom

        target_line = cur_line + 1
        target_col = min(cur_col, len(lines[target_line]))
        self.cursor = self._pos_from_line_col(lines, target_line, target_col)

    def _pos_from_line_col(self, lines, target_line, target_col):
        """Convert a visual (line, col) back to a text character index."""
        # Rebuild the text position by walking through paragraphs and wrapping
        cpl = self.chars_per_line
        text = self.text

        line_idx = 0
        text_pos = 0

        for para in text.split("\n"):
            if para == "":
                if line_idx == target_line:
                    return text_pos + min(target_col, 0)
                line_idx += 1
                text_pos += 1  # the \n character
                continue

            wrapped = textwrap.wrap(para, width=cpl,
                                    break_long_words=True,
                                    break_on_hyphens=False)
            if not wrapped:
                wrapped = [""]

            para_char = 0
            for w_line in wrapped:
                if line_idx == target_line:
                    col = min(target_col, len(w_line))
                    return text_pos + para_char + col
                para_char += len(w_line)
                # Skip the space consumed by wrapping
                if para_char < len(para) and para[para_char] == " ":
                    para_char += 1
                line_idx += 1

            text_pos += len(para) + 1  # +1 for \n

        # Past end of text
        return len(text)

    # --- Keyboard input ---

    def _handle_key(self, keycode, value):
        """Process a keyboard event."""
        # Track modifier state
        if keycode in (ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT):
            self.shift_held = value != 0
            return
        if keycode in (ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL):
            self.ctrl_held = value != 0
            return

        # Only handle press and repeat
        if value == 0:
            return

        # Ctrl shortcuts
        if self.ctrl_held:
            if keycode == ecodes.KEY_Q:
                self.save_document()
                self._sleep_mode()
                return
            elif keycode == ecodes.KEY_S:
                self.save_document()
                self.needs_display_update = True
                return
            elif keycode == ecodes.KEY_N:
                self.new_document()
                return
            elif keycode == ecodes.KEY_R:
                # Force full refresh
                img = self.render()
                self.epd.full_refresh(list(img.tobytes()))
                self.needs_display_update = False
                return
            elif keycode == ecodes.KEY_LEFT:
                self._switch_document(-1)
                return
            elif keycode == ecodes.KEY_RIGHT:
                self._switch_document(+1)
                return
            elif keycode == ecodes.KEY_F:
                self._file_server_mode()
                return
            elif keycode == ecodes.KEY_K:
                self._show_layout_picker()
                return

        # Arrow keys
        if keycode == ecodes.KEY_LEFT:
            if self.cursor > 0:
                self.cursor -= 1
                self.needs_display_update = True
            return

        if keycode == ecodes.KEY_RIGHT:
            if self.cursor < len(self.text):
                self.cursor += 1
                self.needs_display_update = True
            return

        if keycode == ecodes.KEY_UP:
            self._cursor_up()
            self.needs_display_update = True
            return

        if keycode == ecodes.KEY_DOWN:
            self._cursor_down()
            self.needs_display_update = True
            return

        if keycode == ecodes.KEY_HOME:
            # Move to start of current visual line
            lines, cur_line, _ = self._wrap_with_cursor()
            self.cursor = self._pos_from_line_col(lines, cur_line, 0)
            self.needs_display_update = True
            return

        if keycode == ecodes.KEY_END:
            # Move to end of current visual line
            lines, cur_line, _ = self._wrap_with_cursor()
            self.cursor = self._pos_from_line_col(lines, cur_line, len(lines[cur_line]))
            self.needs_display_update = True
            return

        # Enter
        if keycode == ecodes.KEY_ENTER:
            self.text = self.text[:self.cursor] + "\n" + self.text[self.cursor:]
            self.cursor += 1
            self.dirty = True
            self.needs_display_update = True
            return

        # Backspace
        if keycode == ecodes.KEY_BACKSPACE:
            if self.cursor > 0:
                self.text = self.text[:self.cursor - 1] + self.text[self.cursor:]
                self.cursor -= 1
                self.dirty = True
                self.needs_display_update = True
            return

        # Delete
        if keycode == ecodes.KEY_DELETE:
            if self.cursor < len(self.text):
                self.text = self.text[:self.cursor] + self.text[self.cursor + 1:]
                self.dirty = True
                self.needs_display_update = True
            return

        # Regular characters - insert at cursor position
        if keycode in self.keymap:
            normal, shifted = self.keymap[keycode]
            char = shifted if self.shift_held else normal
            self.text = self.text[:self.cursor] + char + self.text[self.cursor:]
            self.cursor += len(char)
            self.dirty = True
            self.needs_display_update = True

    # --- Sleep / wake ---

    def _sleep_mode(self):
        """Save, show goodbye screen, put display to sleep, wait for Ctrl+Q to wake."""
        print("Entering sleep mode...")

        # Show goodbye screen
        if self.epd:
            try:
                self.epd.init()
                self.epd.clear(color=0xFF)
                self.epd.sleep()
            except Exception:
                pass

        # Wait for Ctrl+Q on keyboard
        print("Sleeping. Press Ctrl+Q to wake up...")
        self._wait_for_wake()

        # Wake up: reinitialize display and resume
        print("Waking up...")
        self.epd.init()
        img = self.render()
        self.epd.display(list(img.tobytes()))
        self.epd.init_partial()
        self.needs_display_update = False
        print("Resumed.")

    def _wait_for_wake(self):
        """Block until Ctrl+Q is pressed again on the keyboard."""
        ctrl_held = False
        while self.running:
            if self.keyboard is None:
                self.keyboard = self._find_keyboard()
                if self.keyboard is None:
                    time.sleep(1)
                    continue
            try:
                r, _, _ = select.select([self.keyboard.fd], [], [], 1.0)
                if not r:
                    continue
                for event in self.keyboard.read():
                    if event.type != ecodes.EV_KEY:
                        continue
                    if event.code in (ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL):
                        ctrl_held = event.value != 0
                    elif event.code == ecodes.KEY_Q and event.value == 1 and ctrl_held:
                        return
            except OSError:
                self.keyboard = None
                time.sleep(1)

    # --- File server mode (Bluetooth PAN) ---

    BT_PAN_IP = "10.44.0.1"
    BT_PAN_BRIDGE = "pan0"
    BT_PAN_PORT = 443
    BT_PAN_TIMEOUT = 300  # auto-shutdown after 5 minutes
    BT_AGENT_PATH = "/etyper/agent"
    BT_CERT_DIR = os.path.join(DOCS_DIR, ".ssl")  # persistent across reboots

    def _file_server_mode(self):
        """Start Bluetooth PAN + web server, show instructions, wait for Ctrl+F."""
        if not HAS_DBUS:
            print("ERROR: python3-dbus and python3-gi required for file server.")
            return

        self.save_document()

        url = f"https://{self.BT_PAN_IP}"
        timeout_min = self.BT_PAN_TIMEOUT // 60

        # Show instructions on e-paper
        self.epd.init()
        img = Image.new("1", (PORTRAIT_W, PORTRAIT_H), 255)
        draw = ImageDraw.Draw(img)

        y = MARGIN_Y + 10
        draw.text((MARGIN_X, y), "-- File Server --", font=self.font, fill=0)
        y += self.line_h * 2
        draw.text((MARGIN_X, y), "1. Pair Bluetooth", font=self.font, fill=0)
        y += self.line_h
        draw.text((MARGIN_X, y), "   with \"etyper\"", font=self.font, fill=0)
        y += self.line_h * 2
        draw.text((MARGIN_X, y), "2. Open browser:", font=self.font, fill=0)
        y += self.line_h
        draw.text((MARGIN_X, y), f"   {url}", font=self.font, fill=0)
        y += self.line_h * 2
        draw.text((MARGIN_X, y), f"Auto-off: {timeout_min} min", font=self.font, fill=0)
        y += self.line_h
        draw.text((MARGIN_X, y), "Ctrl+F to stop", font=self.font, fill=0)

        img_landscape = img.rotate(0)
        self.epd.display(list(img_landscape.tobytes()))

        # Start Bluetooth PAN and file server
        bt_state = self._start_bt_pan()
        if bt_state is None:
            print("Could not start Bluetooth PAN, aborting.")
            self._resume_typewriter_display()
            return

        server = None
        http_server = None
        try:
            server = self._start_file_server(self.BT_PAN_PORT)
            if server is None:
                print("Could not start HTTPS server.")
                return

            # Also start plain HTTP on port 8080 as fallback for devices
            # that don't handle self-signed certs well
            http_server = self._start_file_server(8080, use_ssl=False)

            print(f"File server ready at {url}")

            # Wait for Ctrl+F or timeout
            self._wait_for_key_or_timeout(ecodes.KEY_F, self.BT_PAN_TIMEOUT)
        finally:
            # Guaranteed cleanup regardless of exceptions
            print("Stopping file server...")
            if server:
                try:
                    server.shutdown()
                except Exception:
                    pass
            if http_server:
                try:
                    http_server.shutdown()
                except Exception:
                    pass
            self._stop_bt_pan(bt_state)
            print("File server stopped.")

        self._resume_typewriter_display()

    def _resume_typewriter_display(self):
        """Reinitialize display and show typewriter screen."""
        time.sleep(1)
        self.epd.init()
        img = self.render()
        self.epd.display(list(img.tobytes()))
        self.epd.init_partial()
        self.needs_display_update = False

    def _start_bt_pan(self):
        """Set up Bluetooth PAN: agent, bridge, NAP, DHCP. Returns state dict or None."""
        print("Starting Bluetooth PAN...")

        try:
            # Set up D-Bus mainloop only once (repeated calls cause conflicts)
            if not self._dbus_mainloop_set:
                dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
                self._dbus_mainloop_set = True

            # Reuse bus connection (D-Bus returns same conn anyway, but be explicit)
            if self._bt_bus is None:
                self._bt_bus = dbus.SystemBus()
            bus = self._bt_bus

            # Power on adapter
            props = dbus.Interface(
                bus.get_object("org.bluez", "/org/bluez/hci0"),
                "org.freedesktop.DBus.Properties",
            )
            props.Set("org.bluez.Adapter1", "Powered", True)
            time.sleep(0.5)

            # Register auto-accept agent (reuse if already created)
            if self._bt_agent is None:
                self._bt_agent = _BtAutoAcceptAgent(bus, self.BT_AGENT_PATH)
            mgr = dbus.Interface(
                bus.get_object("org.bluez", "/org/bluez"),
                "org.bluez.AgentManager1",
            )
            try:
                mgr.UnregisterAgent(self.BT_AGENT_PATH)
            except Exception:
                pass
            mgr.RegisterAgent(self.BT_AGENT_PATH, "DisplayYesNo")
            mgr.RequestDefaultAgent(self.BT_AGENT_PATH)
            print("  BT agent registered (auto-accept)")

            # Make adapter discoverable and pairable
            props.Set("org.bluez.Adapter1", "Alias", "etyper")
            props.Set("org.bluez.Adapter1", "Discoverable", True)
            props.Set("org.bluez.Adapter1", "DiscoverableTimeout", dbus.UInt32(0))
            props.Set("org.bluez.Adapter1", "Pairable", True)
            props.Set("org.bluez.Adapter1", "PairableTimeout", dbus.UInt32(0))
            print("  BT adapter: etyper, discoverable, pairable")

            # Create bridge for PAN
            subprocess.run(["ip", "link", "del", self.BT_PAN_BRIDGE],
                           capture_output=True)
            time.sleep(0.5)
            r = subprocess.run(
                ["ip", "link", "add", self.BT_PAN_BRIDGE, "type", "bridge"],
                capture_output=True, text=True,
            )
            if r.returncode != 0:
                print(f"  Bridge creation failed: {r.stderr}")
                mgr.UnregisterAgent(self.BT_AGENT_PATH)
                return None
            subprocess.run(
                ["ip", "addr", "add", f"{self.BT_PAN_IP}/24", "dev", self.BT_PAN_BRIDGE],
                capture_output=True,
            )
            subprocess.run(
                ["ip", "link", "set", self.BT_PAN_BRIDGE, "up"],
                capture_output=True,
            )
            subprocess.run(["sysctl", "-w", "net.ipv4.ip_forward=1"],
                           capture_output=True)
            print(f"  Bridge {self.BT_PAN_BRIDGE} up @ {self.BT_PAN_IP}")

            # Register NAP server on the bridge
            net_server = dbus.Interface(
                bus.get_object("org.bluez", "/org/bluez/hci0"),
                "org.bluez.NetworkServer1",
            )
            try:
                net_server.Unregister("nap")
            except Exception:
                pass
            net_server.Register("nap", self.BT_PAN_BRIDGE)
            print(f"  NAP server registered on {self.BT_PAN_BRIDGE}")

            # Start dnsmasq for DHCP
            dnsmasq = subprocess.Popen([
                "dnsmasq",
                f"--interface={self.BT_PAN_BRIDGE}",
                "--except-interface=lo",
                "--bind-interfaces",
                "--dhcp-range=10.44.0.10,10.44.0.50,255.255.255.0,1h",
                "--no-daemon", "--no-resolv", "--log-facility=-",
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print("  DHCP server running")

            # Start GLib mainloop for D-Bus event processing
            loop = GLib.MainLoop()
            loop_thread = threading.Thread(target=loop.run, daemon=True)
            loop_thread.start()

            print("Bluetooth PAN ready. Waiting for connections...")
            return {
                "bus": bus,
                "agent": self._bt_agent,
                "mgr": mgr,
                "props": props,
                "net_server": net_server,
                "dnsmasq": dnsmasq,
                "loop": loop,
            }

        except Exception as e:
            print(f"BT PAN setup failed: {e}")
            # Best-effort cleanup of anything partially started
            try:
                dnsmasq.terminate()
            except Exception:
                pass
            subprocess.run(["ip", "link", "del", self.BT_PAN_BRIDGE],
                           capture_output=True)
            self._bt_power_off()
            return None

    def _stop_bt_pan(self, state):
        """Tear down Bluetooth PAN, disconnect devices, and power off Bluetooth.

        Each step is wrapped individually so one failure doesn't prevent the rest.
        """
        print("Stopping Bluetooth PAN...")

        # 1. Stop DHCP server
        try:
            state["dnsmasq"].terminate()
            state["dnsmasq"].wait(timeout=3)
        except Exception as e:
            print(f"  dnsmasq stop warning: {e}")
            try:
                state["dnsmasq"].kill()
            except Exception:
                pass

        # 2. Stop GLib mainloop
        try:
            state["loop"].quit()
        except Exception as e:
            print(f"  mainloop stop warning: {e}")

        # 3. Unregister NAP server
        try:
            state["net_server"].Unregister("nap")
        except Exception as e:
            print(f"  NAP unregister warning: {e}")

        # 4. Make adapter non-discoverable
        try:
            state["props"].Set("org.bluez.Adapter1", "Discoverable", False)
            state["props"].Set("org.bluez.Adapter1", "Pairable", False)
        except Exception as e:
            print(f"  adapter config warning: {e}")

        # 5. Unregister agent from BlueZ (keep self._bt_agent for reuse)
        try:
            state["mgr"].UnregisterAgent(self.BT_AGENT_PATH)
        except Exception as e:
            print(f"  agent unregister warning: {e}")

        # 6. Disconnect all connected BT devices
        self._bt_disconnect_all()

        # 7. Remove bridge
        subprocess.run(["ip", "link", "del", self.BT_PAN_BRIDGE],
                       capture_output=True)

        # 8. Power off Bluetooth adapter
        self._bt_power_off()
        print("Bluetooth PAN stopped.")

    @staticmethod
    def _bt_power_off():
        """Power off the Bluetooth adapter via bluetoothctl."""
        try:
            subprocess.run(
                ["bluetoothctl", "power", "off"],
                capture_output=True, timeout=5,
            )
            print("  BT adapter powered off")
        except Exception:
            pass

    @staticmethod
    def _bt_disconnect_all():
        """Disconnect (but keep pairing of) all Bluetooth devices.

        We keep pairings so returning devices can reconnect without
        re-pairing each time Ctrl+F is used.
        """
        try:
            r = subprocess.run(
                ["bluetoothctl", "devices", "Connected"],
                capture_output=True, text=True, timeout=5,
            )
            for line in r.stdout.strip().splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    mac = parts[1]
                    subprocess.run(
                        ["bluetoothctl", "disconnect", mac],
                        capture_output=True, timeout=5,
                    )
                    print(f"  BT: disconnected {mac}")
        except Exception as e:
            print(f"  BT disconnect warning: {e}")

    @classmethod
    def _cleanup_stale_bt(cls):
        """Clean up leftover BT state from a previous crash (stale bridge, dnsmasq, etc)."""
        # Kill any stale dnsmasq bound to the PAN bridge
        try:
            r = subprocess.run(
                ["pgrep", "-f", f"dnsmasq.*{cls.BT_PAN_BRIDGE}"],
                capture_output=True, text=True, timeout=3,
            )
            for pid in r.stdout.strip().splitlines():
                pid = pid.strip()
                if pid:
                    subprocess.run(["kill", pid], capture_output=True, timeout=3)
                    print(f"  Killed stale dnsmasq (pid {pid})")
        except Exception:
            pass

        # Remove stale bridge
        subprocess.run(
            ["ip", "link", "del", cls.BT_PAN_BRIDGE],
            capture_output=True,
        )

        # Power off adapter
        cls._bt_power_off()

    def _ensure_ssl_cert(self):
        """Generate a self-signed SSL certificate if one doesn't exist."""
        os.makedirs(self.BT_CERT_DIR, exist_ok=True)
        cert_file = os.path.join(self.BT_CERT_DIR, "cert.pem")
        key_file = os.path.join(self.BT_CERT_DIR, "key.pem")

        if os.path.exists(cert_file) and os.path.exists(key_file):
            return cert_file, key_file

        print("  Generating SSL certificate...")
        r = subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", key_file, "-out", cert_file,
            "-days", "3650", "-nodes",
            "-subj", "/CN=etyper/O=etyper",
        ], capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  SSL cert generation failed: {r.stderr}")
            return None, None
        return cert_file, key_file

    def _start_file_server(self, port, use_ssl=True):
        """Start a threaded HTTP(S) server serving the documents directory."""
        docs_dir = DOCS_DIR

        cert_file, key_file = None, None
        if use_ssl:
            cert_file, key_file = self._ensure_ssl_cert()
            if cert_file is None:
                print("Could not generate SSL certificate.")
                return None

        class DocsHandler(SimpleHTTPRequestHandler):
            """Serve document listing and file downloads."""

            def do_GET(self):
                if self.path == "/" or self.path == "":
                    self._serve_index()
                elif self.path == "/download-all":
                    self._serve_zip()
                elif self.path.startswith("/dl/"):
                    self._serve_file(unquote(self.path[4:]))
                else:
                    self.send_error(404)

            def _serve_index(self):
                files = sorted(
                    f for f in os.listdir(docs_dir)
                    if f.endswith(".txt") and f.startswith("doc_")
                )
                html = (
                    "<!DOCTYPE html><html><head>"
                    "<meta charset='utf-8'>"
                    "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                    "<title>etyper documents</title>"
                    "<style>"
                    "body{font-family:system-ui,sans-serif;max-width:600px;"
                    "margin:2em auto;padding:0 1em;background:#f8f8f8;color:#222}"
                    "h1{font-size:1.4em;border-bottom:2px solid #222;padding-bottom:.3em}"
                    "a{color:#222;text-decoration:none;display:block;padding:.7em;"
                    "margin:.3em 0;background:#fff;border:1px solid #ccc;border-radius:4px}"
                    "a:hover{background:#eee}"
                    ".meta{color:#888;font-size:.85em}"
                    ".dl-all{text-align:center;margin-top:1.5em}"
                    ".dl-all a{display:inline-block;background:#222;color:#fff;"
                    "padding:.6em 1.5em;border:none}"
                    ".dl-all a:hover{background:#444}"
                    "</style></head><body>"
                    "<h1>etyper documents</h1>"
                )
                if not files:
                    html += "<p>No documents yet.</p>"
                else:
                    for f in reversed(files):
                        fpath = os.path.join(docs_dir, f)
                        size = os.path.getsize(fpath)
                        if size < 1024:
                            size_str = f"{size} B"
                        else:
                            size_str = f"{size / 1024:.1f} KB"
                        html += (
                            f"<a href='/dl/{quote(f)}'>"
                            f"{f} <span class='meta'>({size_str})</span></a>"
                        )
                    html += (
                        "<div class='dl-all'>"
                        "<a href='/download-all'>Download all as .zip</a>"
                        "</div>"
                    )
                html += "</body></html>"
                data = html.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", len(data))
                self.end_headers()
                self.wfile.write(data)

            def _serve_file(self, filename):
                fpath = os.path.join(docs_dir, os.path.basename(filename))
                if not os.path.isfile(fpath):
                    self.send_error(404)
                    return
                data = open(fpath, "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Disposition",
                                 f"attachment; filename=\"{os.path.basename(fpath)}\"")
                self.send_header("Content-Length", len(data))
                self.end_headers()
                self.wfile.write(data)

            def _serve_zip(self):
                import zipfile
                import io
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                    for f in os.listdir(docs_dir):
                        if f.endswith(".txt") and f.startswith("doc_"):
                            zf.write(os.path.join(docs_dir, f), f)
                data = buf.getvalue()
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition",
                                 "attachment; filename=\"etyper_docs.zip\"")
                self.send_header("Content-Length", len(data))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format, *args):
                print(f"  [http] {args[0]}")

        class _ReuseHTTPServer(HTTPServer):
            allow_reuse_address = True

        try:
            server = _ReuseHTTPServer(("0.0.0.0", port), DocsHandler)
            if use_ssl and cert_file:
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                ctx.load_cert_chain(cert_file, key_file)
                server.socket = ctx.wrap_socket(server.socket, server_side=True)
        except OSError as e:
            print(f"Could not start server on port {port}: {e}")
            return None

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server

    def _wait_for_key_or_timeout(self, target_key, timeout=0):
        """Block until Ctrl+<target_key> is pressed or timeout expires (0=no timeout)."""
        ctrl_held = False
        start = time.time()
        while self.running:
            if timeout > 0 and time.time() - start >= timeout:
                print("Timeout reached.")
                return
            if self.keyboard is None:
                self.keyboard = self._find_keyboard()
                if self.keyboard is None:
                    time.sleep(1)
                    continue
            try:
                r, _, _ = select.select([self.keyboard.fd], [], [], 1.0)
                if not r:
                    continue
                for event in self.keyboard.read():
                    if event.type != ecodes.EV_KEY:
                        continue
                    if event.code in (ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL):
                        ctrl_held = event.value != 0
                    elif event.code == target_key and event.value == 1 and ctrl_held:
                        return
            except OSError:
                self.keyboard = None
                time.sleep(1)

    # --- Main loop ---

    def run(self):
        """Start the typewriter."""
        print("=== etyper - E-Paper Typewriter ===")

        # Clean up stale BT state from previous crashes
        if HAS_DBUS:
            self._cleanup_stale_bt()

        print("Initializing display...")
        self.epd = EPD42()

        self.font = self._find_font()
        self._calc_text_metrics()
        print(f"Text area: {self.chars_per_line} chars x {self.lines_per_page} lines")

        self._load_layout_pref()
        self.load_document()

        print("Initial display refresh...")
        self.epd.init()
        img = self.render()
        self.epd.display(list(img.tobytes()))
        self.epd.init_partial()

        self.keyboard = self._find_keyboard()

        self.running = True
        self.last_save_time = time.time()
        self.needs_display_update = False

        def signal_handler(sig, frame):
            self.running = False
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        print("Ready! Start typing...")
        print("  Arrows: move  |  Ctrl+S: save  |  Ctrl+N: new  |  Ctrl+R: refresh  |  Ctrl+K: layout  |  Ctrl+Q: sleep/wake")

        try:
            self._main_loop()
        finally:
            self._shutdown()

    def _main_loop(self):
        """Event loop: read keyboard, update display, autosave."""
        while self.running:
            if self.keyboard is None:
                self.keyboard = self._find_keyboard()
                if self.keyboard is None:
                    time.sleep(1)
                    self._check_autosave()
                    continue

            try:
                r, _, _ = select.select([self.keyboard.fd], [], [], 0.5)

                if r:
                    for event in self.keyboard.read():
                        if event.type == ecodes.EV_KEY:
                            self._handle_key(event.code, event.value)

                if self.needs_display_update:
                    img = self.render()
                    self.epd.display_image_partial(img)
                    self.needs_display_update = False

                self._check_autosave()

            except OSError:
                print("Keyboard disconnected, waiting...")
                self.keyboard = None
                time.sleep(1)

    def _check_autosave(self):
        if self.dirty and (time.time() - self.last_save_time >= AUTOSAVE_INTERVAL):
            self.save_document()
            print(f"Autosaved: {self.doc_path}")

    def _shutdown(self):
        print("\nShutting down...")
        if self.dirty:
            self.save_document()
            print(f"Saved: {self.doc_path}")

        if self.epd:
            try:
                self.epd.init()
                self.epd.clear(color=0xFF)
                self.epd.sleep()
            except Exception:
                pass
            self.epd.close()

        print("Done.")


def main():
    if not HAS_EVDEV:
        print("ERROR: python3-evdev is required.")
        print("Install it: sudo apt-get install python3-evdev")
        sys.exit(1)

    app = EtyperApp()
    app.run()


if __name__ == "__main__":
    main()
