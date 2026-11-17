# IDO `.iwf` Watch Face Format (TOOBUR IDW20 / VeryFit)

Reverse-engineered from one sample: face `f468`, cached by the VeryFit app
(`ido_watch_plate_data.iwf`, 963,566 bytes, target device `IDW20`).

**Status:** the container and the RAW image format are decoded and verified
against the face's own `preview.png`. Semantics of some manifest fields and a
few header bytes are inferred or unknown, and they are marked below.

Confidence tags: **[verified]** reproduced by working code and checked visually,
**[inferred]** consistent with the sample but not proven, **[unknown]** not yet understood.

---

## 1. Overview

An `.iwf` file is a flat archive with no compression and no encryption. It holds:

1. `iwf.json`, a manifest that places text widgets on the screen.
2. `font.json`, which lists the glyph sets ("fonts") used by those widgets.
3. Bitmap assets in a custom `RAW` format: preview, background and one bitmap per glyph.

The watch face is composed from a background image plus digit and label bitmaps
drawn at manifest-defined positions. There is no scripting.

Byte order: **container and RAW header fields are little-endian. RGB565 pixel data is big-endian.**

---

## 2. Container layout [verified]

| Offset | Size | Field |
|---|---|---|
| 0x00 | 4 | Magic `69 77 66 00` (`"iwf\0"`) |
| 0x04 | 2 | Format version, `u16` (sample = 1) |
| 0x06 | 2 | Entry count N, `u16` (sample = 72) |
| 0x08 | 40 × N | Entry table |
| 8 + 40·N | | Entry data (sample: 0xB48) |

Entry table record (40 bytes):

| Offset | Size | Field |
|---|---|---|
| 0 | 32 | Name, ASCII, NUL-padded, no extension tricks (names like `g626_0`, `preview.png`) |
| 32 | 4 | Absolute file offset, `u32` |
| 36 | 4 | Size in bytes, `u32` |

Observations from the sample:

- Entry data is packed back-to-back in table order, with no gaps or padding.
  The last entry ends exactly at end of file.
- No checksum, signature or trailer was found [inferred: none exists in this file].
- Entry order: `iwf.json`, `font.json`, `preview.png`, background, then glyph sets in manifest order.
- Names ending in `.png` (`preview.png`, `files61.png`) are **not PNG**. They are RAW images (section 4).
  Glyph entries have no extension and are also RAW.

### Reference parser

```python
import struct

def read_iwf(path):
    d = open(path, "rb").read()
    magic, ver, n = struct.unpack_from("<4sHH", d, 0)
    assert magic == b"iwf\0"
    entries = {}
    for i in range(n):
        o = 8 + 40 * i
        name = d[o:o+32].rstrip(b"\0").decode()
        off, size = struct.unpack_from("<II", d, o + 32)
        entries[name] = d[off:off+size]
    return ver, entries
```

---

## 3. Manifest: `iwf.json` [verified structure, inferred semantics]

UTF-8 JSON. Sample (abridged):

```json
{
  "version": 1,
  "clouddialversion": 3,
  "preview": "preview.png",
  "name": "f468",
  "author": "<creator name, escaped unicode>",
  "description": "IDW20",
  "deviceId": "IDW20",
  "bluetooth": false,
  "disturb": false,
  "battery": false,
  "compress": "LZ4",
  "environment": "Production",
  "item": [ { "widget": "custom", "type": "hour", "x": 15, "y": 12,
              "w": 188, "h": 100, "fgcolor": "0xFFFFFFFF",
              "fgrender": "0xFFFFFFFF", "align": "left",
              "font": "g626", "fontnum": 10, "isShowAddwh": true } ],
  "bkground": "files61.png"
}
```

### Top-level fields

| Field | Meaning |
|---|---|
| `version` | Manifest version (1). |
| `clouddialversion` | Cloud dial schema version (3). [inferred] |
| `preview` | Entry name of the preview image. |
| `name` | Face ID (`f468`). |
| `author` | Creator name. |
| `description`, `deviceId` | Both `IDW20`, the target device model. |
| `bluetooth`, `disturb`, `battery` | Booleans, all `false` here. [unknown] Likely toggles for built-in status icons. |
| `compress` | `"LZ4"`. **Not reflected in the file**: no entry is compressed (section 4). [unknown] May be applied at transfer time or be vestigial. |
| `environment` | `"Production"`. |
| `bkground` | Entry name of the full-screen background image. |
| `item` | Array of widgets (below). |

