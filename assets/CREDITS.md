# Credits and sources

Retrieved 2026-11-05. No AI image generation or paid services were used. Nothing comes from Nous Research, the marketplace, other plugins or a logo. The reference images were viewed for style only and are not read by the build.

The navy `#14142a` was picked to match the card colour in the marketplace and is the same value as a colour token in the Hermes docs theme, so the cover fades into the card. No Nous artwork is used.

## Head: The Metropolitan Museum of Art

| | |
|---|---|
| Title | Marble head of Zeus Ammon |
| Object ID | 257864 |
| Accession | 2012.22 |
| Date, culture | ca. 120-160 CE, Roman, Imperial period |
| Object page | https://www.metmuseum.org/art/collection/search/257864 |
| API record | https://collectionapi.metmuseum.org/public/collection/v1/objects/257864 |
| Image used | https://images.metmuseum.org/CRDImages/gr/original/DP265183.jpg (`art/sources/met-257864/view-0.jpg`) |
| Image SHA-256 | `2969d7e4393c9355f5fb2f9fc5f4128852ec78117c46f202c82a3bca1f755acc` |
| Saved record SHA-256 | `e005c0cb23630c929f9d0f076357be3729029094a0f26a7546bb53707201c0ad` |

Licence evidence:

- The API record has `"isPublicDomain": true`, saved at `art/met-257864-object.json`.
- The API docs (https://metmuseum.github.io/) say the API gives access to Open Access data and "corresponding high resolution images (JPEG format) that are in the public domain".
- The Met's Open Access repository (https://github.com/metmuseum/openaccess) says the dataset is released under Creative Commons Zero and that covered artworks are marked "with the Creative Commons Zero (CC0) icon" on the museum's website.

Not seen: the object page and the Met's Open Access policy page return HTTP 429 to scripts, so the CC0 icon on the object page itself wasn't checked. The API record, re-read on 2026-11-10, still says `"isPublicDomain": true` for the same image.

Attribution is not required. A suggested courtesy line is "Head: Marble head of Zeus Ammon, The Metropolitan Museum of Art, 2012.22 (public domain)."

The Met retired `/v1/search` on 2026-10-01, so the search used `/v1.1/search`. `art/met_search.py` and `art/met_fetch.py` record how the object was found and downloaded.

Eight other public-domain Met heads were downloaded as candidates and not used: 248112, 250678, 251390, 252511, 252953, 254640, 255420, 256121.

## Font: Inter

| | |
|---|---|
| Version | Inter 4.1, official release |
| Download | https://github.com/rsms/inter/releases/download/v4.1/Inter-4.1.zip |
| Archive SHA-256 | `9883fdd4a49d4fb66bd8177ba6625ef9a64aa45899767dde3d36aa425756b11e` |
| Licence | SIL Open Font License 1.1, in `LICENSE.txt` inside the archive. Copyright 2016 The Inter Project Authors |

Used only for the wordmark and tagline, rasterised in `readme-header.png`. The font files are not redistributed.

## Original work

The arms, hands, veins and fade are all procedural, written for this project in `art/make_art.py`, with fixed seeds and no external input.

## How the image is made

1. **Head mask.** The marble is warm and the Met's backdrop is neutral grey, so red minus blue separates them. The broken horn stump on the viewer's left is cut away.
2. **Head tone.** A red-weighted luminance reduces the orange staining. The damaged viewer-left eye is replaced by a mirror of the right eye. The photo is then denoised, flat-fielded and locally sharpened, and the bust is pushed into shadow so the beard stands out.
3. **Arms and hands.** Each limb is a height field built from swept round cones, with articulated fingers and smooth unions, then lit from the same side as the photo. Arms darken close to the head, and a one-dot pale keyline separates overlaps.
4. **Ground.** Branching random-walk cracks and soft mottling appear only at the left and right edges.
5. **Halftone.** The layers are averaged per grid cell, with tone curve and dots drawn from the result. Outer fingertips randomly drop cells and shed loose dots. Dots grow into the bottom, then the colour fades to navy.
6. **Eyes.** At this scale an eye is about 8 dots wide, too few to hold the carved lids, so a small hand-designed dot pattern is stamped onto the grid in the halftone outputs. The eyes in the grey layers are the real marble ones.

## Layers for Remotion

All layers are 2400x1200. Stack `ground-grey.png`, then hands, then head. The cover's grid is 200x100 cells, and at 12px pitch `head.png` and `hands.png` line up with it dot for dot.

- `head.png`, `hands.png`: finished halftone on transparency, including the dissolve.
- `head-grey.png`, `hands-grey.png`, `ground-grey.png`: pre-halftone, for computing dots live. These have no eye stamp and no dissolve.
- `composite-grey.png`: the flattened grey image, without the colour fade. The ground already includes the dark ramp at the bottom.

Regenerate everything by placing the two sources under `assets/art/sources/` (`met-257864/view-0.jpg` and `inter/Inter.zip`, both linked above), then running:

```
python assets/art/make_art.py
```

It needs Pillow, numpy, scipy and scikit-image, and writes into `assets/`. Repeat runs gave byte-identical PNGs. The script also writes an experimental `avatar.png`, which is gitignored and unused.
