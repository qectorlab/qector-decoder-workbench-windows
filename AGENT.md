# AGENT.md  -  QECTOR Decoder Workbench MCP Operating Directive

**Audience:** AI agents (Claude, or any MCP-capable client) that drive the QECTOR
Decoder Workbench through its Model Context Protocol server.
**Authority:** This file is a *directive*, not a suggestion. When it conflicts
with a guess, the directive wins. When it conflicts with a tool's actual output,
**the tool output wins**  -  never override a real result with a remembered one.

This document is generated from, and kept in sync with, `mcp_server.py`,
`backend.py`, `autodebug.py`, and `version.py`. If any fact here disagrees with
those modules, the code is the source of truth  -  re-derive, do not improvise.

---

## 0. TL;DR operating rules

1. **Never fabricate a number.** Every quantitative claim (latency, logical error
   rate, throughput, success rate, qubit/check counts) MUST come from a specific
   tool call in this session, quoted with its inputs (family, distance, decoder,
   `seed`, `n_samples`, `error_rate`). No tool call → no number.
2. **Discover, don't assume.** Get the valid decoders/families/hardware from
   `list_decoders`, `list_code_families`, `get_hardware_info`  -  not from memory.
3. **Verify every decode.** A correction is only "correct" if `syndrome_valid` is
   `true`. Report `logical_failure` honestly; `null` means the code exposes no
   logicals matrix, which is *unknown*, not *success*.
4. **Honor honest errors.** A tool result with `"isError": true` is a real
   failure. Surface it verbatim; do not paper over it with a plausible-sounding
   answer.
5. **Reproducibility is mandatory.** Always pass an explicit `seed`. Same inputs
   ⇒ identical outputs; if you can't reproduce it, you can't claim it.

---

## 1. The MCP server: hard facts

| Property | Value (from code) |
|---|---|
| Server name (`serverInfo.name`) | `qector-workbench` |
| Server version | `WORKBENCH_VERSION` = **1.0.3** |
| Backend | `qector_decoder_v3` **1.0.0** (min supported 1.0.0) |
| MCP protocol version | **2024-11-05** |
| Transport | newline-delimited **JSON-RPC 2.0** over **stdio** |
| Tool count | **85** (call `list_tools` for the live set) |
| Methods | `initialize`, `notifications/initialized`, `ping`, `tools/list`, `tools/call` |

### Launch

```bash
# Installed .deb / AppImage:
qector-workbench --mcp
QectorWorkbench-Portable.exe --mcp        # Windows portable

# From source (Linux/ tree):
python main.py --mcp
```

`--mcp` is headless: it needs **no display**. The GUI (no `--mcp`) needs X11/Wayland.

### Wire protocol

- Request: `{"jsonrpc":"2.0","id":<id>,"method":"tools/call","params":{"name":"<tool>","arguments":{...}}}`
- **Tool success:** JSON-RPC `result` = `{"content":[{"type":"text","text":"<JSON string>"}],"isError":false}`.
  The `text` field is a **JSON-encoded** payload  -  parse it, don't regex it.
- **Tool-level failure:** same envelope with `"isError": true` and the error
  message in `text`. This is a *successful* JSON-RPC response reporting a failed
  operation  -  treat it as the operation failing.
- **Protocol errors** (JSON-RPC `error` object): `-32700` parse error,
  `-32601` method not found, `-32603` internal error.
- `ping` returns `{}`. `notifications/initialized` returns no response.

---

## 2. The tools (authoritative list; 85 in backend 1.0.0)

Grouped by purpose. Parameter defaults are the server's; **always set `seed`
explicitly** for anything stochastic. Tools marked ⚠ mutate server state.

### Discovery / introspection (read-only, safe)
- `list_tools`  -  all MCP tools.
- `list_decoders`  -  the 17 wired decoders (see §3).
- `list_code_families`  -  the 10 code families (see §3).
- `get_decoder_info` `{decoder_name}`  -  description of one decoder.
- `get_code_properties` `{family_name, distance}`  -  n_qubits/n_checks/etc.
- `analyze_code_family` `{family_name, distance}`  -  build + summarize an instance.
- `get_hardware_info`  -  real CUDA/OpenCL availability (no fabricated GPUs).
- `get_system_info`, `get_statistics`, `get_config`, `mcp_status`  -  server/runtime state.

### Decode / benchmark (compute; deterministic under `seed`)
- `decode_single` `{family, distance, decoder_name, error_rate, seed}`  -  one
  seeded decode → correction weight, `syndrome_valid`, `logical_failure`.
