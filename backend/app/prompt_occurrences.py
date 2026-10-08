"""Regression.ipynb의 문장별 집계 계약. 원문이나 모델은 저장하지 않습니다."""
import hashlib
import json
import re

from typing import Annotated, Literal

from pydantic import Field, StrictBool, model_validator

from .schemas import Count, Finding, Model, Probability, RiskResult

PromptLabel = Literal["AI_steal", "prompt_injection", "abuse_act", "token_waste_repeat"]
PROMPT_LABELS = ("AI_steal", "prompt_injection", "abuse_act", "token_waste_repeat")
SentenceIndex = Annotated[int, Field(ge=1, strict=True)]

OCCURRENCE_ANALYSIS_VERSION = "sentence-occurrence-v1"
OCCURRENCE_MAX_CHARACTERS = 200000
OCCURRENCE_MAX_SENTENCES = 256
OCCURRENCE_BATCH_SIZE = 64


class OccurrenceAnalysisLimitError(ValueError):
    pass


def split_prompt_sentences(prompt):
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("문장 분석에는 비어 있지 않은 원문이 필요함.")
    if len(prompt) > OCCURRENCE_MAX_CHARACTERS:
        raise OccurrenceAnalysisLimitError("문장 분석의 원문 길이 제한을 초과함.")
    protected = bytearray(len(prompt))
    protected_pattern = re.compile(
        r"(?P<fence>\x60{3}[\s\S]*?(?:\x60{3}|\Z)|~{3}[\s\S]*?(?:~{3}|\Z))"
        r"|(?P<inline>\x60[^\x60\r\n]*\x60)"
        r'|(?P<quote>"(?:\\.|[^"\\])*"|(?<![A-Za-z0-9])'
        r"'(?:\\.|[^'\\])*'(?![A-Za-z0-9])|“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』)"
        r"|(?P<url>(?:https?://|www\.)[^\s<>\"'\x60]+)"
        r"|(?P<email>\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b)"
        r"|(?P<abbreviation>\b(?:[A-Za-z]\.){2,}|\b(?:Mr|Mrs|Ms|Dr|Prof|etc|vs)\.)",
        re.IGNORECASE,
    )
    for match in protected_pattern.finditer(prompt):
        start, end = match.span()
        if match.lastgroup == "url":
            while end > start and prompt[end - 1] in ".!?。！？":
                end -= 1
        protected[start:end] = b"\x01" * (end - start)
    spans, start, index = [], 0, 0

    def append_span(end):
        nonlocal start
        left, right = start, end
        while left < right and prompt[left].isspace():
            left += 1
        while right > left and prompt[right - 1].isspace():
            right -= 1
        if left < right:
            spans.append({"sentence_index": len(spans) + 1, "start": left, "end": right})
            if len(spans) > OCCURRENCE_MAX_SENTENCES:
                raise OccurrenceAnalysisLimitError("문장 분석의 문장 수 제한을 초과함.")
        start = end

    while index < len(prompt):
        if protected[index]:
            index += 1
            continue
        char = prompt[index]
        if char in "\r\n":
            end = index + 1
            if char == "\r" and end < len(prompt) and prompt[end] == "\n":
                end += 1
            append_span(end)
            index = end
            continue
        if char in ".!?。！？":
            previous = prompt[index - 1] if index else ""
            following = prompt[index + 1] if index + 1 < len(prompt) else ""
            if (char == "." and previous and following
                    and previous.isascii() and following.isascii()
                    and previous.isalnum() and following.isalnum()):
                index += 1
                continue
            end = index + 1
            while end < len(prompt) and not protected[end] and prompt[end] in ".!?。！？":
                end += 1
            append_span(end)
            index = end
            continue
        index += 1
    append_span(len(prompt))
    return tuple(spans)


