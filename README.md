# ProofPilot

## Description

ProofPilot is an AI-assisted mathematical proof application. Users can enter a theorem, proof problem, or math concept in normal English, and the application helps them prove, understand, and formalize it.

The app can generate plain-English proof explanations, create proof sketches, translate suitable problems into Lean 4 code, verify Lean proofs, explain verifier errors, suggest missing assumptions or lemmas, and show a simple proof-flow visualization. Users can work entirely in natural language, while an Advanced Lean Workspace is available for directly editing, verifying, and repairing Lean code.

## Usage

Try the hosted app here:

https://proofpilot-57rx.onrender.com/

The free Render instance may take about a minute to wake up if it has been inactive.

Example prompts for the proof assistant:

- `Prove that if p and q are true, then p and q is true.`
- `What is proof by contradiction?`
- `Explain induction on natural numbers.`
- `Rewrite using a = b to prove a + 1 = b + 1.`
- `Prove there exists n such that P n by using witness 0 and proof P 0.`

Example Lean inputs for the Advanced Lean Workspace:

```lean
example : 1 = 1 := by
  rfl
```

```lean
example (p q : Prop) (hp : p) (hq : q) : p ∧ q := by
  exact And.intro hp hq
```

```lean
example : 1 = 2 := by
  rfl
```

The last example intentionally fails, so it is useful for testing diagnostics and repair suggestions.

## Tech Stack

- **Frontend:** HTML, CSS, JavaScript
- **Backend:** Python standard-library HTTP server
- **Database:** SQLite
- **AI/LLM:** Google Gemini API
- **Formal verification:** Lean 4
- **Verification worker:** Python wrapper around the Lean CLI
- **Web hosting:** Docker, Render
- **Testing:** Python `unittest`, Node syntax checks
- **Evaluation:** Custom benchmark scripts for proof generation, verification, and repair behavior