### Widget fields (`item[]`)

| Field | Meaning |
|---|---|
| `widget` | Always `"custom"` in this sample. |
| `type` | Data source: `hour`, `min`, `day`, `week`, `battery`, `step`, `calorie`. |
| `x`, `y` | Top-left of the widget box in screen pixels. |
| `w`, `h` | Box size. For `hour`/`min` the box (188×100, 190×100) is wider than one glyph (94×100), so it holds multiple glyphs. |
| `font` | Glyph-set prefix (see section 5). |
| `fontnum` | Number of glyphs in the set (10 for digits, 11 for battery, 7 for weekdays). |
| `align` | `left` or `center` text alignment within the box. |
| `fgcolor`, `fgrender` | `"0xAARRGGBB"` strings, all `0xFFFFFFFF` here. [unknown] Whether the glyph bitmaps are tinted by these. |
| `isShowAddwh` | Present and `true` on several widgets. [unknown] Name suggests "add leading zero / width handling". |
| `style` | Only on `week` (value 0). [unknown] |

Sample widget placement (screen 320×385):

| type | x | y | w | h | font | align |
|---|---|---|---|---|---|---|
| hour | 15 | 12 | 188 | 100 | g626 | left |
| min | 15 | 119 | 190 | 100 | g12 | left |
| week | 205 | 62 | 68 | 26 | week | left |
| day | 223 | 100 | 32 | 26 | g624 | left |
| calorie | 205 | 231 | 48 | 20 | g620 | center |
| step | 71 | 285 | 60 | 20 | g621 | center |
| battery | 223 | 311 | 70 | 26 | g622 | left |

`font.json` lists each glyph set as `{"name": "<prefix>", "bpp": 16, "format": "png"}`.
The `"png"` label is misleading. The data is RAW. `bpp: 16` matches the RGB565 plane.

---

## 4. RAW image format [verified]

Used by every image entry (preview, background, glyphs). Files are uncompressed.

### Header (16 bytes)

| Offset | Size | Field |
|---|---|---|
| 0 | 4 | Magic `52 41 57 00` (`"RAW\0"`) |
| 4 | 2 | Width, `u16` LE |
| 6 | 2 | Height, `u16` LE |
| 8 | 1 | `0x85`. [unknown] Constant in every file; probably a format/pixel-type ID. |
| 9 | 1 | `0x00` = opaque (no alpha plane), `0x66` = alpha plane present. [inferred] |
| 10 | 2 | [unknown] `0` in most files. `0x0020` in weekday labels, `0x4F00` in `g621`. |
| 12 | 4 | Size in bytes of the RGB565 plane, `u32` LE. `0` for opaque images. |

### Body

1. **Color plane:** `width × height` pixels, 2 bytes each, **RGB565 big-endian**
   (`RRRRRGGG GGGBBBBB`, high byte first), row-major, top row first.
2. **Alpha plane** (only if header byte 9 = `0x66`): 4 bits per pixel,
   `width × height / 2` bytes, row-major.

Total file size = `16 + w·h·2` (opaque) or `16 + w·h·2.5` (with alpha).
This holds for every entry in the sample, so there is no per-image compression.

**Alpha nibble order is unverified.** [inferred] The reference decoder
reads the low nibble first and scales by 17 (0–15 to 0–255). Glyph edges showed faint
speckle, so nibble order or scaling may need adjusting. Test against `preview.png`,
which is a full composite of the face.

### Reference decoder

