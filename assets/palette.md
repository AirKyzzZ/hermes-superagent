# hermes-superagent palette

| Token | Hex | Used for |
|---|---|---|
| `indigo` | `#1a10b8` | Halftone dots, mid to dark tones |
| `indigo-deep` | `#0a0a9a` | Largest dots and solid shadows |
| `dot-lilac` | `#a9a9cc` | Smallest dots, in highlights and on the ground |
| `ground` | `#f4f4fd` | Pale paper between the dots |
| `navy-card` | `#14142a` | Bottom fade, header background |
| `text-white` | `#ffffff` | Wordmark |
| `text-grey` | `#9a9ab4` | Tagline |

Each dot takes its colour from its size: `dot-lilac` below about 17% ink, blending to `indigo` by 36%, then to `indigo-deep` from 80% to solid.

`avatar.png` is pure 1-bit `#000000` on `#ffffff`.

Dot grid: a 6px pitch on a square, axis-aligned grid at 1200 wide (12px in the 2400 layers, 4px in the 512 avatar). The cover's fade runs from 80% of the height to `navy-card` on the last row.
