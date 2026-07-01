# Captured API fixtures for offline unit tests

These files were captured from live LCSC, JLC, and EasyEDA endpoints.
Tests must never call the network at runtime; they load these fixtures instead.

## LCSC (`lcsc_api_responses.json`)

Source: `https://wmsc.lcsc.com/ftps/wm/product/detail?productCode={pid}`

| Part | Description |
|------|-------------|
| C25804 | 0603 10kΩ resistor (±, Ω in fields) |
| C49678 | 0805 100nF capacitor |
| C8734 | STM32F103C8T6 LQFP-48 MCU |
| C165948 | USB-C connector |
| C51118 | AP2112K-3.3 LDO |

## JLC (`jlc_cart_responses.json`, `jlc_official_mapped.json`)

- **Cart API** (no key): `https://cart.jlcpcb.com/shoppingCart/smtGood/getComponentDetail`
- **Official API** (`jlc_api_responses.json`): requires Bearer token; captures 404 without key
- `jlc_official_mapped.json`: cart responses mapped to official API shape for wrapper unit tests

## EasyEDA (`easyeda_responses.json`)

CAD data + `/svgs` UUID response for symbol/footprint generation (C25804, C49678, C8734).