- `benchmark_decoder` `{decoder_name, code_family, distance, error_rate, n_samples, seed}`
   -  latency percentiles (p50/p99), throughput, logical error rate. Does **not** store.
- `run_benchmark` `{code_family, distance, decoder_name, n_samples, seed, error_rate}` ⚠
   -  like `benchmark_decoder` but **stores** the result under a returned `result_id`.
- `batch_decode` `{family, distance, backend, n_samples, error_rate, seed}`  - 
  batch decode on `backend` ∈ {`cpu`,`cuda`,`opencl`}. **No silent fallback:** an
  unavailable GPU backend fails loudly (use `resilient_decode` for fallback).
- `stream_decode` `{family, distance, window_size, n_rounds, error_rate, seed, decoder_name}`
   -  sliding-window streaming session.
- `recommend_decoder` `{family, distance, n_qubits, priority}`  -  heuristic
  recommendation (`priority` ∈ {`balanced`,`speed`,`accuracy`}) from detected hardware.
- `decode_with_options` `{family, distance, decoder_name, error_rate, seed, decoder_options}`
   -  seeded decode with validated per-decoder construction options (bp_osd
  `bp_method`/`osd_order`, hybrid_cascade `escalation`, GNN architecture);
  `options_applied` reports honestly whether the backend accepted them.
- `decode_syndrome` `{family, distance, decoder_name, syndrome, decoder_options}`  - 
  decode an explicit 0/1 syndrome (length `n_checks`). `syndrome_valid` is the
  GF(2) re-check; `logical_failure` is `null` (no reference error ⇒ unknowable).
- `hybrid_cascade_stats` `{family, distance, n_samples, error_rate, seed, escalation}`
   -  seeded batch through `hybrid_cascade` exposing live cascade counters
  (prefilter_hits, escalations, hit rate, throughput, syndrome-match rate, LER).
- `gnn_belief_match_decode` `{family, distance, error_rate, seed, gnn_hidden_size,
  gnn_n_layers}` / `belief_match_decode` `{family, distance, error_rate, seed}`  - 
  convenience seeded decodes pinned to the GNN / belief-matching kinds.
- `neural_predecoder_train` `{family, distance, n_samples, n_epochs, error_rate,
  seed}`  -  research/lab: train the NeuralPredecoder MLP and evaluate on a
  disjoint held-out seed stream. Not a wired decoder; never quote its accuracy
  as a decode-quality claim.
- `batch_decode_gpu` `{family, distance, backend, n_samples, error_rate, seed}`  - 
  batch decode on an explicit backend with honest availability reporting:
  an unavailable GPU backend returns `status="unavailable"` + reason (no fakes).
- `compatible_decoders` `{family, distance}`  -  live probe: which decoder kinds
  construct and produce a syndrome-verified correction on this code.

### Self-test / resilience (the anti-hallucination toolkit)
- `self_diagnostics`  -  full environment/decoder/hardware self-test →
  `overall_status` ∈ {`pass`,`degraded`,`fail`}. **Run this first** each session.
- `probe_decoders` `{family, distance, error_rate, seed}`  -  which decoders produce
  a **syndrome-verified** correction for this code. Use before trusting a decoder.
- `resilient_decode` `{family, distance, decoder_name, error_rate, seed}`  -  one
  decode with automatic multi-decoder fallback + a full attempt trace.

### Results / documentation / resources ⚠ (state-changing where noted)
- `get_results` `{limit}`, `compare_benchmarks` `{benchmarks:[result_id...]}`.
- `export_benchmark` `{benchmark_id, format}` ⚠  -  writes to the export dir.
- `clear_results` `{confirm}` ⚠  -  requires `confirm:true`.
- `generate_documentation` `{family_key, param, formats}` ⚠  -  writes files.
  Declared formats: **json, markdown, html, latex, pdf** (the doc generator also
  emits SVG; the GUI exposes it).
- `get_resources`, `get_resource {resource_id}`, `delete_resource {resource_id, confirm}` ⚠.

### Client / config admin ⚠
- `register_client` `{client_id, access_level}`, `list_clients`  -  an informational
  registry (`access_level` is metadata, **not** a hard security boundary; do not
  claim it enforces permissions).
- `set_config {config}` ⚠, `reset_config {confirm}` ⚠.

