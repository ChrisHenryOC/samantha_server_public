# External references

Articles, papers, tools, and standards behind Samantha Server, plus further
reading, organized by subject.

## The Samantha project

- AI and Laboratory Workflow, Part 1 (Substack)
  <https://chenryventures.substack.com/p/ai-and-laboratory-workflow-part-1>
- AI and Laboratory Workflow, Part 2 (Substack)
  <https://chenryventures.substack.com/p/ai-and-laboratory-workflow-part-2>

## The harness is the product

- Alfonso de la Rocha, "The Model is Still Not the Product"
  ("models are a new primitive")
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
- Alfonso de la Rocha, "A Glimpse of the New Software Engineering" (Claude
  Code leak analysis)
  <https://adlrocha.substack.com/p/adlrocha-a-glimpse-of-the-new-software>
- Claude Code From Source (architecture walkthrough)
  <https://claude-code-from-source.com/ch01-architecture/>
- "AI Agent Harnesses Explained: Architecture, Ecosystem, and Multi-User
  Design" (boringbot)
  <https://boringbot.substack.com/p/ai-agent-harnesses-explained-architecture>

## Agent harnesses

- Pi, a terminal coding-agent harness <https://pi.dev>
- Hermes Agent (Nous Research)
  <https://github.com/NousResearch/hermes-agent>
- OpenCode, open-source coding agent <https://opencode.ai>
  (also one of the agents benchmarked in SWE-Bench Mobile)

## Observability and tooling

- OpenTelemetry (OTel / OTLP), the telemetry standard the engine exports
  with <https://opentelemetry.io>
- Langfuse, the OTLP-native trace backend <https://langfuse.com>
- Langfuse Workshop, the AI engineering loop end to end (hands-on tracing,
  prompt management, monitoring, datasets, experiments, evaluation)
  <https://langfuse.com/workshop>
- Anthropic, "Writing Effective Tools for Agents" (the tool-design lesson
  behind the one typed tool, `list_applicable_rules`)
  <https://www.anthropic.com/engineering/writing-tools-for-agents>
- Doneyli De Jesus, "I Built My Own Observability for Claude Code" (self-hosted
  Langfuse via a Claude Code Stop hook, the same OTLP-native backend Samantha
  exports to)
  <https://doneyli.substack.com/p/i-built-my-own-observability-for>

## Benchmarks and research

- MMLU-Pro, Wang et al., 2024
  <https://arxiv.org/abs/2406.01574> ·
  dataset <https://huggingface.co/datasets/TIGER-Lab/MMLU-Pro>
- SWE-Bench Mobile, Tian et al., Feb 2026 (one model, 6x across
  harnesses) <https://arxiv.org/abs/2602.09540> ·
  leaderboard <https://swebenchmobile.com>
- Meta-Harness, Lee et al. (Stanford / MIT), Mar 2026 (automated
  harness search beat hand-built harnesses)
  <https://arxiv.org/abs/2603.28052>
- AIOS: LLM Agent Operating System (Rutgers)
  <https://arxiv.org/abs/2403.16971>
- Matrix-OS whitepaper <https://matrix-os.com/whitepaper>

## Regulatory context

These two anchors are kept only as orientation for readers in regulated
domains.

- EU AI Act, Regulation (EU) 2024/1689
  <https://eur-lex.europa.eu/eli/reg/2024/1689/oj/eng>
- FDA, Predetermined Change Control Plan (PCCP) final guidance, Dec 2024
  <https://www.fda.gov/regulatory-information/search-fda-guidance-documents/marketing-submission-recommendations-predetermined-change-control-plan-artificial-intelligence>
