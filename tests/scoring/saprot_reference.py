"""The official SaProt mutation scoring, used as a test oracle.

``predict_mut`` is copied from ``model/saprot/saprot_foldseek_mutation_model.py`` of
https://github.com/westlake-repl/SaProt at commit ``e91e4858b55944523f1f8d385f7b96a0d3d34c1d``
(MIT licence, Copyright (c) 2023 westlake-repl), apart from typing, formatting and taking the
model and tokenizer as arguments instead of ``self``. ProteinGym's
``proteingym/baselines/saprot/compute_fitness.py`` holds the same function. Nothing here is
imported by ``src/``.
"""

from typing import Any

import torch

foldseek_struc_vocab = "pynwrqhgdlvtmfsaeikc#"


def predict_mut(model: Any, tokenizer: Any, seq: str, mut_info: str) -> float:
    tokens = tokenizer.tokenize(seq)
    for single in mut_info.split(":"):
        pos = int(single[1:-1])
        tokens[pos - 1] = "#" + tokens[pos - 1][-1]

    mask_seq = " ".join(tokens)
    inputs = tokenizer(mask_seq, return_tensors="pt")

    with torch.no_grad():
        outputs = model(**inputs)
        logits = outputs.logits
        probs = logits.softmax(dim=-1)

        score = torch.tensor(0.0)
        for single in mut_info.split(":"):
            ori_aa, pos, mut_aa = single[0], int(single[1:-1]), single[-1]
            ori_st = tokenizer.get_vocab()[ori_aa + foldseek_struc_vocab[0]]
            mut_st = tokenizer.get_vocab()[mut_aa + foldseek_struc_vocab[0]]

            ori_prob = probs[0, pos, ori_st : ori_st + len(foldseek_struc_vocab)].sum()
            mut_prob = probs[0, pos, mut_st : mut_st + len(foldseek_struc_vocab)].sum()

            score += torch.log(mut_prob / ori_prob)

    return float(score.item())
