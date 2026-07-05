const apiBase = window.location.protocol === "file:" ? "http://127.0.0.1:8000" : window.location.origin;

const assistantInput = document.querySelector("#assistantInput");
const assistButton = document.querySelector("#assistButton");
const useAssistantLeanButton = document.querySelector("#useAssistantLeanButton");
const assistantOutput = document.querySelector("#assistantOutput");
const advancedWorkspace = document.querySelector(".advanced-workspace");
const titleInput = document.querySelector("#titleInput");
const codeInput = document.querySelector("#codeInput");
const verifyButton = document.querySelector("#verifyButton");
const repairButton = document.querySelector("#repairButton");
const statusBadge = document.querySelector("#statusBadge");
const diagnostics = document.querySelector("#diagnostics");
const repairPrompt = document.querySelector("#repairPrompt");
const repairHistory = document.querySelector("#repairHistory");
const attempts = document.querySelector("#attempts");
const metrics = document.querySelector("#metrics");
const resetButton = document.querySelector("#resetButton");

let selectedAttempt = null;
let currentRepairs = [];
let lastAssistantLean = "";
let lastAssistantPrompt = "";

assistButton.addEventListener("click", generateAssistantResponse);
useAssistantLeanButton.addEventListener("click", useAssistantLean);
verifyButton.addEventListener("click", verifyProof);
repairButton.addEventListener("click", createRepairPrompt);
repairHistory.addEventListener("click", handleRepairHistoryClick);
resetButton.addEventListener("click", resetDevData);
loadMetrics();
loadAttempts();

async function generateAssistantResponse() {
  const prompt = assistantInput.value.trim();
  if (!prompt) {
    renderAssistantMessage("Enter a proof problem or concept first.");
    return;
  }

  assistButton.disabled = true;
  useAssistantLeanButton.disabled = true;
  lastAssistantLean = "";
  lastAssistantPrompt = prompt;
  renderAssistantMessage("Generating proof sketch and checking Lean...");

  try {
    const response = await fetch(`${apiBase}/api/assist`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        prompt,
        mode: "auto",
        lean_code_context: "",
      }),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Assistant request failed.");
    }

    lastAssistantLean = payload.lean_code || "";
    useAssistantLeanButton.disabled = !lastAssistantLean;
    renderAssistantResult(payload);
  } catch (error) {
    renderAssistantMessage(error.message);
  } finally {
    assistButton.disabled = false;
  }
}

function useAssistantLean() {
  if (!lastAssistantLean) return;

  titleInput.value = "Assistant generated proof";
  codeInput.value = lastAssistantLean;
  if (advancedWorkspace) {
    advancedWorkspace.open = true;
  }
  codeInput.focus();
}

async function verifyProof() {
  setStatus("running", "Running");
  verifyButton.disabled = true;
  diagnostics.textContent = "Sending proof attempt to verifier...";

  try {
    const response = await fetch(`${apiBase}/api/proof-attempts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: titleInput.value,
        statement: codeInput.value,
        code: codeInput.value,
      }),
    });

    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Verification request failed.");
    }

    renderAttemptResult(payload);
    await loadRepairHistory(payload.id);
    await loadAttempts();
    await loadMetrics();
  } catch (error) {
    setStatus("failed", "Request failed");
    diagnostics.textContent = error.message;
  } finally {
    verifyButton.disabled = false;
  }
}

async function loadAttempts() {
  try {
    const response = await fetch(`${apiBase}/api/proof-attempts`);
    const payload = await response.json();
    attempts.innerHTML = "";

    for (const attempt of payload.attempts || []) {
      const item = document.createElement("div");
      item.className = "attempt";
      item.innerHTML = `
        <strong>${escapeHtml(attempt.title)}</strong>
        <span>${escapeHtml(attempt.status)} · ${new Date(attempt.created_at * 1000).toLocaleTimeString()}</span>
      `;
      item.addEventListener("click", () => renderAttemptResult(attempt));
      attempts.appendChild(item);
    }
  } catch {
    attempts.textContent = "Start the backend to load attempts.";
  }
}

async function resetDevData() {
  const confirmed = window.confirm("Clear all local ProofPilot attempts and repairs?");
  if (!confirmed) return;

  resetButton.disabled = true;
  try {
    const response = await fetch(`${apiBase}/api/dev/reset`, { method: "POST" });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Reset failed.");
    }

    selectedAttempt = null;
    currentRepairs = [];
    setStatus("idle", "Idle");
    diagnostics.textContent = "Run verification to see Lean output.";
    repairPrompt.textContent = "Run repair after a failed verification to see the candidate proof.";
    repairHistory.textContent = "No repairs yet.";
    repairButton.disabled = true;
    await loadAttempts();
    await loadMetrics();
  } catch (error) {
    repairPrompt.textContent = error.message;
  } finally {
    resetButton.disabled = false;
  }
}

function renderAttemptResult(attempt) {
  selectedAttempt = attempt;
  setStatus(attempt.status, attempt.status);
  repairButton.disabled = attempt.status === "verified";
  repairPrompt.textContent = "Run repair after a failed verification to see the candidate proof.";
  repairHistory.textContent = "Loading repair history...";

  const result = attempt.result || {};
  diagnostics.textContent = formatDiagnostics(attempt, result);
  loadRepairHistory(attempt.id);
}

async function createRepairPrompt() {
  if (!selectedAttempt) return;

  repairButton.disabled = true;
  repairPrompt.textContent = "Building repair prompt...";

  try {
    const response = await fetch(`${apiBase}/api/proof-attempts/${selectedAttempt.id}/repairs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Repair prompt request failed.");
    }
    repairPrompt.textContent = formatRepair(payload);
    await loadRepairHistory(selectedAttempt.id);
    await loadMetrics();
  } catch (error) {
    repairPrompt.textContent = error.message;
  } finally {
    repairButton.disabled = selectedAttempt.status === "verified";
  }
}

