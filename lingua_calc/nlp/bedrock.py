from __future__ import annotations

import json
import re
import textwrap
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import datetime

import boto3
from botocore.config import Config as BotoConfig
from pydantic import TypeAdapter

from lingua_calc.config import Settings, get_settings
from lingua_calc.models import ParsedToken

_SYSTEM_PROMPT = textwrap.dedent(
        """\
        You are a classical Greek linguistics assistant. Given a passage of Ancient Greek \
        (Koine or Attic learner text), output a JSON array only—no markdown, no commentary.

        Each element must be an object with exactly these string fields:
        - "type": part of speech or category (noun, verb, adjective, article, pronoun, \
            preposition, conjunction, particle, numeral, other).
        - "lemma": dictionary lemma (Greek script).
        - "form": the surface form as it appears in the text (Greek script).
        - "parse": brief morphological label in a normalized abbreviated format.

        Use only these parse abbreviations and order:
        - cases: nom., gen., dat., acc., voc.
        - numbers: sg., pl.
        - voices: act., mid., pass.
        - moods: ind., subj., opt., imp., inf., part.
        - tenses: pres., impf., fut., aor., perf., plup.
        - persons: 1, 2, 3 followed by sg. or pl. when applicable.

        Examples:
        - noun phrase: "nom. sg.", "gen. pl.", "acc. sg. fem."
        - verb: "pres. act. ind. 3sg", "aor. pass. ind. 1pl", "impf. mid. subj. 3pl"
        - article: "def. art. nom. sg.", "indef. art. acc. pl."
        - if no morphological parse applies: "-"

        IMPORTANT: Only output one token object per lexical item. Do not output tokens for punctuation or whitespace. Omit any token whose type would be "punctuation" or that represents only spacing.

        Tokenize in reading order left-to-right. Prefer preserving elided forms as the text shows them.

        If uncertain, choose the best scholarly guess and still fill all four fields using the normalized parse format.
        """
)

logger = logging.getLogger(__name__)
LOGS_DIR = Path("logs")
LOGS_DIR.mkdir(exist_ok=True)


