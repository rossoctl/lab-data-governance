"""In-process NER inference for P-classification (issue #79, ADR-0023).

The fine-tuned token-classification model (a ``RobertaForTokenClassification``
git-LFS artifact, baked into the classification image; ADR-0022/0023) tags each
sub-word token of the **Classifiable text** with a BIO label. This module turns
that per-token label stream into a list of **Finding** regions —
:data:`~.detector.Annotation` ``(start, end, tag)`` triples, char offsets into the
text, one per detected finding, the tag being an NER tag (never an **Entity**;
CONTEXT.md).

The logic is ported verbatim (behaviour-preserving) from the reference eval
harness ``classification/model/utils/predict_entities.py`` — which stays intact as
the offline harness — with two deliberate shape changes for the productized path:

* The **pure token-grouping** (buffer sub-words into words, coalesce ``B-``/``I-``
  runs, merge across small gaps, drop special-token slots) is exposed as
  :func:`group_annotations`, which returns ``(start, end, tag)`` triples directly.
  It has **no torch dependency**, so it — and the whole seam around it — is
  importable and testable without torch or the ~500 MB weights.
* :func:`predict_entities` runs the sliding-window tokenization + forward pass and
  then calls :func:`group_annotations`. ``torch`` / ``transformers`` are imported
  lazily inside the functions that need them, so only the classification image
  (which installs the ``classification`` extra) pays for them; the shared
  receiver/UI/interactions image stays torch-free (ADR-0022).

The base tokenizer is ``ibm-granite/granite-embedding-125m-english`` (a RoBERTa
vocab) — baked into the image alongside the weights so startup does no network
fetch (the cluster's egress is flaky; ADR-0023).
"""

from __future__ import annotations

from typing import Any

from .detector import Annotation

# The base tokenizer the fine-tuned NER model was trained on. Its vocab must
# match the model's ``vocab_size``; baked into the classification image so
# startup does no HF-Hub fetch (ADR-0023, flaky-egress cluster).
BASE_TOKENIZER = "ibm-granite/granite-embedding-125m-english"

# Sliding-window tokenization parameters (mirrors the reference harness): text
# beyond ``MAX_LENGTH`` tokens is not silently dropped — it produces further
# overflow chunks, ``STRIDE`` tokens of which overlap the previous chunk so a
# finding straddling a window boundary is not lost.
MAX_LENGTH = 512
STRIDE = 32


# ---------------------------------------------------------------------------
# Pure token-grouping (torch-free) — ported from the reference harness.
# ---------------------------------------------------------------------------


def _merge_entities_with_gap(entities: list[dict], text: str) -> list[dict]:
    """Merge adjacent same-tag runs separated by a <=2-char non-separator gap.

    Two same-tag runs with a tiny gap between them are one finding unless the
    intervening text is a ``,`` or ``and`` (a genuine list separator), which keeps
    them apart. Ported verbatim from the reference harness."""
    if not entities:
        return []
    merged = [entities[0]]
    for i in range(1, len(entities)):
        prev = merged[-1]
        curr = entities[i]
        if curr["label"] == prev["label"] and curr["start"] - prev["end"] <= 2:
            intervening = text[prev["end"] : curr["start"]].strip()
            if intervening in {",", "and"}:
                merged.append(curr)
            else:
                prev["end"] = curr["end"]
                prev["text"] = text[prev["start"] : prev["end"]]
                prev["confidence"] = round(
                    (prev["confidence"] + curr["confidence"]) / 2, 4
                )
        else:
            merged.append(curr)
    return merged


def _apply_abbreviation_heuristic(pred_labels: list[str], tokens: list[str]) -> list[str]:
    """Rescue a personal-name initial split by a period token (e.g. ``A. Smith``).

    When an ``I-PN`` sits on a ``.`` between a capitalized alpha word and another
    ``I-`` token, promote the preceding word to ``B-PN`` so the initial joins the
    name. Ported verbatim from the reference harness."""
    for i in range(1, len(pred_labels) - 1):
        if (
            pred_labels[i] == "I-PN"
            and tokens[i] == "."
            and pred_labels[i - 1] == "O"
            and tokens[i - 1].isalpha()
            and tokens[i - 1][0].isupper()
            and pred_labels[i + 1].startswith("I-")
        ):
            pred_labels[i - 1] = "B-PN"
            pred_labels[i] = "I-PN"
    return pred_labels


