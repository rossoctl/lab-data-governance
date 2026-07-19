import os
from collections import Counter, defaultdict

import torch
import torch.nn.functional as F
from transformers import AutoModelForTokenClassification, AutoTokenizer


def get_optimal_device():
    """Detect the best available device: CUDA → MPS → CPU.

    Returns:
        (torch.device, bool): device to use, and True when it is a CUDA device.
    """
    if torch.cuda.is_available():
        return torch.device("cuda"), True
    if torch.backends.mps.is_available():
        return torch.device("mps"), False
    return torch.device("cpu"), False


def merge_entities_with_gap(entities, text):
    if not entities:
        return []
    merged = [entities[0]]
    for i in range(1, len(entities)):
        prev = merged[-1]
        curr = entities[i]
        if (
            curr["label"] == prev["label"]
            and curr["start"] - prev["end"] <= 2
        ):
            # Check if there's a separator in the text between the two entities
            intervening = text[prev["end"]:curr["start"]].strip()
            if intervening in {",", "and"}:
                merged.append(curr)
            else:
                prev["end"] = curr["end"]
                prev["text"] = text[prev["start"]:prev["end"]]
                prev["confidence"] = round((prev["confidence"] + curr["confidence"]) / 2, 4)
        else:
            merged.append(curr)
    return merged

def apply_abbreviation_heuristic(pred_labels, tokens):
    for i in range(1, len(pred_labels) - 1):
        if (
            pred_labels[i] == "I-PN"
            and tokens[i] == "."
            and pred_labels[i - 1] == "O"
            and tokens[i - 1].isalpha() and tokens[i - 1][0].isupper()
            and pred_labels[i + 1].startswith("I-")
        ):
            # print(f"[Heuristic] Abbreviation detected: merging '{tokens[i-1]}.{tokens[i+1]}'")
            pred_labels[i - 1] = "B-PN"
            pred_labels[i] = "I-PN"
    return pred_labels

def process_buffer(buffer, current_entity, gap_token, entities, force_new_entity=False):
    if not buffer:
        return current_entity, gap_token

    entity_label = None
    for tok in buffer:
        if tok["label"].startswith("B-") or tok["label"].startswith("I-"):
            entity_label = tok["label"][2:]
            break

    if entity_label is None:
        for idx, tok in enumerate(buffer):
            if tok["label"].startswith("I-"):
                # print(f"Warning: {tok['label']} at token with no preceding entity. Treated as B-{tok['label'][2:]}")
                tok["label"] = "B-" + tok["label"][2:]
        for tok in buffer:
            if tok["label"].startswith("B-") or tok["label"].startswith("I-"):
                entity_label = tok["label"][2:]
                break

    if entity_label:
        word_start = buffer[0]["start"]
        word_end = buffer[-1]["end"]
        avg_conf = sum(t["confidence"] for t in buffer) / len(buffer)

        if not force_new_entity and current_entity and current_entity["label"] == entity_label:
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
                current_entity["confidence"] = round(current_entity["score_sum"] / current_entity["token_count"], 4)
                entities.append(current_entity)
            current_entity = {
                "start": word_start,
                "end": word_end,
                "label": entity_label,
                "score_sum": avg_conf,
                "token_count": 1
            }
    else:
        if current_entity:
            current_entity["confidence"] = round(current_entity["score_sum"] / current_entity["token_count"], 4)
            entities.append(current_entity)
            current_entity = None

    return current_entity, gap_token

def group_tokens_into_entities(pred_labels, offset_mapping, confidences, word_ids, tokens):
    entities = []
    current_entity = None
    gap_token = None
    buffer = []
    last_word_id = None

    for i, (label, (start, end), confidence, wid) in enumerate(zip(pred_labels, offset_mapping, confidences, word_ids)):
        if start == end:
            continue

        if label.startswith("B-") and current_entity and current_entity["label"] == label[2:] and wid != last_word_id:
            prev_token = tokens[i - 1] if i > 0 else ""
            if prev_token.strip().lower() in {",", "and"}:
                current_entity["confidence"] = round(current_entity["score_sum"] / current_entity["token_count"], 4)
                entities.append(current_entity)
                current_entity = None
                current_entity, gap_token = process_buffer(buffer, current_entity, gap_token, entities, force_new_entity=True)
                buffer = [{"start": start, "end": end, "label": label, "confidence": confidence}]
                last_word_id = wid
                continue

        if label.startswith("B-") and current_entity and current_entity["label"] == label[2:] and wid == last_word_id:
            label = "I-" + label[2:]

        if wid != last_word_id:
            current_entity, gap_token = process_buffer(buffer, current_entity, gap_token, entities)
            buffer = []
            last_word_id = wid

        buffer.append({"start": start, "end": end, "label": label, "confidence": confidence})

    current_entity, gap_token = process_buffer(buffer, current_entity, gap_token, entities)
    if current_entity:
        current_entity["confidence"] = round(current_entity["score_sum"] / current_entity["token_count"], 4)
        entities.append(current_entity)

    return entities

import time
def single_check(texts, model, tokenizer):
    start_time = time.perf_counter()
    for text in texts:
        inputs = tokenizer(
            text, 
            padding=True, # Pad all sequences to the length of the longest in the batch
            truncation=True, 
            return_tensors="pt" # Return PyTorch tensors (or "tf" for TensorFlow)
        ).to("cuda" if torch.cuda.is_available() else "cpu")
        with torch.no_grad(): # Disable gradient calculation for inference
            outputs = model(**inputs)
    end_time = time.perf_counter()

    # Calculate and print the elapsed time
    elapsed_time = end_time - start_time
    print(f"Execution time singles: {elapsed_time:.4f} seconds")        