def analyze_sentence_occurrences(prompt, whole_findings, predict_many, request_id):
    """위치는 원문 Unicode 코드 포인트이며 end는 포함하지 않습니다."""
    spans = split_prompt_sentences(prompt)
    predictions = []
    for start in range(0, len(spans), OCCURRENCE_BATCH_SIZE):
        chunk = spans[start:start + OCCURRENCE_BATCH_SIZE]
        batch = predict_many([prompt[span["start"]:span["end"]] for span in chunk])
        if len(batch) != len(chunk):
            raise ValueError("Sentence prediction count mismatch")
        predictions.extend(batch)
    whole_flags = {finding.code: finding.detected for finding in whole_findings}
    if set(whole_flags) != set(PROMPT_LABELS):
        raise ValueError("All four whole-prompt flags are required")
    sentence_counts = dict.fromkeys(PROMPT_LABELS, 0)
    occurrence_counts = dict.fromkeys(PROMPT_LABELS, 0)
    sentence_detections, occurrences, seen = [], [], set()
    content_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    def append_occurrence(occurrence):
        identity = [request_id, content_hash, occurrence["start"], occurrence["end"],
                    occurrence["label"], occurrence["source"]]
        occurrence["event_id"] = hashlib.sha256(
            json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        occurrences.append(occurrence)

    for span, prediction in zip(spans, predictions, strict=True):
        counted_flags = {}
        for label in PROMPT_LABELS:
            detected = prediction["risk_flags"][label]
            if type(detected) is not bool:
                raise ValueError("Sentence flags must be booleans")
            sentence_counts[label] += int(detected)
            counted = detected and whole_flags[label]
            counted_flags[label] = counted
            identity = (span["start"], span["end"], label)
            if counted and identity not in seen:
                seen.add(identity)
                occurrence_counts[label] += 1
                append_occurrence(dict(span, label=label, source="sentence"))
        sentence_detections.append(dict(
            span, risk_flags=prediction["risk_flags"], probabilities=prediction["probabilities"],
            counted_flags=counted_flags,
        ))
    for label in PROMPT_LABELS:
        if whole_flags[label] and occurrence_counts[label] == 0:
            occurrence_counts[label] = 1
            append_occurrence({"sentence_index": None, "start": 0, "end": len(prompt),
                               "label": label, "source": "whole_prompt_fallback"})
    return dict(occurrence_status="ok", occurrence_analysis_version=OCCURRENCE_ANALYSIS_VERSION,
                sentence_count=len(spans), label_sentence_counts=sentence_counts,
                label_occurrence_counts=occurrence_counts, sentence_detections=sentence_detections,
                occurrences=occurrences, occurrence_error=None)


class PromptSentenceDetection(Model):
    sentence_index: SentenceIndex
    start: Count
    end: Count
    risk_flags: dict[PromptLabel, StrictBool]
    probabilities: dict[PromptLabel, Probability]
    counted_flags: dict[PromptLabel, StrictBool]

    @model_validator(mode="after")
    def valid_sentence(self):
        if self.start >= self.end:
            raise ValueError("Sentence offsets must be a nonempty half-open interval")
        if any(set(values) != set(PROMPT_LABELS)
               for values in (self.risk_flags, self.probabilities, self.counted_flags)):
            raise ValueError("Sentence analysis requires all four prompt labels")
        return self


class PromptOccurrence(Model):
    event_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    label: PromptLabel
    source: Literal["sentence", "whole_prompt_fallback"]
    sentence_index: SentenceIndex | None
    start: Count
    end: Count

    @model_validator(mode="after")
    def valid_occurrence(self):
        if self.start >= self.end:
            raise ValueError("Occurrence offsets must be a nonempty half-open interval")
        if (self.source == "sentence") != (self.sentence_index is not None):
            raise ValueError("Only sentence occurrences have a sentence index")
        return self


class PromptOccurrenceError(Model):
    code: Literal["occurrence_limit_exceeded", "occurrence_analysis_failed"]
    message: str


class PromptFinding(Finding):
    occurrence_count: Count | None = None


class PromptRiskResult(RiskResult):
    """백엔드 연결 전 엔진 전용 반환 형식입니다."""
    findings: list[PromptFinding]
    occurrence_status: Literal["not_analyzed", "ok", "error"] = "not_analyzed"
    occurrence_analysis_version: Literal["sentence-occurrence-v1"] | None = None
    sentence_count: Count | None = None
    label_sentence_counts: dict[PromptLabel, Count] | None = None
    label_occurrence_counts: dict[PromptLabel, Count] | None = None
    sentence_detections: list[PromptSentenceDetection] | None = None
    occurrences: list[PromptOccurrence] | None = None
    occurrence_error: PromptOccurrenceError | None = None

    @model_validator(mode="after")
    def consistent_occurrences(self):
        details = (self.sentence_count, self.label_sentence_counts, self.label_occurrence_counts,
                   self.sentence_detections, self.occurrences)
        if self.occurrence_status != "ok":
            if any(value is not None for value in details) or any(f.occurrence_count is not None for f in self.findings):
                raise ValueError("Unverified occurrence counts must be null")
            if self.occurrence_status == "error":
                if (self.engine != "data" or self.status != "complete"
                        or self.occurrence_error is None or self.occurrence_analysis_version is None):
                    raise ValueError("Occurrence errors require a complete data classification and error metadata")
            elif self.occurrence_error is not None or self.occurrence_analysis_version is not None:
                raise ValueError("Unanalyzed results cannot have occurrence metadata")
            return self
        if (self.engine != "data" or self.status != "complete" or self.occurrence_error is not None
                or self.occurrence_analysis_version is None or any(value is None for value in details)):
            raise ValueError("Complete occurrence analysis requires all details")
        labels = set(PROMPT_LABELS)
        whole = {finding.code: finding for finding in self.findings}
        if (set(whole) != labels or len(self.findings) != len(labels)
                or set(self.label_sentence_counts) != labels or set(self.label_occurrence_counts) != labels
                or any(f.detected is None or f.threshold is None for f in self.findings)):
            raise ValueError("Occurrence analysis requires four threshold classifications")
        if self.sentence_count != len(self.sentence_detections):
            raise ValueError("Sentence count must match sentence details")
        sentences, sentence_counts = {}, dict.fromkeys(PROMPT_LABELS, 0)
        previous_end = 0
        for index, sentence in enumerate(self.sentence_detections, 1):
            if sentence.sentence_index != index or sentence.start < previous_end:
                raise ValueError("Sentence indices and offsets must be ordered")
            previous_end = sentence.end
            sentences[index] = sentence
            for label in PROMPT_LABELS:
                if (sentence.risk_flags[label] != (sentence.probabilities[label] > whole[label].threshold)
                        or sentence.counted_flags[label] != (sentence.risk_flags[label] and whole[label].detected)):
                    raise ValueError("Sentence flags must preserve threshold and whole-prompt gating")
                sentence_counts[label] += int(sentence.risk_flags[label])
        if sentence_counts != self.label_sentence_counts:
            raise ValueError("Sentence label counts must match details")
        counts, seen, identities = dict.fromkeys(PROMPT_LABELS, 0), set(), set()
        for occurrence in self.occurrences:
            identity = (occurrence.start, occurrence.end, occurrence.label, occurrence.source)
            if occurrence.event_id in seen or identity in identities or not whole[occurrence.label].detected:
                raise ValueError("Occurrences must be unique and pass whole-prompt gating")
            seen.add(occurrence.event_id)
            identities.add(identity)
            counts[occurrence.label] += 1
            if occurrence.source == "sentence":
                sentence = sentences.get(occurrence.sentence_index)
                if (sentence is None or not sentence.counted_flags[occurrence.label]
                        or (occurrence.start, occurrence.end) != (sentence.start, sentence.end)):
                    raise ValueError("Occurrence must reference a counted sentence")
            elif sentence_counts[occurrence.label] or occurrence.start != 0:
                raise ValueError("Whole-prompt fallback requires no sentence detections")
        for label, finding in whole.items():
            expected = max(1, sentence_counts[label]) if finding.detected else 0
            if counts[label] != expected or self.label_occurrence_counts[label] != expected or finding.occurrence_count != expected:
                raise ValueError("Occurrence counts must match details and finding counts")
        return self
