# Blog Post Draft

Suggested title:

`The Missing Layer Between AI Agents and Real Software`

Draft text:

AI agents can call tools. They cannot use software.

The difference is not about capability in the abstract. It is about behavioral structure. Using software means understanding that operations happen in sequences with dependencies, that state can change between steps, that some failures are recoverable and others are not, and that certain actions require human approval before proceeding.

Current agent-application integration works at two levels, and neither provides this structure.

**Pixel-level integration** (computer use) has the agent looking at screenshots and clicking coordinates. It works until the layout changes, a dialog appears, or the screen looks different from training data. The agent has a model of appearance, not state.

**Function-level integration** (tool calling, MCP) gives the agent typed functions to call. This is better — the agent operates on real state. But a function list does not tell the agent what order to call things in, what to do when a call fails, when to retry versus give up, or when to pause and ask the user. The agent gets a bag of verbs with no grammar.

The missing layer is behavioral semantics. That is what ANAC provides.

## What ANAC is

ANAC (Agent-Native Application Contract) is a specification for the behavioral layer between agents and applications. An application publishes a manifest describing three things:

**Entities** — the typed objects it manages, with optional revision tracking for optimistic concurrency control. A spreadsheet has cells, rows, and ranges. A design tool has artboards, layers, and export jobs.

**Actions** — the operations it supports, classified by what they read and write, how they can fail, and what permissions they require.

**Workflows** — state machines that compose actions into multi-step operations. Each step has typed transitions: `success`, `failure`, `stale_revision`, `approved`, `rejected`, `timeout`. The manifest encodes recovery paths, retry limits, and human-in-the-loop checkpoints.

The critical design constraint is that the ANAC surface and the human UI share the same underlying state. Both views see the same entities, respect the same revision history, and are subject to the same concurrency rules.

## Why concurrency matters most

The most common failure mode for agents operating on real software is silent data corruption. The agent reads a value, reasons about it, writes back a result — but someone else changed the value in between. The write overwrites their change. Nobody notices until the damage is downstream.

ANAC prevents this with optimistic concurrency control at the workflow level. When an agent reads an entity, the context frame includes a revision identifier. When it writes, the action checks the expected revision against the current revision. If they diverge, the action returns `STALE_REVISION` and the workflow follows its declared recovery path: re-read, re-evaluate, retry.

This is not theoretical. The ANAC repository includes a spreadsheet workflow that exercises this exact loop against both a mock adapter and a live Google Sheets integration. When a concurrent edit happens between the read and write steps, the executor detects it, follows the manifest's recovery transition, re-reads the sheet, and retries. If retries are exhausted, the workflow terminates with a structured outcome that tells the orchestrator exactly what happened and why.

The agent did not invent this recovery behavior. The manifest defined it. The executor followed it.

## What the repo actually proves

The repository at [github.com/ericorr/anac-spec](https://github.com/ericorr/anac-spec) is not a specification with aspirational examples. It has four machine-enforced validation layers:

**Schema validation** checks manifest structure. **Semantic linting** checks properties the schema cannot express — transition targets resolve, revision-tracking annotations are consistent, references point to real entities. **Runtime validation** checks the payloads the executor actually emits against formal schemas for context frames, action results, and workflow outcomes. **Integration testing** runs five scenarios across two adapters with different failure profiles.

The outcome schema was deliberately left informal until a second adapter confirmed the shape. A spreadsheet adapter (SheetApp) and a design-tool adapter (VectorForge) exercise different workflow structures and different failure modes — stale revisions, retry exhaustion, and non-retryable permission errors. The same outcome shape held across both, which is when it was formalized.

A live Google Sheets adapter runs the same workflow against a real spreadsheet via the Sheets and Drive APIs, with captured traces committed in the repository.

## The LSP analogy

Before the Language Server Protocol, every editor had to write custom integration logic for every programming language — syntax highlighting, go-to-definition, error checking, all from scratch. LSP standardized the interface: languages emit semantic state, any editor consumes it. The MxN problem (M editors x N languages) collapsed to M+N.

ANAC aims at the same simplification for agent-application integration. Without a shared behavioral contract, every agent framework must learn every application's state model, failure modes, and concurrency rules independently. With a shared contract, applications emit a standard behavioral surface and any agent framework navigates it.

The analogy should not be overstated. Application behavior varies more than programming-language semantics. But the integration pattern — standardize the contract so both sides can evolve independently — is the same.

## What ANAC is not

It is not MCP. MCP defines how agents discover and call tools. ANAC defines what agents should do with those tools — in what order, under what conditions, with what failure recovery. They are complementary.

It is not a GUI replacement. The ANAC surface runs in parallel with the human interface, backed by the same state.

It is not an agent framework. It does not specify how agents reason, plan, or decide. It specifies what applications expose and what behavioral contract governs the interaction.

## The real risk

The technical path now looks plausible: the spec executes, the schemas validate, and the live adapter has been exercised against a real sheet. The adoption risk is the hard one: will application vendors actually expose behavioral semantics, or will they stop at function lists?

That question cannot be answered by a specification. It can only be answered by proving, application by application, that the behavioral layer makes agents dramatically more reliable. The concurrency story is the first proof point. The next is a real integration with an existing agent framework that shows the M+N claim is not just an analogy.

Repo: [github.com/ericorr/anac-spec](https://github.com/ericorr/anac-spec)