```python
import struct
import numpy as np
from PIL import Image

def decode_raw(b):
    assert b[:4] == b"RAW\0"
    w, h = struct.unpack_from("<HH", b, 4)
    n = w * h
    px = np.frombuffer(b[16:16 + n * 2], dtype=">u2").reshape(h, w)
    r = ((px >> 11) & 31) * 255 // 31
    g = ((px >> 5) & 63) * 255 // 63
    bl = (px & 31) * 255 // 31
    rest = b[16 + n * 2:]
    if rest:
        a8 = np.frombuffer(rest, np.uint8)
        nib = np.empty(a8.size * 2, np.uint8)
        nib[0::2] = a8 & 15           # low nibble first (UNVERIFIED)
        nib[1::2] = a8 >> 4
        a = (nib[:n] * 17).reshape(h, w)
    else:
        a = np.full((h, w), 255)
    return Image.fromarray(np.dstack([r, g, bl, a]).astype(np.uint8), "RGBA")
```

---

## 5. Image inventory (sample `f468`)

| Entry | Size (px) | Alpha | Purpose |
|---|---|---|---|
| `preview.png` | 272×324 | no | Thumbnail of the whole face, drawn inside a device-bezel frame, with sample time and stats |
| `files61.png` | 320×385 | no | Full-screen background. The 320×385 size is the likely native resolution of the IDW20 panel. |
| `g626_0`–`g626_9` | 94×100 | yes | Large digits, hour |
| `g12_0`–`g12_9` | 94×100 | yes | Large digits, minute |
| `g624_0`–`g624_9` | 16×26 | yes | Day-of-month digits |
| `week_en_{sun,mon,tue,wed,thur,fri,sat}` | 68×26 | yes | Weekday labels (note `thur`) |
| `g622_0`–`g622_9`, `g622_10` | 16×26, `_10` = 22×26 | yes | Battery digits, `_10` = `%` |
| `g621_0`–`g621_9` | 12×20 | yes | Step-count digits |
| `g620_0`–`g620_9` | 12×20 | yes | Calorie digits |

Naming convention: `<font>_<index>`. Indices 0–9 are the digits `0`–`9`. Index 10
(where present) is `%`. Weekday labels use `week_<lang>_<day>`. Only `en` exists in the sample.

Note that the static text and icons ("steps", "kcal", the shoe, flame and battery
glyphs) are **baked into the background image**. Only the changing values are widgets.

---

## 6. Rendering procedure (inferred)

1. Draw the background (`bkground`) at (0, 0).
2. For each `item`, take the value for `type` (time, date, battery %, steps, calories),
   format it as decimal digits, and look up one glyph per character as `<font>_<digit>`.
3. Lay the glyphs out left to right inside the box `(x, y, w, h)`, honoring `align`.
4. Alpha-blend each glyph over the background.

The preview shows the hour and minute stacked as two rows of two digits, which fits
the hour box (188 px) holding two 94 px glyphs. Spacing, leading-zero behavior and tinting
by `fgcolor` are not confirmed.

---

## 7. Proof of watch face sideloading success
The **Blue_Cyber** watch face script generated the .zip file, loaded the watch face in IDO's Demo App **IDO Demo Custom (https://github.com/idoosmart/IDOAndroidBleSDK-_UPDATE/tree/master)**, and was installed successfully.

This proves local .iwf watch face installation is possible via both the IDO Demo App and VeryLoad.

<img width="540" height="720" alt="20261116_230431_scaled" src="https://github.com/user-attachments/assets/db7d86ca-6bbf-42ff-a7a8-1af648467304" />

---

## 8. Open questions

1. **`compress: "LZ4"`**: nothing in this file is compressed. Check whether the app
   compresses on transfer (BLE or Wi-Fi capture) or whether other faces use it.
2. **RAW header bytes 8, 10, 11**: meaning unknown. Byte 8 may encode pixel format,
   and bytes 10–11 may be flags or offsets.
3. **Alpha plane**: confirm nibble order and scaling against the preview composite.
4. **Manifest flags**: `bluetooth`, `disturb`, `battery`, `isShowAddwh`, `style`, `fgrender`.
5. **Other widget types**: only 7 are seen. Heart rate, weather, analog hands and so on
   will need more sample faces.
6. **Transfer and install**: how the app sends this file to the watch and whether the
   watch validates it. Not examined.
7. **Single sample**: all conclusions come from one face. Fields that look constant
   (version, `0x85`, `clouddialversion`) may vary elsewhere.
