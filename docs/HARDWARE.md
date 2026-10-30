# Shining Mask hardware: what chip is inside?

Research note, 2026-10-30. Based on a read-only GATT dump of `MASK-B042F1`, static
analysis of the official Android app (`cn.com.heaton.shiningmask` v1.2.3) and its bundled
assets, and public sources. **No Bluetooth writes were made and nothing was flashed.**

## Verdict

| Claim | Confidence |
|---|---|
| The app's OTA path is the **Panchip PAN1020 (PN102-series) SDK "OTA server" protocol** (service `FD00`, data `FD01`, ctrl `FD02`) | **Confirmed** |
| The vendor's earlier "TR" platform (`TR1906R04`, the Shining *Glasses* firmware bundled in this APK) runs on a **Panchip PAN1020-class Cortex-M0** | **Confirmed** (register map match, see below) |
| "TR" (manufacturer data `0x5254`) is the board/firmware vendor's own tag, not a real Bluetooth SIG company ID. `00 4A` is the product ID (mask). The glasses use `00 41` | **Likely**. The app's scan filter matches `54 52 00 4A` exactly |
| This mask's newer board (`TR2111R004`) uses a **different SoC from TR1906**, most likely a **JieLi AC63xx** (AC632N / AC6321A / AC6328 class) | **Likely, about 55-60%** |
| Alternative: still Panchip (PAN10xx, a larger part than PAN1020) with an extra AE00 service | **Plausible, about 35%** |
| Bluetrum / Telink / Beken / Actions / Phyplus / Realtek | **Unlikely, under 10% combined**. None of their signature UUIDs appear (for example, Telink OTA `0x1912`) |
| The LEDs are driven by a **scanned matrix with constant-current shift-register drivers**, not WS2812 chains | **Likely** (based on engineering reasoning, not a teardown) |

Why the mask is probably not a plain PAN1020: it exposes service **`AE00` with exactly `AE01` (write without response) and `AE02` (notify)**. That is the exact UUID set JieLi's own OTA/RCSP tools expect (`Jieli-Tech/WeChat-Mini-Program-OTA`, `HarmonyOS-JL_OTA`). The TR1906 Panchip firmware contains no AE00 service, and the official app never touches AE00. A full RGB888 frame buffer (8,004 B) plus 20 stored DIY images (about 160 KB) plus built-in animations is also tight for a PAN1020, which has about 16 KB of SRAM and 256 KB of flash with a 75 KB app region. The likeliest story is that the vendor moved the 2021 board to a cheaper or larger JieLi part and kept the old GATT layout (FFF0 + FD00 + the `0x2901` version string) so the existing app still works. Nothing on the radio side proves this. Only the chip marking settles it.

## Evidence

| # | Observation | Source | Points to |
|---|---|---|---|
| 1 | `com.cdbwsoft.library.panchip.PanchipOtaManager` uses UUIDs `0000fd00/fd01/fd02` | decompiled app | Panchip |
| 2 | Panchip SDK `ota_server.h` (Copyright Shanghai Panchip Microelectronics) defines `OTA_SVC_UUID=0xFD00`, `DATA=0xFD01`, `CTRL=0xFD02` | github.com/hao0527/BLE_APP (PAN1020 SDK) | Panchip, exact match |
| 3 | The same SDK's template service is `0xFFF0` with 16-bit chars `FFF1..FFFA`. The mask uses `FFF0` but with 128-bit `d44bc439-...` chars | same repo | Vendor customized the Panchip template |
| 4 | APK `assets/TR1906R04-10_OTA.bin` and `TR1906R04-1-10_OTA.bin` (about 64 KB each) are a 16-byte plaintext header followed by a 128-byte repeating XOR, decrypting to a Cortex-M image. Strings include `GLASSES-` and `TR1906R04-10` | local decrypt (scratchpad); Blato58/MaskApp, aks3310/glasses-eyes | Firmware is for the glasses, not this mask |
| 5 | The decrypted TR1906 image's literal pool holds exact PN102 peripheral bases: CLK `0x50000200` (6 hits), BLECORE `0x50001000` (3), DMA `0x50002000` (3), FMC `0x5000C000` (13), UART0 `0x40100000` (3), ADC `0x400E0000` (3), TIMER0, WDT | local analysis vs `PN102Series.h` | Panchip PAN1020-class, confirmed for TR1906 |
| 6 | Mask exposes `AE00` with `AE01` W/oResp and `AE02` notify. JieLi's official OTA apps hard-code exactly these UUIDs | GATT dump; Jieli-Tech OTA repos; e87_badge (JieLi badge) | JieLi |
| 7 | JieLi's RCSP "FD00" uses a custom 128-bit base (`c2e6fd00-e966-...`), not the 16-bit `0000fd00`. The mask's `0000fd00` is the Panchip one | jumpingmushroom/e87_badge `const.py` | FD00 here means Panchip OTA, not JieLi |
| 8 | App gates image features on `"TR2111R004-01".compareTo(hardVersion) < 0`. The version is read from the `0x2901` descriptor of char `...9600` | `ConnectActivity.isShowImageFlag` | "TR2111R004" is a hardware/board revision string |
| 9 | Native libs are only `libAES.so` (JNI `csh.tiro.cc.aes`, plain table AES) and `libint16fft.so` (phone-side FFT for music mode) | APK split `config.armeabi_v7a` | No chip-vendor SDK in native code. "tiro" may be the "TR" design house (speculative) |
| 10 | No Device Information Service. Random static address | GATT dump | Not discriminating |

### What "TR2111R004-01" probably means (speculative)

