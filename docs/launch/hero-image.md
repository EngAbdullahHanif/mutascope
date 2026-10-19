# Hero image: what to capture

The README shows `docs/img/explorer.png` at the top. It does not exist yet; capture it by hand
as below and commit it at that path. Do not generate or edit the image beyond cropping.

| | |
| --- | --- |
| Page | https://mutascope.abdullahhanif.me/explore/GFP_AEQVI_Sarkisyan_2016 (the deployed site, not a local build) |
| Protein | GFP (`GFP_AEQVI_Sarkisyan_2016`), the Landing page's first example |
| View | The page's defaults: model "Supervised (n = 1,084)", **Predicted**, structure coloured by effect |
| Theme | Light (set it with the theme toggle; GitHub shows the README on white for most visitors) |
| Browser | Chrome, window content 1440 × 900 CSS pixels, zoom 100%, device pixel ratio 2 (a Retina display) |
| File | PNG, 2880 × 1800 pixels as captured, at most about 1 MB |

Steps:

1. Open the page and wait until the heatmap and the 3D structure have both drawn (the structure
   mounts after the heatmap).
2. Hover one heatmap column so the linked residue is highlighted in the 3D view. Pick a column
   whose residue faces the camera. Avoid the chromophore (S65–Y66–G67): it sits inside the
   β-barrel, so its highlight is likely hidden behind the strands.
3. Keep the tooltip in the frame: it shows the label, the prediction, its intervals and the
   measured value.
4. Capture the browser viewport only (no browser chrome or desktop), for example with Chrome
   DevTools → Command menu → "Capture screenshot".
5. Save as `docs/img/explorer.png`. If it is larger than about 1 MB, compress it losslessly (for
   example `oxipng -o 4`), without resizing.

The README's alt text reads: "The MutaScope Explorer: GFP's mutation landscape as a heatmap,
linked to its AF2 structure". Change it if the capture shows something else.
