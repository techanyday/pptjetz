# Editorial deck reference specification

Source reviewed: `The-creator-economy-is-becoming-a-business-economy.pptx`

## Canvas and global geometry

- Slide size: **13.333 × 7.5 inches**, 16:9.
- Main editorial content grid: approximately **0.667 in left/right margin** and **0.616–0.672 in top title margin**.
- Full-height photo columns in the reference are approximately **4.889 in wide**.
- The reference uses a 12-column visual grid with approximately **0.333 in gutters**.
- Footer/source lines sit approximately **0.667 in from the left**, at **6.59–7.06 in from the top**, depending on the layout.
- Titles generally occupy a single line or a compact two-line claim block below the top margin.
- A thin horizontal rule appears beneath many titles, usually beginning near the left content margin.

## Type system

The source uses a serif display face and a sans-serif body face:

- Display/headline source font: **Gelasio**.
- Body/source font: **Rethink Sans** and **Rethink Sans Medium**.
- PPTJet fallback theme must use **Georgia** for display/headline text and **Calibri** for body text. It must not depend on Google fonts being installed.
- Reference title role: approximately **28–34 pt**, with large cover titles around **38–44 pt**.
- Reference subtitle/deck-description role: approximately **16–22 pt**.
- Card headings: approximately **14–18 pt**.
- Body copy: approximately **11–15 pt**.
- Footer/source/caption text: approximately **8–11 pt**.
- Number/stat display role: approximately **34–52 pt**.

## Palette

Measured/referenced colors:

- Warm paper/cream background: approximately **#F6F0E8** (the sample background is a warm off-white rather than pure white).
- Ink: **#2B1A0F**.
- Dark brown secondary ink: approximately **#3A2616**.
- Muted brown: **#6B4A33**.
- Editorial blue accent: **#1F3E8C**.
- Rust accent: **#B8381A**.
- White overlay/card text: **#FFFFFF**.

The reference uses a restrained palette: cream paper, dark ink, blue as the principal structural accent, and rust as a sparing emphasis color. Editorial decks should not use a rainbow palette.

## Reference layout language

The ten reference slides use ten different compositions:

1. Full-bleed photo cover with a solid color block containing eyebrow, claim title, subtitle, and footer.
2. Tall photo on the left, two text cards on the right, and a takeaway line.
3. Five-row stack on the left, tall photo on the right, and an arrow/caption.
4. Four-step loop diagram arranged as a 2×2 loop with native connectors.
5. Full-bleed photo with title and text overlay.
6. Two large numbers plus a color banner containing the key insight and source line.
7. Native table.
8. Four numbered columns with hairline dividers.
9. Color block with a large phrase on the left and three headed points on the right.
10. Numbered list with a tall photo on the right.

Additional layouts may include quote, section break, comparison, timeline, hub-and-spoke, and closing actions, but adjacent slides must not repeat the same layout.

## Editorial content conventions

- Slide titles are claim-style sentences, not topic labels. Example: “Reach is rented; relationships are owned.”
- Almost every slide has a short “so what” line beneath the main content.
- Data slides include a source line.
- Notes are present on every slide and are approximately 60–90 words.
- Photos are art-directed as one coherent set: warm, natural light, candid people making or building things.
- Diagrams, statistics, and tables must be native editable PowerPoint shapes/tables in PPTJet, even where the source sample uses flattened images.
- Picture alt text must describe the actual image; do not use the sample's `preencoded.png`.

## Measured picture frames

The source contains the following representative picture frames:

- Full-height side frame: approximately **4.889 × 7.5 in**.
- Large inset photo: approximately **7.222 × 4.306 in**.
- Full-width inset photo: approximately **12.0 × 4.444 in**.
- Right-side full-height frame: approximately **4.889 × 7.5 in**.

All PPTJet image placement must crop to the target frame, never stretch, and must degrade to a colored block with an icon when a Pexels image is unavailable.

## Implementation implications

- Use a dedicated `EDITORIAL` theme, feature-flagged and off by default.
- Use Georgia + Calibri by default, with the font pair configurable.
- Use Pexels only through the existing integration.
- Use native shapes, connectors, tables, speaker notes, and real alt text.
- Keep the existing generator as the default until the editorial feature is approved.
