# demo

Synthetic dossiers, foreign reports, scenarios, staged foreign notices and recorded agent answers, as JSON. All synthetic.

Built-in with the server and built after everything else. Policy documents must stay real.

`replay/` holds recorded agent answers for the seeded cases that a run starts again: `runs/<case>/<agent or check>.json` is the result an agent returns, and `dossier/<case>.json` adds the pages those answers cite. The Challenger's answer for a check is `runs/<case>/challenge-<check result id>.json`. A string like `$request.input_fingerprint` echoes that field of the request.