async function loadMetrics() {
  try {
    const response = await fetch(`${apiBase}/api/metrics`);
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Could not load metrics.");
    }
    renderMetrics(payload);
  } catch {
    metrics.textContent = "Metrics unavailable.";
  }
}

function renderMetrics(payload) {
  const proof = payload.proofs || {};
  const repair = payload.repairs || {};
  const providers = repair.providers || {};
  metrics.innerHTML = "";

  const cards = [
    ["Proofs", proof.total ?? 0],
    ["Verified", proof.verified ?? 0],
    ["Proof rate", formatPercent(proof.verification_rate)],
    ["Repairs", repair.total ?? 0],
    ["Repair rate", formatPercent(repair.success_rate)],
    ["Provider", formatProviders(providers)],
  ];

  for (const [label, value] of cards) {
    const card = document.createElement("div");
    card.className = "metric-card";

    const valueEl = document.createElement("strong");
    valueEl.textContent = value;

    const labelEl = document.createElement("span");
    labelEl.textContent = label;

    card.append(valueEl, labelEl);
    metrics.appendChild(card);
  }
}

async function loadRepairHistory(attemptId) {
  if (!attemptId) {
    repairHistory.textContent = "Select an attempt to view repairs.";
    return;
  }

  try {
    const response = await fetch(`${apiBase}/api/proof-attempts/${attemptId}/repairs`);
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Could not load repair history.");
    }
    renderRepairHistory(payload.repairs || []);
  } catch (error) {
    repairHistory.textContent = error.message;
  }
}

function renderRepairHistory(repairs) {
  currentRepairs = repairs;
  repairHistory.innerHTML = "";

  if (repairs.length === 0) {
    repairHistory.textContent = "No repairs yet.";
    return;
  }

  for (const repair of repairs) {
    const item = document.createElement("div");
    item.className = "repair-item";
    item.dataset.repairId = repair.id;

    const header = document.createElement("div");
    header.className = "repair-header";

    const status = document.createElement("strong");
    status.textContent = `Round ${repair.round_index || 1}: ${repair.status}`;

    const time = document.createElement("span");
    time.textContent = new Date(repair.created_at * 1000).toLocaleTimeString();

    header.append(status, time);

    const candidate = document.createElement("pre");
    candidate.className = "candidate-preview";
    candidate.textContent = repair.candidate_code || "(empty)";

    const actions = document.createElement("div");
    actions.className = "repair-actions";

    const viewButton = document.createElement("button");
    viewButton.className = "secondary compact";
    viewButton.type = "button";
    viewButton.textContent = "View";
    viewButton.dataset.action = "view";
    viewButton.dataset.repairId = repair.id;

    const applyButton = document.createElement("button");
    applyButton.className = "secondary compact";
    applyButton.type = "button";
    applyButton.textContent = "Apply";
    applyButton.disabled = !repair.candidate_code || repair.status !== "candidate_verified";
    applyButton.dataset.action = "apply";
    applyButton.dataset.repairId = repair.id;

    actions.append(viewButton, applyButton);
    item.append(header, candidate, actions);
    repairHistory.appendChild(item);
  }
}

