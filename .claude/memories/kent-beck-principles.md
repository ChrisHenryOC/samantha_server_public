# Kent Beck Software Development Principles

Kent Beck is the creator of **Extreme Programming (XP)** and a leading proponent of **Test-Driven Development (TDD)**. He was one of the 17 original signatories of the Agile Manifesto.

## The Four Rules of Simple Design

In priority order:

1. **Passes the tests** - Working code is the primary goal; tests verify it works as intended
2. **Reveals intention** - Code should be easy to understand; programs are meant to be read by people
3. **No duplication** - "Everything should be said once and only once" (DRY principle)
4. **Fewest elements** - Minimize classes and methods, but only after satisfying rules 1-3

## "Make It Work, Make It Right, Make It Fast"

A practical sequencing for development:

- **Make it work** - First get the code functioning correctly
- **Make it right** - Then refactor for readability and maintainability
- **Make it fast** - Only optimize for performance after correctness and clarity are achieved

This principle advises against premature optimization. Get it working first, clean it up second, and only optimize when you have evidence of a performance problem.

## XP Core Values

- **Communication** - Team works jointly at every stage
- **Simplicity** - Write simple code; minimalism and incrementalism
- **Feedback** - Deliver frequently, iterate based on feedback
- **Respect** - Every person contributes to a common goal

## Test-Driven Development (TDD)

Two key rules:

1. Never write code unless you have a failing automated test
2. Eliminate duplication

The **red/green/refactor** cycle:

1. **Red** - Write a failing test that defines the expected behavior
2. **Green** - Write the minimum code to make the test pass
3. **Refactor** - Clean up while keeping tests green

Repeat this cycle in small increments.

## Notable Quotes

- *"Optimism is an occupational hazard of programming. Feedback is the treatment."*
- *"More time at the desk does not equal increased productivity for creative work."*
- On XP: *"Start where you are now and move towards the ideal. Could you improve a little bit?"*

## Applying These Principles

### In Code Reviews

Ask these questions in order:

1. Does it pass the tests? Are there tests?
2. Does the code reveal its intention? Can you understand it without comments?
3. Is there duplication that should be extracted?
4. Are there unnecessary elements that could be removed?

### In Development

- Write the test first (TDD)
- Get it working with the simplest possible implementation
- Refactor only after tests pass
- Optimize only when you have profiling data showing a bottleneck

### Anti-Patterns to Avoid

- Writing code "just in case"
- Abstracting before you have multiple use cases
- Optimizing without benchmarks
- Designing for hypothetical future requirements

## Sources

- [Kent Beck - Wikipedia](https://en.wikipedia.org/wiki/Kent_Beck)
- [Extreme Programming Values, Principles, and Practices](https://www.altexsoft.com/blog/extreme-programming-values-principles-and-practices/)
- [Martin Fowler - Beck Design Rules](https://martinfowler.com/bliki/BeckDesignRules.html)
- [Make It Work Make It Right Make It Fast - C2 Wiki](https://wiki.c2.com/?MakeItWorkMakeItRightMakeItFast=)
- [TDD, AI agents and coding with Kent Beck](https://newsletter.pragmaticengineer.com/p/tdd-ai-agents-and-coding-with-kent)