def _process_buffer(buffer, current_entity, gap_token, entities, force_new_entity=False):
    """Fold one word's worth of sub-word tokens (``buffer``) into the running
    entity, or start a new one. Ported verbatim from the reference harness."""
    if not buffer:
        return current_entity, gap_token

    entity_label = None
    for tok in buffer:
        if tok["label"].startswith("B-") or tok["label"].startswith("I-"):
            entity_label = tok["label"][2:]
            break

    if entity_label is None:
        for tok in buffer:
            if tok["label"].startswith("I-"):
                tok["label"] = "B-" + tok["label"][2:]
        for tok in buffer:
            if tok["label"].startswith("B-") or tok["label"].startswith("I-"):
                entity_label = tok["label"][2:]
                break

    if entity_label:
        word_start = buffer[0]["start"]
        word_end = buffer[-1]["end"]
        avg_conf = sum(t["confidence"] for t in buffer) / len(buffer)

        if (
            not force_new_entity
            and current_entity
            and current_entity["label"] == entity_label
        ):
            if gap_token:
                current_entity["end"] = gap_token["end"]
                current_entity["score_sum"] += gap_token["confidence"]
                current_entity["token_count"] += 1
                gap_token = None
            current_entity["end"] = word_end
            current_entity["score_sum"] += avg_conf
            current_entity["token_count"] += 1
        else:
            if current_entity:
                current_entity["confidence"] = round(
                    current_entity["score_sum"] / current_entity["token_count"], 4
                )
                entities.append(current_entity)
            current_entity = {
                "start": word_start,
                "end": word_end,
                "label": entity_label,
                "score_sum": avg_conf,
                "token_count": 1,
            }
    else:
        if current_entity:
            current_entity["confidence"] = round(
                current_entity["score_sum"] / current_entity["token_count"], 4
            )
            entities.append(current_entity)
            current_entity = None

    return current_entity, gap_token


def _group_tokens_into_entities(pred_labels, offset_mapping, confidences, word_ids, tokens):
    """Buffer sub-word tokens into words and coalesce BIO runs into entity dicts.
    Ported verbatim from the reference harness."""
    entities: list[dict] = []
    current_entity = None
    gap_token = None
    buffer: list[dict] = []
    last_word_id = None

    for i, (label, (start, end), confidence, wid) in enumerate(
        zip(pred_labels, offset_mapping, confidences, word_ids)
    ):
        if start == end:
            continue

        if (
            label.startswith("B-")
            and current_entity
            and current_entity["label"] == label[2:]
            and wid != last_word_id
        ):
            prev_token = tokens[i - 1] if i > 0 else ""
            if prev_token.strip().lower() in {",", "and"}:
                current_entity["confidence"] = round(
                    current_entity["score_sum"] / current_entity["token_count"], 4
                )
                entities.append(current_entity)
                current_entity = None
                current_entity, gap_token = _process_buffer(
                    buffer, current_entity, gap_token, entities, force_new_entity=True
                )
                buffer = [
                    {"start": start, "end": end, "label": label, "confidence": confidence}
                ]
                last_word_id = wid
                continue

        if (
            label.startswith("B-")
            and current_entity
            and current_entity["label"] == label[2:]
            and wid == last_word_id
        ):
            label = "I-" + label[2:]

        if wid != last_word_id:
            current_entity, gap_token = _process_buffer(
                buffer, current_entity, gap_token, entities
            )
            buffer = []
            last_word_id = wid

        buffer.append({"start": start, "end": end, "label": label, "confidence": confidence})

    current_entity, gap_token = _process_buffer(buffer, current_entity, gap_token, entities)
    if current_entity:
        current_entity["confidence"] = round(
            current_entity["score_sum"] / current_entity["token_count"], 4
        )
        entities.append(current_entity)

    return entities


