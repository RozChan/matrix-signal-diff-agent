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
  `10c9cadd87182fc29379074f84ca74e23b18e6ce6cd8e58e62ab490e932830b7`

The sdist contains the Python sources, compiled frontend, and source map, but
not the complete TypeScript build workspace. `upstream/` remains byte-for-byte
equivalent to the extracted sdist. The build helper copies it to an isolated
temporary directory, applies the reviewed compiled-artifact patch, changes the
package version to `1.1.9+manual.1`, and builds a wheel.

## Patch purpose

The upstream Manual Update handler only logs `Manual update triggered`. The
project-local patch first calls AG Grid's `stopEditing()`, then invokes the
component's existing `returnGridValue()` collector with event name
`manualUpdate`. The existing collector continues to call
`Streamlit.setComponentValue()`.

The patch is guarded by both the original bundle SHA256 and an exact
single-match check. It refuses unknown inputs and recognizes an already patched
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