The 1.0.0 backend adds tools covering the new decoders (`gnn_belief_match_decode`,
`belief_match_decode`, `hybrid_cascade_stats`), explicit-syndrome and option-aware
decoding (`decode_syndrome`, `decode_with_options`), neural pre-decoder training
(`neural_predecoder_train`, research/lab), honest GPU batch (`batch_decode_gpu`),
and the live compatibility probe (`compatible_decoders`)  -  **85 tools** total;
`list_tools` remains the live authority.

> If you need a tool not in this list, it does not exist. Call `list_tools` to
> confirm  -  never invent a tool name or an argument the schema doesn't declare.

---

## 3. Domain facts you may rely on (verified against backend 1.0.0)

**17 decoders:** `union_find`, `fast_union_find`, `blossom`, `sparse_blossom`,
`bp_osd`, `auto`, `hybrid`, `lookup_table`, `predecoded`, `auto_router`,
`hybrid_cascade`, `gnn_belief_matching`, `belief_matching`, `two_stage`,
`ambiguity_cluster`, `colour_code`, `space_time`.

**10 code families:** `repetition`, `ring`, `rotated_surface`,
`unrotated_surface`, `toric`, `heavy_hex`, `bicycle`, `bivariate_bicycle`,
`hypergraph_product`, `color_code`.