function handleRepairHistoryClick(event) {
  const button = event.target.closest("button[data-action]");
  if (!button || !repairHistory.contains(button)) return;

  const repair = currentRepairs.find((item) => item.id === button.dataset.repairId);
  if (!repair) return;

  markSelectedRepair(repair.id);

  if (button.dataset.action === "view") {
    repairPrompt.textContent = formatRepair(repair);
    repairPrompt.scrollIntoView({ behavior: "smooth", block: "nearest" });
    return;
  }

  if (button.dataset.action === "apply") {
    codeInput.value = repair.candidate_code;
    repairPrompt.textContent = `Applied repair candidate ${repair.id} to the editor.`;
    codeInput.focus();
  }
}

function markSelectedRepair(repairId) {
  for (const item of repairHistory.querySelectorAll(".repair-item")) {
    item.classList.toggle("selected", item.dataset.repairId === repairId);
  }
}

function formatRepair(repair) {
  const result = repair.result || {};
  const candidateVerified = repair.status === "candidate_verified";
  const diagnosticsText = result.stderr || result.stdout || result.error || "";
  const issues = parseLeanIssues(diagnosticsText);
  const classification = result.classification || {};
  const lines = [
    `Repair ${repair.round_index || 1}: ${candidateVerified ? "Candidate verified" : humanizeStatus(repair.status)}`,
    `Provider: ${repair.provider || "unknown"} / ${repair.model || "unknown"}`,
    `Time: ${result.elapsed_ms ?? "n/a"} ms`,
    classification.category ? `Classification: ${humanizeStatus(classification.category)}` : "",
    "",
  ];

  if (candidateVerified) {
    lines.push("ProofPilot found a Lean proof that the verifier accepted.");
  } else if (issues.length > 0) {
    lines.push(`The candidate still has ${issues.length} Lean issue${issues.length === 1 ? "" : "s"}:`);
    for (const [index, issue] of issues.entries()) {
      lines.push(`${index + 1}. ${issue}`);
    }
  } else {
    lines.push("ProofPilot produced a candidate, but it was not accepted by Lean.");
  }

  lines.push("");
  lines.push("Candidate Lean:");
  lines.push(repair.candidate_code || "(empty)");

  if (diagnosticsText && !candidateVerified && issues.length === 0) {
    lines.push("");
    lines.push("Verifier details:");
    lines.push(diagnosticsText);
  }

  lines.push("");
  lines.push(`Repair ID: ${repair.id}`);
  return lines.join("\n");
}

function formatDiagnostics(attempt, result) {
  const issues = parseLeanIssues(`${result.stderr || ""}\n${result.stdout || ""}`);
  const classification = result.classification || {};
  const lines = [
    `Proof: ${attempt.title || "Untitled proof"}`,
    `Status: ${humanizeStatus(attempt.status)}`,
    classification.category ? `Classification: ${humanizeStatus(classification.category)}` : "",
    `Verifier: ${result.verifier_version || result.verifier || "Lean"}`,
    `Time: ${result.elapsed_ms ?? "n/a"} ms`,
  ].filter(Boolean);

  if (result.exit_code !== undefined && result.exit_code !== null) {
    lines.push(`Exit code: ${result.exit_code}`);
  }

  lines.push("");

  if (attempt.status === "verified") {
    lines.push("Lean accepted this proof.");
  } else if (classification.summary || classification.hint) {
    if (classification.summary) {
      lines.push(classification.summary);
    }
    if (classification.hint) {
      lines.push(`Hint: ${classification.hint}`);
    }
    if (issues.length > 0) {
      lines.push("");
      lines.push(`Lean reported ${issues.length} issue${issues.length === 1 ? "" : "s"}:`);
      for (const [index, issue] of issues.entries()) {
        lines.push(`${index + 1}. ${issue}`);
      }
    }
  } else if (issues.length > 0) {
    lines.push(`Lean reported ${issues.length} issue${issues.length === 1 ? "" : "s"}:`);
    for (const [index, issue] of issues.entries()) {
      lines.push(`${index + 1}. ${issue}`);
    }
  } else if (result.error) {
    lines.push(result.error);
  } else {
    lines.push("Lean rejected this proof, but did not return a detailed diagnostic.");
  }

  const suggestions = diagnosticSuggestions(classification, result, attempt.code || "");
  if (suggestions.length > 0) {
    lines.push("");
    lines.push("What to try:");
    for (const suggestion of suggestions) {
      lines.push(`- ${suggestion}`);
    }
  }

  lines.push("");
  lines.push(`Attempt ID: ${attempt.id}`);
  return lines.join("\n");
}

