A fork of [etyper](https://github.com/Quackieduckie/etyper) designed to work with the Raspberry Pi Zero 2 W. 

Tested on the **Raspberry Pi Zero 2 W** running **64-bit Raspberry Pi OS Lite (Bookworm)** with a **Waveshare 4.2" V2 e-Paper Module**. Running it on Trixie might cause problems due to its use of `libgpiod2` (or at least that's my understanding).

This version has also been found to support wireless keyboard connections via a 2.4GHz USB receiver.

To run this fork on the Pi, you need to set the line values of GPIO 27 and GPIO 23 to 0 and connect CS and RST to the corresponding pins. Set the GPIO line values by running `gpioset gpiochip0 27=0 23=0`.

**Wiring**

| Display Pin | Header Pin | GPIO       | Function           |
|-------------|-----------|------------|--------------------|
| DIN / MOSI  | Pin 19    | 10         | SPI0 MOSI (hardware) |
| CLK / SCK   | Pin 23    | 11         | SPI0 CLK (hardware)  |
| CS          | Pin 13    | 27         | Chip Select (GPIO)   |
| DC          | Pin 22    | 25         | Data/Command (GPIO)  |
| RST         | Pin 16    | 23         | Reset (GPIO)         |
| BUSY        | Pin 18    | 24         | Busy signal (GPIO input) |
| VCC         | Pin 17    | 3.3V       | Power               |
| GND         | Pin 20    | GND        | Ground               |

# etyper

> **Disclaimer**: This project was mostly generated with AI assistance (Claude / Cursor).
> It has been tested on real hardware but may contain bugs or suboptimal patterns.
> Contributions and corrections are welcome.

E-Paper display driver and distraction-free typewriter for the **WeAct Studio 4.2" E-Paper Module** (SSD1683, 400x300 B/W) on the **Orange Pi Zero 2W** (Allwinner H618, Armbian).

## Hardware

- **Display**: WeAct Studio 4.2" E-Paper (SSD1683 controller, 400x300px)
  - Compatible with Waveshare 4.2" V2 displays
- **SBC**: Orange Pi Zero 2W (Allwinner H618, 64-bit ARM)
- **OS**: Armbian (Debian-based)

## Wiring

Follows the [WeAct Studio Raspberry Pi pinout](https://github.com/WeActStudio/WeActStudio.EpaperModule):

| Display Pin | Header Pin | GPIO       | Function           |
|-------------|-----------|------------|--------------------|
| DIN / MOSI  | Pin 19    | PH7 (231)  | SPI1 MOSI (hardware) |
| CLK / SCK   | Pin 23    | PH6 (230)  | SPI1 CLK (hardware)  |
| CS          | Pin 24    | PH5 (229)  | Chip Select (GPIO)   |
| DC          | Pin 22    | PI6 (262)  | Data/Command (GPIO)  |
| RST         | Pin 11    | PH2 (226)  | Reset (GPIO)         |
| BUSY        | Pin 18    | PH4 (228)  | Busy signal (GPIO input) |
| VCC         | Pin 17    | 3.3V       | Power               |
| GND         | Pin 20    | GND        | Ground               |

> **Important**: CS is controlled via GPIO (not hardware SPI CS). The hardware SPI1 CS1 on Pin 26 (PH9) is **not used**. This is because the WeAct pinout places CS on Pin 24, which is not the Orange Pi's SPI1 CS pin.

## Typewriter Mode

etyper includes a distraction-free typewriter application inspired by [ZeroWriter](https://github.com/zerowriter/zerowriter1).

**Features:**
- Portrait display (rotated 90 CCW, 300x400 effective resolution)
- Auto-opens last document on startup
- Autosave every 10 seconds
- USB keyboard input (any standard USB keyboard)
- Switchable keyboard layout: US QWERTY, UK QWERTY, DE QWERTZ, FR AZERTY, ES QWERTY, IT QWERTY, SE QWERTY, NO/DK QWERTY, Colemak, US DVORAK (Ctrl+K)
- Full text editing with arrow key cursor movement
- Word wrap, auto-scrolling to follow cursor
- Partial refresh for fast typing response (~0.5s per update)
- Time-based full refresh every 5 minutes to clean e-paper ghosting
- Auto-start on boot via systemd service
- Survives power outages (autosave + auto-start + e-paper retains image)

### Keyboard Commands

**Typing:**
| Key | Action |
|-----|--------|
| A-Z, 0-9, symbols | Insert character at cursor position |
| Space | Insert space |
| Enter | Insert new line |
| Tab | Insert 4 spaces |
| Backspace | Delete character before cursor |
| Delete | Delete character after cursor |

**Cursor movement:**
| Key | Action |
|-----|--------|
| Left arrow | Move cursor one character left |
| Right arrow | Move cursor one character right |
| Up arrow | Move cursor up one visual line |
| Down arrow | Move cursor down one visual line |
| Home | Jump to start of current line |
| End | Jump to end of current line |

**Shortcuts:**
| Shortcut | Action |
|----------|--------|
| Ctrl+S | Save document |
| Ctrl+N | Save current and create new document |
| Ctrl+Left | Switch to previous document |
| Ctrl+Right | Switch to next document |
| Ctrl+F | Toggle file server via Bluetooth (download docs in browser) |
| Ctrl+K | Choose keyboard layout (Up/Down to browse, Enter to select, Esc to cancel) |
| Ctrl+R | Force full display refresh (cleans ghosting) |
| Ctrl+Q | Sleep / wake toggle (saves on sleep) |

### Status Bar

The bottom of the screen shows: `*doc_20260115_143022.txt L12:5 482c`
- `*` = unsaved changes (disappears after save/autosave)
- `L12:5` = cursor at line 12, column 5
- `482c` = total character count

### Running

**Run manually:**
```bash
sudo python3 typewriter.py
```

**Run as boot service (auto-starts on power on):**
```bash
sudo bash install.sh
# Or manually (replace /path/to/etyper with your actual install path):
sed "s|__INSTALL_DIR__|/path/to/etyper|g" etyper.service | sudo tee /etc/systemd/system/etyper.service
sudo systemctl daemon-reload
sudo systemctl enable --now etyper
```

**Service management:**
```bash
sudo systemctl status etyper    # Check status
sudo systemctl stop etyper      # Stop
sudo systemctl start etyper     # Start
sudo systemctl restart etyper   # Restart after code changes
journalctl -u etyper -f         # View live logs
```

### Documents

- Saved to `~/etyper_docs/` as plain `.txt` files
- Filenames are timestamped: `doc_20260115_143022.txt`
- Last opened document is tracked in `~/etyper_docs/.last_doc`
- On startup, the last document is automatically reopened with cursor at the end

### File Transfer (Ctrl+F)

Download your documents wirelessly via Bluetooth PAN (Personal Area Network). No WiFi required — the etyper creates its own network over Bluetooth.

**How it works:**
1. Press **Ctrl+F** — Bluetooth powers on, the file server starts, and instructions appear on screen
2. On your computer, open **Bluetooth settings** and pair with **"etyper"** (auto-accepts, no PIN needed)
3. Once paired, open a browser and go to **`https://10.44.0.1`** (accept the certificate warning)
4. Download individual documents or all at once as a `.zip` file
5. Press **Ctrl+F** again to stop — devices are disconnected and Bluetooth powers off

**Notes:**
- Bluetooth is **off by default** and only activates during file transfer
- Auto-shuts down after **5 minutes** if you forget to stop it
- **Pairings are preserved** — once you pair a device, it can reconnect next time without re-pairing
- SSL certificate persists across reboots (stored in `~/etyper_docs/.ssl/`)
- Survives crashes: stale bridges and DHCP servers are cleaned up automatically on startup
- Works best with desktop/laptop browsers — phone Bluetooth PAN support varies by device
- If your browser forces HTTPS errors, try `http://10.44.0.1:8080` as a fallback
- Requires `python3-dbus`, `python3-gi`, and `dnsmasq` on the Pi

---

## Setup

### 1. Enable SPI1

Add the SPI1 device tree overlay in `/boot/armbianEnv.txt`:

```
overlays=spidev1_1
```

Reboot. Verify `/dev/spidev1.1` exists:

```bash
ls /dev/spidev*
```

### 2. Install dependencies and service

The easiest way is to use the included installer, which installs all dependencies, disables the conflicting system dnsmasq service, and optionally sets up auto-start on boot:

```bash
cd etyper
sudo bash install.sh
```

Or install manually:

```bash
apt-get update
apt-get install python3-spidev python3-libgpiod python3-pil python3-evdev \
               python3-dbus python3-gi dnsmasq openssl
systemctl disable --now dnsmasq   # prevent conflict with etyper's own instance
```

> `python3-libgpiod`, `python3-evdev`, `python3-dbus`, and `python3-gi` must be installed via apt (not pip).
> `dnsmasq` is required for Bluetooth file transfer. The system dnsmasq service must be disabled to avoid a port conflict.

### 3. Run the typewriter

```bash
sudo python3 typewriter.py
```

Or to test the display separately:

```bash
python3 examples/hello_world.py
```

## Usage

```python
from epd42_driver import EPD42
from PIL import Image, ImageDraw, ImageFont

with EPD42() as epd:
    # Initialize and clear
    epd.init()
    epd.clear()
    epd.sleep()

    # Create an image
    img = Image.new("1", (epd.width, epd.height), 255)  # white background
    draw = ImageDraw.Draw(img)
    draw.text((50, 100), "Hello!", fill=0)

    # Display it
    epd.init()
    epd.display_image(img)
    epd.sleep()
```

### API Reference

| Method | Description |
|--------|-------------|
| `EPD42(pins, spi_bus, spi_dev, spi_speed, spi_mode, gpiochip)` | Constructor with optional config |
| `epd.init()` | Initialize display for full refresh |
| `epd.init_partial()` | Switch to partial refresh mode (call after `init` + `display`) |
| `epd.display(buffer)` | Write raw buffer and full refresh (~4s) |
| `epd.display_partial(buffer)` | Write raw buffer and partial refresh (~0.5s) |
| `epd.display_image(image)` | Display a PIL Image with full refresh |
| `epd.display_image_partial(image)` | Display a PIL Image with partial refresh |
| `epd.full_refresh(buffer)` | Force a full refresh to clean ghosting |
| `epd.clear(color=0xFF)` | Clear to white (0xFF) or black (0x00) |
| `epd.sleep()` | Enter deep sleep (requires `init()` to wake) |
| `epd.reset()` | Hardware reset |
| `epd.close()` | Release GPIO and SPI resources |
| `EPD42.getbuffer(image)` | Static: convert PIL Image to raw buffer |

### Custom pin mapping

```python
epd = EPD42(pins={
    "dc": 262,
    "cs": 229,
    "rst": 226,
    "busy": 228,
})
```

## Technical Details

- **SPI**: Hardware SPI1 at 4MHz, Mode 0. CS managed via GPIO.
- **Controller**: SSD1683 (Solomon Systech)
- **Full refresh**: ~4 seconds, no ghosting. Used on startup and every 5 minutes.
- **Partial refresh**: ~0.5 seconds, slight ghosting. Used for typing updates.
- **Display buffer**: 15,000 bytes (400/8 * 300). 1 bit per pixel, MSB first. 1=white, 0=black.
- **Deep sleep**: ~1uA current draw. Requires hardware reset to wake.
- **Typewriter font**: [Atkinson Hyperlegible Mono](https://github.com/googlefonts/atkinson-hyperlegible-next-mono) Medium 16px, 28 chars x 15 lines in portrait mode. Line height follows WCAG 1.5x recommendation. Designed by the Braille Institute for maximum legibility on low-resolution displays. Falls back to DejaVu Sans Mono if not found. Licensed under SIL Open Font License 1.1.
- **File transfer**: Bluetooth PAN (NAP) with auto-accept D-Bus agent, bridge networking, dnsmasq DHCP, and HTTPS (self-signed cert) + HTTP fallback. Bluetooth is powered off when not in use. Pairings are preserved across sessions; stale state is cleaned up on startup for crash resilience.

## Project Structure

```
etyper/
  epd42_driver.py          # E-paper display driver (full + partial refresh)
  typewriter.py            # Typewriter application
  install.sh               # Installer (dependencies + systemd service)
  etyper.service           # systemd unit file
  requirements.txt         # Python dependencies
  README.md                # This file
  fonts/
    AtkinsonHyperlegibleMono-Medium.ttf    # Primary display font
    AtkinsonHyperlegibleMono-SemiBold.ttf  # Heavier variant
    AtkinsonHyperlegibleMono-Regular.ttf   # Lighter variant
    AtkinsonHyperlegibleMono-Bold.ttf      # Bold variant
    OFL.txt                                # SIL Open Font License
  examples/
    hello_world.py         # Basic "Hello World" demo
    test_patterns.py       # Diagnostic test patterns
```

## Credits

- [WeAct Studio](https://github.com/WeActStudio/WeActStudio.EpaperModule) - Display manufacturer & reference C driver
- [Waveshare](https://github.com/waveshare/e-Paper) - Compatible Python driver reference
- [Atkinson Hyperlegible Mono](https://github.com/googlefonts/atkinson-hyperlegible-next-mono) - Display font by the Braille Institute (SIL OFL 1.1)