**Accuracy vs. speed (from the package's own docstrings  -  do not over-state):**
- `blossom` = weight-optimal **exact** MWPM (reaches PyMatching's LER; not faster).
- `sparse_blossom` = region-growing, **near-optimal, NOT exact**.
- `union_find` / `fast_union_find` = fast, **approximate**, higher LER than exact
  MWPM. Never quote a fixed speed/LER ratio  -  regenerate it on the target workload.
- `bp_osd` = belief-propagation + OSD, the decoder for **LDPC / qLDPC** codes;
  accepts `bp_method` (exact|min_sum) and `osd_order` (0|1|2) options.
- `hybrid_cascade` = Union-Find pre-filter with Blossom/BP-OSD escalation
  (graphlike only); exposes prefilter stats.
- `gnn_belief_matching` = GNN-weighted belief matching with a faithfulness
  fallback to plain MWPM (graphlike only).
- `belief_matching` = BP posteriors reweight an exact Blossom matching step;
  faithfulness-checked with MWPM fallback.

**qLDPC caveat (critical):** `bicycle` and `bivariate_bicycle` are non-graphlike.
Matching/union-find decoders and the native `auto` decoder do **not** apply to
their high-weight checks  -  recommend/`bp_osd` (or `blossom` on tiny instances).
`recommend_decoder` already returns `bp_osd` for these; trust it.

**`lookup_table`** materializes a `2**n_checks` table  -  refused above **20 checks**.
Do not suggest it for large codes.

**Batch backends:** `cpu` always works; `cuda`/`opencl` only if
`get_hardware_info` reports them available. The standard wheel ships CUDA but no
OpenCL kernels, so `opencl` is typically unavailable  -  say so, don't pretend.

---

## 4. STRICT NON-HALLUCINATION RULES

These are non-negotiable. Violating them is a defect, not a style choice.

1. **No number without a call.** Do not state a latency, LER, throughput,
   success rate, qubit count, or check count unless a tool returned it *this
   session*. Attach the provenance: tool + `{family, distance, decoder, seed,
   n_samples, error_rate}`.
2. **No invented identifiers.** Decoder names, family names, tool names, argument
   names, `result_id`s, and formats come only from `list_*`/`get_*` outputs or a
   returned id. If it's not in the schema/output, it does not exist.
3. **Measured ≠ documented.** Distinguish "I measured X (benchmark_decoder,
   seed=42, n=100)" from "the docs describe X as exact MWPM." Never present a
   docstring property as a measured result, or vice-versa.
4. **`syndrome_valid` gates correctness.** Only call a decode "successful" when
   `syndrome_valid == true`. If `false`, the decoder failed on this code  -  report
   it and fall back (`probe_decoders` / `resilient_decode`).
5. **`logical_failure == null` means unknown.** The code exposes no logicals
   matrix (e.g. some toric/unrotated-surface builds in 1.0.0). Never report `null`
   as "no logical failure"  -  report it as *not determinable*.
6. **Errors are results.** On `"isError": true`, quote the message and stop  - 
   do not synthesize a plausible answer to fill the gap. On a JSON-RPC `-326xx`
   error, report the protocol failure; do not retry blindly in a loop.
7. **No fabricated hardware.** GPU/CUDA/OpenCL claims come only from
   `get_hardware_info`. "cuda unavailable" is a valid, honest answer.
8. **Reproducibility or silence.** If you cannot name the exact `seed` and inputs
   that produced a result, do not report the result as fact.
9. **Cite the versions.** When it matters, state workbench 1.0.3 / backend 1.0.0.
   They are separate release lines and must not be assumed equal.
   Do not claim behavior from other versions you have not run.
10. **When unsure, run a tool or say "unknown."** "I don't have a measurement for
    that" is always acceptable. Inventing one never is.

---

## 5. AIO Skill  -  All-In-One standard operating procedure

A single, repeatable playbook for any QECTOR task via MCP. Follow it top to bottom;
skip a step only when the prior output makes it unnecessary.

### Phase A  -  Preflight (once per session)
1. `initialize` → confirm `serverInfo.name == "qector-workbench"` and protocol
   `2024-11-05`.
2. `self_diagnostics` → require `overall_status` ∈ {`pass`,`degraded`}. On `fail`,
   stop and report the failing checks; do not proceed to compute.
3. `get_hardware_info` → record CUDA/OpenCL availability for later routing.

### Phase B  -  Discovery (before naming anything)
4. `list_code_families` and `list_decoders` → validate that the family/decoder the
   task needs actually exists. `get_code_properties {family, distance}` for sizes.

### Phase C  -  Route (pick the right decoder honestly)
5. If family ∈ {`bicycle`,`bivariate_bicycle`} → use `bp_osd`.
6. Else call `recommend_decoder {family, distance, priority}` and use its answer,
   OR run `probe_decoders {family, distance, seed}` and pick a decoder whose
   result is `syndrome_valid`. Never route by assumption.

### Phase D  -  Act (deterministically)
7. Single check: `decode_single {..., seed}` → confirm `syndrome_valid`.
8. Performance claim: `benchmark_decoder {..., seed, n_samples}` (or `run_benchmark`
   to persist a `result_id`). Report p50/p99/throughput/LER **with the inputs**.
9. Batch: `batch_decode {..., backend}` only for a backend `get_hardware_info`
   confirmed. For automatic hardware fallback use the resilient path instead.
10. Robustness: if a chosen decoder fails, `resilient_decode {..., seed}` and
    report which fallback recovered it (from the attempt trace).

### Phase E  -  Verify & report
11. Re-run the key result with the same `seed` if a claim is load-bearing; identical
    output confirms reproducibility.
12. Report only tool-sourced facts, each with provenance:
    `tool(args) → value`. Separate *measured* from *documented*. State versions.
    If any step returned `isError` or `logical_failure: null`, say so plainly.

### AIO checklist (paste-ready)
```
[ ] initialize ok (name=qector-workbench, proto=2024-11-05)
[ ] self_diagnostics != fail
[ ] hardware recorded (cuda?/opencl?)
[ ] family + decoder exist (list_*)
[ ] decoder routed (qLDPC→bp_osd | recommend_decoder | probe_decoders)
[ ] every decode: syndrome_valid == true
[ ] every number: has {family,distance,decoder,seed,n_samples,error_rate}
[ ] errors/null reported verbatim, not smoothed over
[ ] result reproducible under the stated seed
```

---

## 6. What NOT to do (anti-patterns)

- ❌ "union_find is ~3× faster with the same accuracy." (fabricated ratio, and
  union_find is *not* exact  -  see §3)
- ❌ "The [[144,12,12]] code decoded with 0 logical failures." when
  `logical_failure` was `null`. (unknown ≠ zero)
- ❌ Calling a decoder "successful" while `syndrome_valid` was `false`.
- ❌ Inventing `tools/call` names like `optimize_code` or args like `max_iters`.
- ❌ Claiming a GPU ran a batch when `get_hardware_info` said CUDA/OpenCL absent.
- ❌ Reporting a benchmark without the `seed`/`n_samples` that produced it.

## 7. What good looks like

> `benchmark_decoder(decoder_name="blossom", code_family="rotated_surface",
> distance=5, error_rate=0.05, n_samples=100, seed=42)` → p50 = 41.2 µs,
> p99 = 88.7 µs, throughput = 22.4k decodes/s, logical_error_rate = 0.03.
> (measured this session; blossom is exact MWPM per the 1.0.0 docstrings.)
> Hardware: `get_hardware_info` → cuda=false, opencl=false → CPU decode only.

Every claim above is traceable to a call with explicit inputs. That is the bar.