`TR` = vendor prefix (same as the `0x5254` manufacturer tag). `2111` = a YYMM date code (November 2021), consistent with the glasses' `TR1906` (June 2019). `R004` = board/project revision. `-01` = firmware build. The only other public hits for these strings are Shining Mask and Shining Glasses reverse-engineering repos.

## LED driving

The panel has 46 x 58 = **2,668 RGB LEDs** (8,004 bytes as RGB888, which matches the upload size exactly). The upload format proves only that the firmware keeps a 3-bytes-per-pixel frame. It does not show how the LEDs are wired.

- **A WS2812-style chain is unlikely.** One chain needs 2,668 x 24 bit x 1.25 us, about 80 ms per frame (12 fps max). Each WS2812 also draws about 0.5-1 mA quiescent, so the mask would idle at 1.3-2.7 A with all LEDs dark. That doesn't fit a USB-C or Li-po mask, or the cost. A 4 mm pitch would also need 2020-size smart LEDs, which are expensive.
- **A scanned matrix is likely.** Common cathode or anode rows are switched by P-MOSFETs (for example, 4953-type dual P-FET packages or a row decoder), with columns driven by constant-current shift-register drivers. Candidates are MBI5024/5124-type parts, ICN2053/ICND2153 (built-in SRAM and PWM, common in cheap P-pitch panels), SM16xxx, or generic 74HC595 plus resistors. The MCU streams bits over SPI or GPIO, and 1/16-1/32 scan keeps only a few dozen LEDs lit at once, so current tracks content.
- If the drivers are ICND2153-class with on-chip frame memory, the SoC doesn't need to hold the full PWM frame, which would ease the RAM argument above.

## OTA path in the app (description only)

On connect the app requests MTU 103 (`setMtuSize1(dev,100)`) and caches the `FD02` ctrl / `FD01` data handles. The Panchip flow is:

1. Request the version on ctrl (`01`); the reply is `80 01 app dev pro`.
2. Send the size (`02 type size32`).
3. Stream the image on `FD01` in MTU-sized chunks, with an ack (`80 04`) per chunk.
4. Send a CRC32 from the file header (`03 crc`). A reply of `80 03 00` means success. The app then disconnects, and the device presumably reboots into the new image.

The file header is `codeSize u32, crc32, app u16, dev u16, pro u16, type u8, rsvd`.

In v1.2.3 **nothing in the UI calls `selectFile()`/`getVersion()`**, so the OTA path is dormant. The only bundled images are **TR1906 glasses firmware**.

**Risk:** pushing those images, or any image, through `FD00` would at best be rejected and at worst replace the mask's application with glasses firmware for a different board and possibly a different SoC. The result would be a bricked mask with no known recovery path. Our tooling should never write to `FD01`/`FD02`, and should treat `AE00` as off-limits too, since on JieLi it is the RCSP/OTA channel.

## If someone opens the mask

1. **Main SoC marking:** look for `PAN1020`/`PAN1026`/`PN102x`/`PAN108x` (Panchip) versus `AC632N`/`AC6321A`/`AC6328`/`AC63xx` or a "JL" logo (JieLi). Also note `AB5xxx` (Bluetrum) and `TLSR` (Telink). Photograph it under angled light.
2. **SPI NOR flash** next to the SoC (8-pin, for example `25Q16`/`25Q32`, XT25F, P25Q, GD25, BY25). If present, it holds the images and animations.
3. **LED driver ICs** on the flex/panel: count them and read the markings (MBI, ICN/ICND, SM16, `HC595`), plus row-switch FETs (`4953`, `AO3401`). Count rows and columns to find the scan ratio.
4. **Sensors and power:** IR LED plus photodiode, or a proximity IC, for hand-wave. A MEMS mic or electret for rhythm mode. Charger and boost ICs (for example TP4056, IP5306) and the cell rating.
5. **Test pads:** `SWDIO`/`SWCLK`/`VCC`/`GND` pads suggest an ARM-SWD part (Panchip). USB-C D+/D- routed to the SoC, or pads labelled `DP/DM`/`USBDP`, suggest JieLi's USB-based programming. Just record them. Don't connect a programmer.
6. Board silkscreen: look for `TR2111`, `R004` or a date code to confirm the naming theory.

## Sources

- Panchip PAN1020 SDK (OTA UUIDs, flash map, PN102 register map): https://github.com/hao0527/BLE_APP (`src/application/ota/ota_server.h`, `section_cfg.h`, `src/platform/arch/inc/PN102Series.h`)
- JieLi OTA reference apps using AE00/AE01/AE02: https://github.com/Jieli-Tech/WeChat-Mini-Program-OTA (`code/JLOTA/miniprogram/lib/bluetoothOTAManager.ts`), https://github.com/Jieli-Tech/HarmonyOS-JL_OTA
- JieLi AC63 SDK: https://github.com/Jieli-Tech/fw-AC63_BT_SDK
- JieLi-based badge (AE00 plus 128-bit RCSP FD00): https://github.com/jumpingmushroom/e87_badge
- Shining Glasses (TR1906, Panchip OTA container, `00 41` product ID): https://github.com/Jhan-vasquez-dev/shining-glasses, https://github.com/aks3310/glasses-eyes
- TR1906 firmware decryption and analysis: https://github.com/Blato58/MaskApp (`docs/tr1906-firmware-analysis.md`)
- Other Shining Mask GATT dumps (FD00 plus AE00 present): https://github.com/tayred06/shining-mask-controller
- Security background: https://bishopfox.com/blog/invasion-of-the-face-changers-halloween-hijinks-with-bluetooth-led-masks, https://www.theregister.com/2025/10/30/halloween_hacking_led_masks/
- No public teardown or FCC internal photos of the Shining Mask board were found.
