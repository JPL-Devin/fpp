# fpp-to-plantuml

`fpp_to_plantuml.py` translates FPP state machine definitions into
[PlantUML](https://plantuml.com) state diagrams.

It consumes the `fpp-ast.json` file produced by
[`fpp-to-json`](../../docs/users-guide/Analyzing-and-Translating-Models.adoc)
and emits one `@startuml ... @enduml` diagram for each `state machine`
definition in the model (including those nested inside modules).

## Usage

```sh
# 1. Generate the JSON model (writes fpp-ast.json into the current directory)
fpp-to-json my_model.fpp

# 2. Translate every state machine into PlantUML
python3 fpp_to_plantuml.py fpp-ast.json > my_model.puml

# 3. Render with PlantUML (requires the plantuml jar and graphviz)
plantuml my_model.puml          # produces my_model.png
```

The AST JSON can also be piped in on stdin:

```sh
fpp-to-json my_model.fpp && python3 fpp_to_plantuml.py < fpp-ast.json
```

### Options

| Option | Description |
| --- | --- |
| `ast_json` | Path to `fpp-ast.json` (defaults to stdin when `-` or omitted). |
| `-o`, `--output` | Write PlantUML to a file instead of stdout. |
| `--no-annotations` | Omit FPP annotations (`@ ...`) from the diagram. |

Only `fpp-ast.json` is required; the location map and analysis files emitted
by `fpp-to-json` are not used.

## How the model maps to PlantUML

| FPP construct | PlantUML |
| --- | --- |
| `state machine M` | one `@startuml`/`@enduml` diagram titled `M` |
| `state S` | `state S` (a nested block when it has sub-states) |
| `choice C { if g enter A else enter B }` | `state C <<choice>>` with `[g]` / `[else]` transitions |
| `initial do { a } enter S` | `[*] --> S : / a` |
| `entry do { a }` / `exit do { a }` | `S : entry / a` / `S : exit / a` |
| `on sig if g do { a } enter T` | `S --> T : sig [g] / a` |
| `on sig do { a }` (internal) | `S : sig / a` |

Transition targets (`enter ...`) are resolved using FPP's lexical scoping
rules (innermost enclosing scope first), and states/choices are given unique,
fully-qualified PlantUML identifiers so that names reused across nested scopes
do not collide.

## Tests

```sh
./test/run
```

The test runs the translator against the checked-in
[`test/state-machine/fpp-ast.json`](test/state-machine) fixture (generated from
the compiler's `state-machine.fpp` example) and diffs the result against
[`expected.puml`](test/state-machine/expected.puml).
