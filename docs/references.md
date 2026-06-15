# References

## About the author

Chris Henry ([LinkedIn](https://www.linkedin.com/in/chrishenry1/)) ([Substack](https://chenryventures.substack.com)) is the founder of CHenry Ventures, LLC and is a seasoned technology leader with over twenty years of experience architecting enterprise platforms for healthcare and life sciences organizations. He has held senior technology and IT leadership roles in numerous organizations and has expertise spanning laboratory information systems, AI-powered digital pathology, cloud-native platform development, and cross-functional engineering leadership. His experience includes evaluating laboratory technology infrastructure, assessing scalability and technical debt, and providing strategic recommendations for technology modernization and operational improvement.


## The Samantha project

- [AI and Laboratory Workflow, Samantha Part 1](https://chenryventures.substack.com/p/ai-and-laboratory-workflow-part-1)
- [AI and Laboratory Workflow, Samantha Part 2](https://chenryventures.substack.com/p/ai-and-laboratory-workflow-part-2)
- Samantha Server writeup (coming soon)

## The harness is the product

- Claude Code leak analysis:
  [A Glimpse of the New Software Engineering](https://adlrocha.substack.com/p/adlrocha-a-glimpse-of-the-new-software), by Alfonso de la Rocha
- Models are a new primitive:
  [The Model is Still Not the Product](https://adlrocha.substack.com/p/adlrocha-the-model-is-still-not-the), by Alfonso de la Rocha
- Rent the model, own the harness:
  [The Model Doesn't Matter Anymore](https://doneyli.substack.com/p/the-model-doesnt-matter-anymore), by Doneyli De Jesus
- Gap is the tooling, not the model:
  [Components of a Coding Agent](https://magazine.sebastianraschka.com/p/components-of-a-coding-agent), by Sebastian Raschka
- Agent = Model + Harness:
  [Harness Engineering for Coding Agent Users](https://martinfowler.com/articles/harness-engineering.html), by Birgitta Böckeler & Martin Fowler
- Architecture walkthrough:
  [Claude Code From Source](https://claude-code-from-source.com/ch01-architecture/), by Alejandro Balderas
- [AI Agent Harnesses Explained: Architecture, Ecosystem, and Multi-User Design](https://boringbot.substack.com/p/ai-agent-harnesses-explained-architecture), by Hamza Farooq and Aishwarya Ashok

## Agent harnesses

- [Pi agent](https://pi.dev)
- [Hermes Agent](https://github.com/NousResearch/hermes-agent)
- [OpenCode](https://opencode.ai)

## Observability and tooling

- The telemetry standard the engine exports with:
  [OpenTelemetry (OTel / OTLP)](https://opentelemetry.io)
- The OTLP-native trace backend:
  [Langfuse](https://langfuse.com)
- The AI engineering loop end to end (hands-on tracing, prompt management,
  monitoring, datasets, experiments, evaluation):
  [Langfuse Workshop](https://langfuse.com/workshop)
- The tool-design lesson behind the one typed tool,
  `list_applicable_rules`:
  [Writing Effective Tools for Agents](https://www.anthropic.com/engineering/writing-tools-for-agents), by Anthropic
- Self-hosted Langfuse via a Claude Code Stop hook (the same OTLP-native
  backend Samantha exports to):
  [I Built My Own Observability for Claude Code](https://doneyli.substack.com/p/i-built-my-own-observability-for), by Doneyli De Jesus

## Benchmarks and research

- MMLU-Pro Benchmark:
  [MMLU-Pro](https://arxiv.org/abs/2406.01574) ·
  [dataset](https://huggingface.co/datasets/TIGER-Lab/MMLU-Pro), by Wang et al., 2024
- One model, 6x across harnesses:
  [SWE-Bench Mobile](https://arxiv.org/abs/2602.09540) ·
  [leaderboard](https://swebenchmobile.com), by Tian et al., Feb 2026
- Automated harness search beat
  hand-built harnesses:
  [Meta-Harness](https://arxiv.org/abs/2603.28052), by Lee et al. (Stanford / MIT), Mar 2026
- [AIOS: LLM Agent Operating System](https://arxiv.org/abs/2403.16971), by Kai Mei, et al, Rutgers University, August 2025

## Regulatory context

These two anchors are kept only as orientation for readers in regulated
domains.

- Regulation (EU) 2024/1689:
  [EU AI Act](https://eur-lex.europa.eu/eli/reg/2024/1689/oj/eng)
- FDA, Dec 2024:
  [Predetermined Change Control Plan (PCCP) final guidance](https://www.fda.gov/regulatory-information/search-fda-guidance-documents/marketing-submission-recommendations-predetermined-change-control-plan-artificial-intelligence)
