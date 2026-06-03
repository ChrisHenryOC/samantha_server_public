# External references

Articles, papers, tools, and standards referenced in the CONVEX 2026 talk
"The Model Is Not the Product," plus further reading. This is the page the
closing slide points to ("links to all articles, guides, etc. are in the
README").

All 24 links verified live via tinyfish on 2026-06-02: every URL resolves and
its page title matches the cited source (including the EU AI Act and FDA pages
that 403 to plain bots, the headless browser reaches them).

## The Samantha project

- AI and Laboratory Workflow, Part 1 (Substack)
  <https://chenryventures.substack.com/p/ai-and-laboratory-workflow-part-1>
- AI and Laboratory Workflow, Part 2 (Substack)
  <https://chenryventures.substack.com/p/ai-and-laboratory-workflow-part-2>

## The harness is the product (cited in the talk)

- Alfonso de la Rocha, "The Model is Still Not the Product" (the
  talk's title echoes this; "models are a new primitive")
  <https://adlrocha.substack.com/p/adlrocha-the-model-is-still-not-the>
- Doneyli De Jesus, "The Model Doesn't Matter Anymore" (the
  "rent the model, own the harness" framing)
  <https://doneyli.substack.com/p/the-model-doesnt-matter-anymore>
- Sebastian Raschka, "Components of a Coding Agent" (the
  "gap is the tooling, not the model" point)
  <https://magazine.sebastianraschka.com/p/components-of-a-coding-agent>
- Birgitta Böckeler & Martin Fowler, "Harness Engineering for Coding Agent
  Users" ("Agent = Model + Harness")
  <https://martinfowler.com/articles/harness-engineering.html>
- SWE-Bench Mobile, Tian et al., Feb 2026 (one model, 6x across
  harnesses) <https://arxiv.org/abs/2602.09540> ·
  leaderboard <https://swebenchmobile.com>
- Meta-Harness, Lee et al. (Stanford / MIT), Mar 2026 (automated
  harness search beat hand-built harnesses)
  <https://arxiv.org/abs/2603.28052>
- Alfonso de la Rocha, "A Glimpse of the New Software Engineering" (Claude
  Code leak analysis)
  <https://adlrocha.substack.com/p/adlrocha-a-glimpse-of-the-new-software>
- Claude Code From Source (architecture walkthrough)
  <https://claude-code-from-source.com/ch01-architecture/>

## Agent harnesses to learn from

- Pi, a terminal coding-agent harness <https://pi.dev>
- Hermes Agent (Nous Research)
  <https://github.com/NousResearch/hermes-agent>
- OpenCode, open-source coding agent <https://opencode.ai>
  (also one of the agents benchmarked in SWE-Bench Mobile)

## Tools and standards used by Samantha Server

- OpenTelemetry (OTel / OTLP), the telemetry standard the engine exports
  with <https://opentelemetry.io>
- Langfuse, the OTLP-native trace backend <https://langfuse.com>
- Anthropic, "Writing Effective Tools for Agents" (the tool-design lesson
  behind the one typed tool, `list_applicable_rules`)
  <https://www.anthropic.com/engineering/writing-tools-for-agents>

## Benchmarks

- MMLU-Pro, Wang et al., 2024 (the "Published" benchmark column)
  <https://arxiv.org/abs/2406.01574> ·
  dataset <https://huggingface.co/datasets/TIGER-Lab/MMLU-Pro>

## Further reading (not cited directly in the talk)

- "AI Agent Harnesses Explained: Architecture, Ecosystem, and Multi-User
  Design" (boringbot)
  <https://boringbot.substack.com/p/ai-agent-harnesses-explained-architecture>
- AIOS: LLM Agent Operating System (Rutgers)
  <https://arxiv.org/abs/2403.16971>
- Matrix-OS whitepaper <https://matrix-os.com/whitepaper>

### Regulatory context (optional, pruned)

The talk does not make regulatory claims on-slide; these two anchors are kept
only as orientation for readers in regulated domains.

- EU AI Act, Regulation (EU) 2024/1689
  <https://eur-lex.europa.eu/eli/reg/2024/1689/oj/eng>
- FDA, Predetermined Change Control Plan (PCCP) final guidance, Dec 2024
  <https://www.fda.gov/regulatory-information/search-fda-guidance-documents/marketing-submission-recommendations-predetermined-change-control-plan-artificial-intelligence>
