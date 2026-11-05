# Conservative 9b context counter

These two tokenizer variants were derived from the read-only GGUF vocabulary and merges of
Qwen3.5:9b digest 6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7.
The manifest records their exact hashes. Model weights are neither included nor changed.

The HF tokenizer template came from a 27b package. The actual 9b vocabulary and merges were
compared independently with GGUF metadata. EOS/padding metadata differs in three fields;
this is not a claim that the complete tokenizers are identical.

The application takes the maximum count across both variants and normalization/special-token
modes, charges the complete response grammar conservatively, reserves 4096 framing tokens,
and retains the existing 32768 context and 12000/6000 output budgets. It checks observed native
usage after every response. Missing usage, overflow, unavailable assets and unknown models fail closed.

The original 12 native 9b E2E payloads were checked during CPU development. They are historical
evidence, not new cross-window qualification. The new immutable application profile still needs
its own genuine cancellation, fresh native contrast controls, unchanged E2E and full 12+28 review.
