# Style previews: source images

One picture per art style preset, shown in the frontend's style chooser. Every image has the same subject, Fluffy the poodle walking up the ramp from query 11, without the math, so the only thing that changes is the style.

- `originals/<style>.png`: the chosen render at full size (1024×1024). Don't delete these: every other size is made from them.
- `prompts.json`: the exact prompt for each style. It is the subject sentence plus the preset's `description` and `aesthetic`, word for word from `config.js` (2026-11-22).
- `make_sizes.py`: writes `../style_previews/thumb/` (chooser tiles) and `../style_previews/large/` (hover pop-up). Only `style_previews/` ships in the Docker image.

How they were made (2026-11-22): `gemini-3-pro-image`, text to image with no scaffold, aspect ratio 1:1, two candidates per style. Candidate 1 was picked for storybook and cel_shaded_anime, and candidate 2 for the rest.

To add a preset: render one with the same subject sentence (see `prompts.json`), save it as `originals/<key>.png`, run `make_sizes.py`, and add the key to `STYLE_OPTIONS` in `app.py`.
