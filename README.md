# ProofPilot

## Description

ProofPilot is an AI-assisted mathematical proof application. Users can enter a theorem, proof problem, or math concept in normal English, and the application helps them prove, understand, and formalize it.

The app can generate plain-English proof explanations, create proof sketches, translate suitable problems into Lean 4 code, verify Lean proofs, explain verifier errors, suggest missing assumptions or lemmas, and show a simple proof-flow visualization. Users can work entirely in natural language, while an Advanced Lean Workspace is available for directly editing, verifying, and repairing Lean code.

## Tech Stack

- **Frontend:** HTML, CSS, JavaScript
- **Backend:** Python standard-library HTTP server
- **Database:** SQLite
- **AI/LLM:** Google Gemini API
- **Formal verification:** Lean 4
- **Verification worker:** Python wrapper around the Lean CLI
- **Testing:** Python `unittest`, Node syntax checks
- **Evaluation:** Custom benchmark scripts for proof generation, verification, and repair behavior