function diagnosticSuggestions(classification, result, code) {
  if (!classification || classification.category === "verified") return [];

  const category = classification.category || "";
  const output = `${result.stdout || ""}\n${result.stderr || ""}\n${result.error || ""}`.toLowerCase();
  const leanCode = String(code || "");

  if (category === "unknown_identifier") {
    return [
      "Check spelling, imports, and namespaces for the unknown name.",
      "If the name is meant to be an assumption, add it to the theorem statement.",
    ];
  }
  if (category === "unsolved_goal") {
    const suggestions = [
      "Match your next proof step to the remaining goal shown after `⊢`.",
      "Add an intermediate lemma or assumption whose conclusion is exactly that goal.",
    ];
    if (leanCode.includes("∧")) {
      suggestions.push("For an `and` goal, try `And.intro`, `constructor`, or proving each side separately.");
    }
    return suggestions;
  }
  if (category === "type_mismatch") {
    return [
      "Compare the expected type with the type Lean says your term has.",
      "Add a conversion lemma, rewrite step, or intermediate claim that changes your fact into the expected shape.",
    ];
  }
  if (category === "likely_false_statement") {
    return [
      "Check whether the theorem is true as written.",
      "If it needs conditions, add those assumptions before trying to prove it.",
    ];
  }
  if (output.includes("unknown")) {
    return ["Check whether a referenced lemma, variable, or import is missing."];
  }
  return [];
}

function parseLeanIssues(output) {
  return output
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.includes(": error:"))
    .map((line) => {
      const match = line.match(/ProofPilotAttempt\.lean:(\d+):(\d+): error: (.*)$/);
      if (!match) return line.replace("ProofPilotAttempt.lean:", "Line ");
      return `Line ${match[1]}, column ${match[2]}: ${match[3]}`;
    });
}

function humanizeStatus(status) {
  return String(status || "unknown")
    .replaceAll("_", " ")
    .replace(/^\w/, (letter) => letter.toUpperCase());
}

function renderAssistantMessage(message) {
  assistantOutput.innerHTML = "";
  const empty = document.createElement("div");
  empty.className = "empty-state";
  empty.textContent = message;
  assistantOutput.appendChild(empty);
}

function renderAssistantResult(payload) {
  const verification = payload.verification || {};
  const fallbackNote =
    payload.provider_error && payload.attempted_provider
      ? `fallback: ${payload.attempted_provider}/${payload.attempted_model || "unknown"} failed, used ${payload.provider}/${payload.model || "unknown"}`
      : "";
  const diagnosticsText = formatAssistantVerification(verification);

  assistantOutput.innerHTML = "";

  if (fallbackNote || payload.provider_error) {
    const warning = document.createElement("div");
    warning.className = "assistant-warning";
    warning.textContent = [fallbackNote, payload.provider_error].filter(Boolean).join(" ");
    assistantOutput.appendChild(warning);
  }

  assistantOutput.append(
    createTextPanel("Plain-English Proof", payload.proof_sketch),
    createProofFlowPanel(payload.proof_flow || []),
    createTextPanel("Why This Works", payload.explanation),
    createMissingLemmaPanel(payload.missing_lemma_suggestions || []),
    createDetailsPanel("Formalization Guide", [
      createGuidedFormalizationPanel(payload.guided_formalization || {}),
      createStructuredExplanationPanel(payload.structured_explanation || {}),
    ]),
    createDetailsPanel("Lean Details", [
      createTextPanel("Formalization Plan", payload.formalization_plan),
      createCodePanel("Lean Code", payload.lean_code || "(not generated)"),
      createCodePanel("Lean Check", diagnosticsText),
    ]),
  );
}

