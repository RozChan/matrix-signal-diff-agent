# Project-local streamlit-aggrid Manual build

This directory preserves and patches the official `streamlit-aggrid` 1.1.9
PyPI source distribution for the matrix review application.

## Provenance

- Upstream release: `streamlit-aggrid==1.1.9`
- Source: PyPI source distribution
- Original archive: `streamlit_aggrid-1.1.9.tar.gz`
- Archive SHA256:
  `e17c8aec6a88e9b51b5aff23e3eccd6420f4b8aeeeb57d4c0ef32a5819d90693`
- License: MIT; the upstream license and copyright notice are preserved in
  `LICENSE` and `upstream/LICENSE`.
- Runtime bundle:
  `upstream/st_aggrid/frontend/build/static/js/main.db06ce24.js`
- Original bundle SHA256:
  `35b1e81df4820c9ec69fe96c95473cd28af2add8b84d997b0b1093b6b3538d10`
- Patched bundle SHA256:
  `a892399205a5547613854f847a362e7a3f375deb597e6c9434d655c757c01adb`
- Local wheel SHA256:
  `95a2ba1153bb1fb2317e4a46a2b7c8672f226d8525555075ddfa6d69f31c1173`

The sdist contains the Python sources, compiled frontend, and source map, but
not the complete TypeScript build workspace. `upstream/` remains byte-for-byte
equivalent to the extracted sdist. The build helper copies it to an isolated
temporary directory, applies the reviewed compiled-artifact patch, changes the
package version to `1.1.9+manual.3`, and builds a wheel.

## Patch purpose

The upstream Manual Update handler only logs `Manual update triggered`. The
project-local patch first calls AG Grid's `stopEditing()`, then invokes the
component's existing `returnGridValue()` collector with event name
`manualUpdate`. The existing collector continues to call
`Streamlit.setComponentValue()`.

The `manual.3` patch replaces the compiled floating toolbar with a fixed action
bar above the AG Grid column headers. The action bar participates in layout and
therefore cannot cover the right-pinned columns. It contains a short instruction
and one red `保存` button with a white save icon; its title and accessible label
are both `保存修改`. Collapse, drag, fullscreen, search, and CSV download controls
are not rendered. The Manual handler and collector pipeline remain unchanged.

The patch is guarded by the original bundle SHA256, an exact single-match check
for the Manual handler, and unique toolbar boundaries plus the reviewed toolbar
component SHA256. It refuses unknown inputs and recognizes an already patched
bundle without applying the replacement twice. It does not modify
`site-packages`, inspect the DOM, or inject browser-time JavaScript.

## Build

From the project root:

```powershell
python -m pip install build poetry-core --index-url https://pypi.org/simple
python tools/build_streamlit_aggrid_manual.py
```

The wheel is written to `vendor/wheels/`.

## Emergency rollback

Set `REVIEW_EDITOR_MODE=data_editor` and restart Streamlit. The reliable
`st.data_editor` path uses the same persistence backend and remains in the
business application.
