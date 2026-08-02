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
  `986a070501f6bc75b23d019876a7d654e086590742ffae267d1ad7ab2ff8481d`
- Local wheel SHA256:
  `1d285af322894f8ccde11653cab15370a7c08c97ae4586447ed248c3927c5d4e`

The sdist contains the Python sources, compiled frontend, and source map, but
not the complete TypeScript build workspace. `upstream/` remains byte-for-byte
equivalent to the extracted sdist. The build helper copies it to an isolated
temporary directory, applies the reviewed compiled-artifact patch, changes the
package version to `1.1.9+manual.2`, and builds a wheel.

## Patch purpose

The upstream Manual Update handler only logs `Manual update triggered`. The
project-local patch first calls AG Grid's `stopEditing()`, then invokes the
component's existing `returnGridValue()` collector with event name
`manualUpdate`. The existing collector continues to call
`Streamlit.setComponentValue()`.

The `manual.2` patch also replaces the compiled toolbar with one permanently
visible red save button. Its title and accessible label are both `保存修改`,
and it uses a white save icon. Collapse, drag, fullscreen, search, and CSV
download controls are not rendered. The Manual handler and collector pipeline
remain unchanged from `manual.1`.

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
