# Bundled Tier A fonts (OFL)

Place OFL font files here (gitignored binaries):

- `Inter-Regular.ttf`
- `NotoSansSC-Regular.ttf`
- `ZCOOLKuaiLe-Regular.ttf` (optional)
- `LXGWWenKai-Regular.ttf` (optional)
- `OFL.txt` (copy of `../licenses/OFL-1.1.txt`)

Fetch / copy:

```bash
bash scripts/fetch_fx_fonts.sh
```

Only fonts listed in `../manifest.json` with `tier`/OFL and SPDX in
`docs/FX_ASSET_WHITELIST.md` may be redistributed with the product package.