def group_annotations(
    *,
    text: str,
    tokens: list[str],
    offset_mapping: list[tuple[int, int]],
    labels: list[str],
    confidences: list[float],
    word_ids: list[int | None],
) -> list[Annotation]:
    """Group one tokenized chunk's per-token BIO *labels* into **Finding**
    annotations — ``(start, end, tag)`` triples over *text*.

    Torch-free: it operates on plain Python lists (the label strings, char
    offsets, and word ids a tokenizer/model produce), so it is unit-testable and
    importable without torch. This is the pure core :func:`predict_entities` calls
    per chunk after the forward pass.
    """
    labels = _apply_abbreviation_heuristic(list(labels), tokens)
    raw = _group_tokens_into_entities(labels, offset_mapping, confidences, word_ids, tokens)
    entities = [
        {
            "start": e["start"],
            "end": e["end"],
            "label": e["label"],
            "confidence": e["confidence"],
        }
        for e in raw
    ]
    merged = _merge_entities_with_gap(entities, text)
    return [(e["start"], e["end"], e["label"]) for e in merged]


# ---------------------------------------------------------------------------
# Torch-backed inference (torch imported lazily — classification image only).
# ---------------------------------------------------------------------------


def get_optimal_device():
    """Detect the best available device: CUDA → MPS → CPU.

    Returns ``(device, is_cuda)``. ``torch`` is imported here, not at module top,
    so the module stays importable in the torch-free shared image."""
    import torch  # local import: classification-image-only dependency (ADR-0022)

    if torch.cuda.is_available():
        return torch.device("cuda"), True
    if torch.backends.mps.is_available():
        return torch.device("mps"), False
    return torch.device("cpu"), False


def predict_entities(
    text: str,
    model: Any,
    tokenizer: Any,
    id2label: dict[int, str],
    device: Any,
    is_cuda: bool = False,
) -> list[Annotation]:
    """Run the fine-tuned NER *model* over *text*, returning **Finding**
    annotations.

    Sliding-window tokenization (``MAX_LENGTH`` / ``STRIDE`` overflow chunks) so
    text beyond one window is not dropped; a forward pass per chunk; then
    :func:`group_annotations` per chunk and a cross-chunk merge. Behaviour-
    preserving port of the reference harness's ``predict_entities``. ``torch`` is
    imported here (classification-image-only; ADR-0022).
    """
    import torch  # local import: classification-image-only dependency (ADR-0022)
    import torch.nn.functional as F

    inputs = tokenizer(
        text,
        return_tensors="pt",
        max_length=MAX_LENGTH,
        return_overflowing_tokens=True,
        return_offsets_mapping=True,
        truncation=True,
        padding=True,
        stride=STRIDE,
    )

    offset_mapping = inputs.pop("offset_mapping")
    inputs.pop("overflow_to_sample_mapping")

    inputs_original = inputs
    inputs_device = {k: v.to(device) for k, v in inputs.items()}

    model.eval()
    with torch.no_grad():
        logits = model(**inputs_device).logits
        predicted_label_ids = torch.argmax(logits, dim=-1)
        probs = F.softmax(logits, dim=-1)
        confidences = torch.max(probs, dim=-1).values.tolist()

    if is_cuda:
        torch.cuda.empty_cache()

    tokens_per_chunk = [
        tokenizer.convert_ids_to_tokens(ids.tolist())
        for ids in inputs_device["input_ids"]
    ]
    predicted_labels_per_chunk = [
        [id2label.get(cid.item(), "O") for cid in label_tensor]
        for label_tensor in predicted_label_ids
    ]

    all_findings: list[dict] = []
    for i, (pred_labels, om, confs, toks) in enumerate(
        zip(predicted_labels_per_chunk, offset_mapping, confidences, tokens_per_chunk)
    ):
        word_ids = inputs_original.word_ids(i)
        for start, end, label in group_annotations(
            text=text,
            tokens=toks,
            offset_mapping=om.tolist(),
            labels=pred_labels,
            confidences=confs,
            word_ids=word_ids,
        ):
            all_findings.append(
                {"start": start, "end": end, "label": label, "confidence": 1.0}
            )

    return [
        (e["start"], e["end"], e["label"])
        for e in _merge_entities_with_gap(all_findings, text)
    ]
