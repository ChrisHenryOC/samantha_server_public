# External references

Articles, papers, tools, and standards behind Samantha Server, plus further
reading, organized by subject.

## About the author

Chris Henry ([LinkedIn](https://www.linkedin.com/in/chrishenry1/)) ([Substack](https://chenryventures.substack.com)) is the founder of CHenry Ventures, LLC and is a seasoned technology leader with over twenty years of experience architecting enterprise platforms for healthcare and life sciences organizations. He has held senior technology and IT leadership roles in numerous organizations and has expertise spanning laboratory information systems, AI-powered digital pathology, cloud-native platform development, and cross-functional engineering leadership. His experience includes evaluating laboratory technology infrastructure, assessing scalability and technical debt, and providing strategic recommendations for technology modernization and operational improvement.


## The Samantha project

- Substack, by Chris Henry:
  [AI and Laboratory Workflow, Part 1](https://chenryventures.substack.com/p/ai-and-laboratory-workflow-part-1)
- Substack, by Chris Henry:
  [AI and Laboratory Workflow, Part 2](https://chenryventures.substack.com/p/ai-and-laboratory-workflow-part-2)

## The harness is the product

- Claude Code Leak Analysis, by Alfonso de la Rocha:
  [A Glimpse of the New Software Engineering](https://adlrocha.substack.com/p/adlrocha-a-glimpse-of-the-new-software)
- Models are a new primitive, by Alfonso de la Rocha:
  [The Model is Still Not the Product](https://adlrocha.substack.com/p/adlrocha-the-model-is-still-not-the)
- Rent the model, own the harness, by Doneyli De Jesus:
  [The Model Doesn't Matter Anymore](https://doneyli.substack.com/p/the-model-doesnt-matter-anymore)
- Gap is the tooling, not the model, by Sebastian Raschka:
  [Components of a Coding Agent](https://magazine.sebastianraschka.com/p/components-of-a-coding-agent)
- Agent = Model + Harness, by Birgitta Böckeler & Martin Fowler:
  [Harness Engineering for Coding Agent Users](https://martinfowler.com/articles/harness-engineering.html)
- Architecture walkthrough:
  [Claude Code From Source](https://claude-code-from-source.com/ch01-architecture/)
- boringbot:
  [AI Agent Harnesses Explained: Architecture, Ecosystem, and Multi-User Design](https://boringbot.substack.com/p/ai-agent-harnesses-explained-architecture)

## Agent harnesses

- Terminal coding-agent harness:
  [Pi](https://pi.dev)
- Nous Research:
  [Hermes Agent](https://github.com/NousResearch/hermes-agent)
- Open-source coding agent (also one of the agents benchmarked in
  SWE-Bench Mobile):
  [OpenCode](https://opencode.ai)

## Observability and tooling

- The telemetry standard the engine exports with:
  [OpenTelemetry (OTel / OTLP)](https://opentelemetry.io)
- The OTLP-native trace backend:
  [Langfuse](https://langfuse.com)
- The AI engineering loop end to end (hands-on tracing, prompt management,
  monitoring, datasets, experiments, evaluation):
  [Langfuse Workshop](https://langfuse.com/workshop)
- The tool-design lesson behind the one typed tool,
  `list_applicable_rules`, by Anthropic:
  [Writing Effective Tools for Agents](https://www.anthropic.com/engineering/writing-tools-for-agents)
- Self-hosted Langfuse via a Claude Code Stop hook (the same OTLP-native
  backend Samantha exports to), by Doneyli De Jesus:
  [I Built My Own Observability for Claude Code](https://doneyli.substack.com/p/i-built-my-own-observability-for)

## Benchmarks and research

- Wang et al., 2024:
  [MMLU-Pro](https://arxiv.org/abs/2406.01574) ·
  [dataset](https://huggingface.co/datasets/TIGER-Lab/MMLU-Pro)
- Tian et al., Feb 2026 (one model, 6x across harnesses):
  [SWE-Bench Mobile](https://arxiv.org/abs/2602.09540) ·
  [leaderboard](https://swebenchmobile.com)
- Lee et al. (Stanford / MIT), Mar 2026 (automated harness search beat
  hand-built harnesses):
  [Meta-Harness](https://arxiv.org/abs/2603.28052)
- Rutgers:
  [AIOS: LLM Agent Operating System](https://arxiv.org/abs/2403.16971)
- [Matrix-OS whitepaper](https://matrix-os.com/whitepaper)

## Regulatory context

These two anchors are kept only as orientation for readers in regulated
domains.

- Regulation (EU) 2024/1689:
  [EU AI Act](https://eur-lex.europa.eu/eli/reg/2024/1689/oj/eng)
- FDA, Dec 2024:
  [Predetermined Change Control Plan (PCCP) final guidance](https://www.fda.gov/regulatory-information/search-fda-guidance-documents/marketing-submission-recommendations-predetermined-change-control-plan-artificial-intelligence)