def _split_paragraphs(text: str) -> list[str]:
    parts = re.split(r"\n\s*\n+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _split_long_paragraph(para: str, max_chars: int) -> list[str]:
    """Break a single paragraph that exceeds ``max_chars`` into smaller pieces.

    Splits on Greek/Latin sentence enders first (. · ; ! ?), and hard-splits by
    character count only as a last resort. This guarantees every piece is
    <= max_chars, so no chunk is large enough to blow past the model's max_tokens
    output cap (which causes a truncated, discarded call + sequential re-split).
    """
    if len(para) <= max_chars:
        return [para]

    sentences = re.split(r"(?<=[.·;!?])\s+", para)
    pieces: list[str] = []
    buf = ""
    for s in sentences:
        if len(s) > max_chars:
            # A single sentence is itself too long: flush, then hard-split it.
            if buf:
                pieces.append(buf)
                buf = ""
            for i in range(0, len(s), max_chars):
                pieces.append(s[i : i + max_chars])
            continue
        if buf and len(buf) + 1 + len(s) > max_chars:
            pieces.append(buf)
            buf = s
        else:
            buf = f"{buf} {s}" if buf else s
    if buf:
        pieces.append(buf)
    return pieces


def _chunk_text(text: str, max_chars: int) -> list[str]:

    paras = _split_paragraphs(text)
    if not paras:
        return [text.strip()] if text.strip() else []

    # Expand any oversized paragraph so every unit packed below is <= max_chars.
    paras = [piece for p in paras for piece in _split_long_paragraph(p, max_chars)]

    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for p in paras:
        add = len(p) + (2 if buf else 0)
        if buf and size + add > max_chars:
            chunks.append("\n\n".join(buf))
            buf = [p]
            size = len(p)
        else:
            if buf:
                size += add
            else:
                size = len(p)
            buf.append(p)
    if buf:
        chunks.append("\n\n".join(buf))
    return chunks


def _extract_json_array(raw: str) -> str:
    s = raw.strip()
    # If code-fenced JSON present, use it first.
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", s, re.I)
    if fence:
        candidate = fence.group(1).strip()
        try:
            json.loads(candidate)
            return candidate
        except Exception:
            # fall through to more robust extraction
            s = candidate

    # Find the first '[' and attempt to find a matching ']' by progressive parsing.
    first = s.find("[")
    if first == -1:
        raise ValueError("Model output did not contain a JSON array.")

    # Try progressively larger slices ending at successive ']' positions.
    end_positions = [m.start() for m in re.finditer(r"\]", s)]
    for end_pos in end_positions:
        if end_pos <= first:
            continue
        candidate = s[first : end_pos + 1]
        try:
            json.loads(candidate)
            return candidate
        except Exception:
            continue

    # As a last resort, try to balance brackets by scanning and counting (naive).
    depth = 0
    for i in range(first, len(s)):
        ch = s[i]
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                candidate = s[first : i + 1]
                try:
                    json.loads(candidate)
                    return candidate
                except Exception:
                    break

    raise ValueError("Model output did not contain a JSON array.")


class BedrockClaudeProvider:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        # Configure boto3 client with a larger read timeout to handle big files
        botocore_cfg = BotoConfig(
            read_timeout=self._settings.bedrock_timeout_seconds,
            connect_timeout=10,
            # Adaptive retries back off automatically if parallel calls hit
            # Bedrock throttling limits.
            retries={"max_attempts": 5, "mode": "adaptive"},
        )
        # Use credentials from .env / Settings if present; otherwise fall back to
        # boto3's default chain (env vars / ~/.aws / IAM role) so a developer's
        # existing setup keeps working.
        client_kwargs = {
            "region_name": self._settings.aws_region,
            "config": botocore_cfg,
        }
        if self._settings.aws_access_key_id and self._settings.aws_secret_access_key:
            client_kwargs["aws_access_key_id"] = self._settings.aws_access_key_id
            client_kwargs["aws_secret_access_key"] = self._settings.aws_secret_access_key
        self._client = boto3.client("bedrock-runtime", **client_kwargs)
        self._adapter = TypeAdapter(list[ParsedToken])

    def analyze_chapter(self, text: str, chapter_title: str) -> list[ParsedToken]:
        if not text.strip():
            return []

        chunks = _chunk_text(text, self._settings.max_chunk_chars)
        if not chunks:
            return []

        merged: list[ParsedToken] = []

        def _process_chunk(chunk_text: str, chunk_index: int, depth: int = 0) -> list[ParsedToken]:
            """Process a single chunk, splitting further on max_tokens stop if necessary."""
            user = textwrap.dedent(
                f"""\
                Chapter title (context only): {chapter_title!r}
                Chunk {chunk_index} of {len(chunks)}.

                Greek text:
                {chunk_text}
                """
            )

            model_text, stop_reason = self._invoke(user)

            if stop_reason == "max_tokens" and depth < 4:
                # split into smaller chunks and retry
                sub_max = max(1000, int(self._settings.max_chunk_chars // (2 ** (depth + 1))))
                subchunks = _chunk_text(chunk_text, sub_max)
                if len(subchunks) == 1:
                    # fall back to midpoint split
                    mid = len(chunk_text) // 2
                    subchunks = [chunk_text[:mid], chunk_text[mid:]]
                out: list[ParsedToken] = []
                for j, sc in enumerate(subchunks, start=1):
                    out.extend(_process_chunk(sc, f"{chunk_index}.{j}", depth + 1))
                return out

            # try extraction (with clarifier retry on failure)
            try:
                arr_json = _extract_json_array(model_text)
            except ValueError:
                try:
                    (LOGS_DIR / f"bedrock_fail_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}_{chunk_index}.txt").write_text(model_text, encoding="utf-8")
                except Exception:
                    logger.exception("Failed to write failing raw_text to logs")
                # retry once with clarifier
                clarifier = "\n\nReturn exactly a single JSON array only, containing objects with fields: type, lemma, form, parse. Use normalized parse labels such as nom. sg., gen. pl., pres. act. ind. 3sg, aor. pass. ind. 1pl, or '-' when not applicable. Do NOT output punctuation or whitespace tokens. Do NOT include any commentary or code fences."
                model_text2, stop2 = self._invoke(user + clarifier)
                if stop2 == "max_tokens" and depth < 4:
                    # treat as max_tokens and split
                    mid = len(chunk_text) // 2
                    return _process_chunk(chunk_text[:mid], f"{chunk_index}.r1", depth + 1) + _process_chunk(chunk_text[mid:], f"{chunk_index}.r2", depth + 1)
                arr_json = _extract_json_array(model_text2)

            try:
                data = json.loads(arr_json)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON from model: {e}") from e
            tokens = self._adapter.validate_python(data)
            return tokens

        if len(chunks) == 1:
            return _process_chunk(chunks[0], 1)

        # Chunks are independent; process them concurrently. ThreadPoolExecutor.map
        # preserves input order, so `merged` stays in reading order.
        workers = max(1, min(self._settings.max_workers, len(chunks)))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            per_chunk = ex.map(
                lambda ic: _process_chunk(ic[1], ic[0]),
                list(enumerate(chunks, start=1)),
            )
        for tokens in per_chunk:
            merged.extend(tokens)
        return merged

    def _invoke(self, user_message: str) -> str:
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 16000,
            "temperature": 0.1,
            "system": _SYSTEM_PROMPT,
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": user_message}],
                }
            ],
        }
        resp = self._client.invoke_model(
            modelId=self._settings.bedrock_model_id,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )
        body_bytes = resp["body"].read()
        try:
            payload = json.loads(body_bytes)
        except Exception as e:
            logger.exception("Failed to parse Bedrock response body: %s", e)
            (LOGS_DIR / "bedrock_raw_failures.bin").write_bytes(body_bytes)
            raise

        parts = payload.get("content") or []
        model_text = ""
        if parts and parts[0].get("type") == "text":
            model_text = parts[0].get("text") or ""
        else:
            logger.warning("Unexpected Bedrock response shape; persisting payload.")
            (LOGS_DIR / f"bedrock_unexpected_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.json").write_text(
                json.dumps({"resp": payload, "input_len": len(user_message)}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            raise RuntimeError("Unexpected Bedrock response shape.")

        record = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "model_id": self._settings.bedrock_model_id,
            "input_len": len(user_message),
            "payload": payload,
            "model_text": model_text,
            "stop_reason": payload.get("stop_reason"),
        }
        try:
            with (LOGS_DIR / "bedrock_payloads.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            logger.exception("Failed to write Bedrock log record")

        return model_text, payload.get("stop_reason")
