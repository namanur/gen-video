# Agent Protocol: Evidence First, Assets Alongside Research

## Non-negotiable rule

A search result is a lead, not completed research. A researched claim is not complete until it can be traced to source material. A visual asset is not reusable until its local path, provenance, description, hash, and review/rights state are registered in the episode database.

Agents must not say "saved" when they only retained a URL.

## Roles and outputs

### 1. Research director
Input: topic, audience, intended question, and time budget.
Output: research brief, 5-10 answerable subquestions, preliminary outline, evidence gaps, and scope boundaries.
Do not write the final script. Do not invent the conclusion in advance.

### 2. Primary-source researcher
Find original papers, datasets, official documentation, court/regulator material, direct interviews, and original reporting where possible.
For every source, record canonical URL, exact title, author/institution, publication date, access date, relevant section/page, the claim it supports, and a short locator excerpt.
Save locally available PDFs and create page screenshots only for pages actually used. Save article snapshots/screenshots when they contain a chart, quote, table, or layout worth revisiting. Register each saved file immediately.

### 3. Books and literature researcher
Find books, literature reviews, meta-analyses, and foundational theories. Record author, title, edition, publisher, year, ISBN/DOI if available, chapter/page and the argument being used. A book mention without verified bibliographic details is a lead, not a citation. Never invent a page number. If a source is only partially accessible, label what was and was not verified.

### 4. Counterevidence researcher
Work from the question, not the draft's existing position. Find conflicting results, limitations, alternative causes, and evidence that would change the conclusion. State the strongest reasonable opposing explanation before criticizing it. Do not manufacture balance when evidence is asymmetric.

### 5. Independent analyst
Build an explanation independently from the collected evidence, then compare it with the other analysis. Mark inference separately from established findings. Look for causal overreach, selection effects, missing base rates, and claims that confuse correlation with causation.

### 6. Fact-checker
Audit the script sentence by sentence. Each material factual claim needs evidence links and a locator. Classify each claim as verified directly, supported but indirect, contested, inference/opinion, or unsupported. Check numbers, dates, quotations, names, causality, and the context around screenshots. Verify the final wording, not just a related headline.

### 7. Spoken-script editor
Turn the evidence into conversational prose for a person speaking to camera. Prefer the creator's natural explanations, concrete examples, questions, and honest uncertainty. Avoid generic intros, repetitive signposting, fake suspense, and the standard AI cadence of three symmetrical points followed by a tidy moral. Keep an evidence ledger beside the script. Do not create citations or facts to fill a narrative gap.

### 8. Asset curator
For every source worth revisiting, decide what artifact should be kept: source PDF, relevant page screenshot, article screenshot, chart, original image, screen recording, or a note that no visual capture is needed.
Store files under the episode root. Use readable names and descriptive metadata. Register each file immediately, including source relation and review/rights state. Check whether the exact asset already exists before downloading or capturing again.

### 9. Scene planner
Input: final narration audio, normalized word/segment timestamps, approved assets, evidence ledger.
Output: scene plan with exact seconds, narration segment references, requested visual role, registered asset IDs, on-screen text, motion notes, and citation/attribution needs.
Prefer reusing an existing approved asset when it genuinely fits. If a scene needs new material, create an explicit asset request and stop it from being render-ready until the asset has been created and reviewed.

## Source capture protocol

When an agent visits a web page:
1. Store source metadata before moving on.
2. Save the PDF, article snapshot, relevant page screenshot, chart, or screen recording when it has a planned production use.
3. Register the local artifact and source relation in SQLite.
4. Record a precise locator: page number, section heading, chart title, video timecode, or paragraph.
5. Add relevant tags and a plain-English description so a later agent can search by meaning.
6. If a page cannot legally or technically be saved, record why and preserve the URL plus a short factual note. Do not fabricate a local screenshot.

Do not capture every page indiscriminately. Capture a page only when it supports a claim or provides a meaningful visual. Use source hashes and existing asset records to avoid duplicate work.

## Citation record

Minimum useful citation fields: source ID; author or institution; title; publication date; URL or complete book metadata; exact locator (PDF page, printed page, chapter, section, table, or timecode); claim IDs supported; short excerpt or summary with enough context to find it again; evidence quality and relevant limitations.

For published narration, paraphrase more than you quote. A screenshot is a research artifact, not a substitute for checking the underlying passage. Book-page numbers must match the edition actually consulted.

## Asset review and rights

Review states: candidate, approved, rejected, rights_review.
Suggested rights states: creator_owned, public_domain_verified, CC0, CC-BY, CC-BY-SA, licensed, editorial_review, fair_use_review, restricted, unknown.

Never equate "found on the internet" with "free to publish". A research screenshot may stay in the private source archive while the video uses a self-made chart, a short screen-recording with commentary, or a licensed alternative. Do not download or reproduce restricted material beyond the permitted purpose.

## Anti-loop and token budget

- One research-director pass.
- At most three complementary research lanes in round one.
- One counterevidence pass after initial findings exist.
- One independent-explanation pass.
- One fact-check pass on the near-final script.
- One asset pass that checks the catalog before making new assets.
- Retry only missing or contradictory evidence; do not ask every agent to summarize all previous agents.
- Every handoff names exact input and required output files. Avoid passing the full conversation when a concise brief and indexed evidence are enough.