def batch_check(texts, batch_size, model, tokenizer):
    start_time = time.perf_counter()
    for chunk in [texts[i:i + batch_size] for i in range(0, len(texts), batch_size)]:
        inputs = tokenizer(
            chunk, 
            padding=True, # Pad all sequences to the length of the longest in the batch
            truncation=True, 
            return_tensors="pt" # Return PyTorch tensors (or "tf" for TensorFlow)
        ).to("cuda" if torch.cuda.is_available() else "cpu")

        with torch.no_grad(): # Disable gradient calculation for inference
            outputs = model(**inputs)
    end_time = time.perf_counter()

    # Calculate and print the elapsed time
    elapsed_time = end_time - start_time
    print(f"Execution time batches: {elapsed_time:.4f} seconds")        

def predict_entities(text, model, tokenizer, id2label, device, is_cuda=False):
    # Sliding-window tokenization: produces one chunk per 512-token window with
    # stride=32 so that text beyond the first 512 tokens is never silently dropped.
    inputs = tokenizer(
        text,
        return_tensors="pt",
        max_length=512,
        return_overflowing_tokens=True,
        return_offsets_mapping=True,
        truncation=True,
        padding=True,
        stride=32,
    )

    # Pop metadata tensors before moving to device; keep offset_mapping on CPU
    # so we can read character positions after inference.
    offset_mapping = inputs.pop("offset_mapping")       # shape: (num_chunks, seq_len, 2)
    inputs.pop("overflow_to_sample_mapping")

    # Retain a reference to the CPU-side BatchEncoding so word_ids(i) still works
    # (moving tensors to device creates a plain dict, losing the BatchEncoding API).
    inputs_original = inputs

    # Move input tensors to the target device.
    inputs_device = {k: v.to(device) for k, v in inputs.items()}

    model.eval()
    with torch.no_grad():
        logits = model(**inputs_device).logits          # (num_chunks, seq_len, num_labels)
        predicted_label_ids = torch.argmax(logits, dim=-1)
        probs = F.softmax(logits, dim=-1)
        confidences = torch.max(probs, dim=-1).values.tolist()

    # Clear GPU memory after inference to prevent accumulation across calls.
    if is_cuda:
        torch.cuda.empty_cache()

    # Per-chunk: convert IDs → label strings, apply heuristic, group into entities.
    tokens_per_chunk = [
        tokenizer.convert_ids_to_tokens(ids.tolist())
        for ids in inputs_device["input_ids"]
    ]
    predicted_labels_per_chunk = [
        apply_abbreviation_heuristic(
            [id2label.get(cid.item(), "O") for cid in label_tensor],
            toks,
        )
        for label_tensor, toks in zip(predicted_label_ids, tokens_per_chunk)
    ]

    all_entities = []
    for i, (pred_labels, om, confs, toks) in enumerate(
        zip(predicted_labels_per_chunk, offset_mapping, confidences, tokens_per_chunk)
    ):
        word_ids = inputs_original.word_ids(i)
        chunk_entities = group_tokens_into_entities(
            pred_labels, om.tolist(), confs, word_ids, toks
        )
        for e in chunk_entities:
            all_entities.append({
                "text": text[e["start"]:e["end"]],
                "label": e["label"],
                "start": e["start"],
                "end": e["end"],
                "confidence": e["confidence"],
            })

    # Deduplicate / merge entities that straddle chunk boundaries.
    return merge_entities_with_gap(all_entities, text)

def predict_entities_verbose(text, model, tokenizer, id2label):
    _device, _is_cuda = get_optimal_device()
    result = predict_entities(text, model, tokenizer, id2label, _device, _is_cuda)

    inputs = tokenizer(text, return_tensors="pt", return_offsets_mapping=True, truncation=True)
    offset_mapping = inputs["offset_mapping"][0].tolist()
    tokens = tokenizer.convert_ids_to_tokens(inputs["input_ids"][0])
    word_ids = inputs.word_ids(0)

    model.eval()
    with torch.no_grad():
        outputs = model(**{k: v for k, v in inputs.items() if k != "offset_mapping"})
        logits = outputs.logits[0]
        probs = F.softmax(logits, dim=-1)
        predictions = torch.argmax(probs, dim=-1).tolist()
        confidences = torch.max(probs, dim=-1).values.tolist()
        pred_labels = [id2label.get(p, "O") for p in predictions]

    word_spans = tokenizer.backend_tokenizer.pre_tokenizer.pre_tokenize_str(text)
    token_info = []
    for i, (token, (start, end), label, conf) in enumerate(zip(tokens, offset_mapping, pred_labels, confidences)):
        if start == end:
            continue
        word = None
        word_id = None
        for idx, (w, (w_start, w_end)) in enumerate(word_spans):
            if start >= w_start and end <= w_end:
                word = w
                word_id = idx
                break
        token_info.append({
            "token": token,
            "word": word,
            "word_id": word_id,
            "text_span": text[start:end],
            "label": label,
            "start": start,
            "end": end,
            "confidence": round(conf, 4)
        })

    return {"tokens": token_info, "entities": result}