function formatAssistantVerification(verification) {
  if (!verification || verification.status === "not_generated") {
    return "No Lean code was generated, so there was nothing to verify.";
  }
  return verification.stderr || verification.stdout || verification.error || "(empty)";
}

function createTextPanel(title, body) {
  const panel = document.createElement("section");
  panel.className = "assistant-card";

  const heading = document.createElement("h3");
  heading.textContent = title;

  const content = document.createElement("p");
  content.textContent = body || "(empty)";

  panel.append(heading, content);
  return panel;
}

function createDetailsPanel(title, children) {
  const details = document.createElement("details");
  details.className = "assistant-details";

  const summary = document.createElement("summary");
  summary.textContent = title;

  const content = document.createElement("div");
  content.className = "assistant-details-content";
  content.append(...children);

  details.append(summary, content);
  return details;
}

function createGuidedFormalizationPanel(formalization) {
  const rows = [
    ["Clean Statement", formalization.cleaned_statement],
    ["Assumptions", formalization.assumptions],
    ["Conclusion", formalization.conclusion],
    ["Lean Statement", formalization.lean_statement],
    ["Verification", formalization.verification_expectation],
  ];
  return createRowPanel("Guided Formalization", rows);
}

function createStructuredExplanationPanel(explanation) {
  const rows = [
    ["Assumptions", explanation.assumptions],
    ["Goal", explanation.goal],
    ["Main Idea", explanation.strategy],
    ["Lean Translation", explanation.lean_translation],
    ["Step By Step", explanation.step_explanation],
  ];
  return createRowPanel("Proof Breakdown", rows);
}

function createRowPanel(title, rows) {
  const panel = document.createElement("section");
  panel.className = "assistant-card structured-explanation";

  const heading = document.createElement("h3");
  heading.textContent = title;

  const list = document.createElement("div");
  list.className = "explanation-rows";

  for (const [label, value] of rows) {
    const row = document.createElement("div");
    row.className = "explanation-row";

    const labelEl = document.createElement("strong");
    labelEl.textContent = label;

    const valueEl = document.createElement("p");
    valueEl.textContent = value || "Not applicable.";

    row.append(labelEl, valueEl);
    list.appendChild(row);
  }

  panel.append(heading, list);
  return panel;
}

function createProofFlowPanel(flow) {
  const panel = document.createElement("section");
  panel.className = "assistant-card";

  const heading = document.createElement("h3");
  heading.textContent = "Proof Flow";

  const list = document.createElement("ol");
  list.className = "proof-flow";

  for (const step of flow) {
    const item = document.createElement("li");
    const label = document.createElement("strong");
    label.textContent = step.label || "Step";
    const detail = document.createElement("p");
    detail.textContent = step.detail || "No detail provided.";
    item.append(label, detail);
    list.appendChild(item);
  }

  if (list.children.length === 0) {
    const item = document.createElement("li");
    item.textContent = "No proof flow was generated.";
    list.appendChild(item);
  }

  panel.append(heading, list);
  return panel;
}

function createMissingLemmaPanel(suggestions) {
  if (!suggestions.length) {
    return document.createDocumentFragment();
  }

  const panel = document.createElement("section");
  panel.className = "assistant-card missing-lemmas";

  const heading = document.createElement("h3");
  heading.textContent = "Missing Lemma Suggestions";

  const list = document.createElement("ul");
  for (const suggestion of suggestions) {
    const item = document.createElement("li");
    item.textContent = suggestion;
    list.appendChild(item);
  }

  panel.append(heading, list);
  return panel;
}

function createCodePanel(title, body) {
  const panel = document.createElement("section");
  panel.className = "assistant-card";

  const heading = document.createElement("h3");
  heading.textContent = title;

  const content = document.createElement("pre");
  content.className = "assistant-code";
  content.textContent = body;

  panel.append(heading, content);
  return panel;
}

function setStatus(className, label) {
  statusBadge.className = `status ${className}`;
  statusBadge.textContent = label;
}

function formatPercent(value) {
  if (typeof value !== "number") return "0%";
  return `${Math.round(value * 100)}%`;
}

function formatProviders(providers) {
  const entries = Object.entries(providers);
  if (entries.length === 0) return "none";
  return entries.map(([name, count]) => `${name} ${count}`).join(", ");
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
