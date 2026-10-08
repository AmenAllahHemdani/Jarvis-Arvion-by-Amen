import json
import sys
import time
import base64
import logging
from pathlib import Path
from typing import Optional

import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("openrouter_client")

def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR     = _get_base_dir()
API_KEY_PATH = BASE_DIR / "config" / "api_keys.json"

def _load_api_key() -> str:
    try:
        with open(API_KEY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        key = data.get("openrouter_api_key", "").strip()
        if not key:
            raise ValueError("openrouter_api_key is empty in api_keys.json")
        return key
    except FileNotFoundError:
        raise RuntimeError(f"api_keys.json not found at: {API_KEY_PATH}")
    except Exception as e:
        raise RuntimeError(f"Failed to load OpenRouter API key: {e}")

# Emergency fallback only — the real pools are discovered live from
# OpenRouter's /models endpoint (free models come and go weekly, and a
# stale hardcoded list means every call 404s until nothing answers).
_STATIC_TEXT_MODELS: list[str] = [
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "nvidia/nemotron-3.5-lightning:free",
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
]

_STATIC_VISION_MODELS: list[str] = [
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
]

# Preference order for ranking discovered models (substring match, first wins).
# Instruction-tuned chat models first — reasoning models (nemotron ultra/super)
# leak chain-of-thought into their output, so they rank last.
_MODEL_PREFERENCE = [
    "gemma-4-31b", "gemma-4", "nemotron-3.5-lightning", "laguna-s",
    "inkling", "ling-", "laguna", "gemma", "lfm",
    "nemotron-3-super", "nemotron-3-ultra", "nemotron",
]

MODELS_URL        = "https://openrouter.ai/api/v1/models"
MODELS_CACHE_TTL  = 3600  # re-discover hourly

_models_cache: dict = {"ts": 0.0, "text": [], "vision": []}
_models_lock  = __import__("threading").Lock()
_dead_models: set[str] = set()   # 404'd this session — model no longer exists


def _rank(model_id: str) -> int:
    mid = model_id.lower()
    for i, pref in enumerate(_MODEL_PREFERENCE):
        if pref in mid:
            return i
    return len(_MODEL_PREFERENCE)


def _discover_models(api_key: str) -> None:
    """Fetch the live free-model list; on failure keep the previous/static pools."""
    try:
        resp = requests.get(
            MODELS_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=20,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}")
        free = [m for m in resp.json().get("data", []) if m.get("id", "").endswith(":free")]
        if not free:
            raise RuntimeError("empty free-model list")
        free.sort(key=lambda m: _rank(m["id"]))
        text   = [m["id"] for m in free]
        vision = [m["id"] for m in free
                  if "image" in m.get("architecture", {}).get("input_modalities", [])]
        _models_cache.update({"ts": time.time(), "text": text, "vision": vision})
        logger.info(f"[OpenRouter] Discovered {len(text)} free models "
                    f"({len(vision)} with vision)")
    except Exception as e:
        logger.warning(f"[OpenRouter] Model discovery failed ({e}) — "
                       f"using previous/static list")
        if not _models_cache["text"]:
            _models_cache.update({"ts": time.time(),
                                  "text": list(_STATIC_TEXT_MODELS),
                                  "vision": list(_STATIC_VISION_MODELS)})
        else:
            _models_cache["ts"] = time.time()  # don't hammer on failure


def _get_pool(kind: str, api_key: str) -> list[str]:
    with _models_lock:
        if time.time() - _models_cache["ts"] > MODELS_CACHE_TTL:
            _discover_models(api_key)
        pool = _models_cache["vision" if kind == "vision" else "text"]
        return [m for m in pool if m not in _dead_models] or list(pool)

API_URL               = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MAX_TOKENS    = 4096
DEFAULT_TEMPERATURE   = 0.7
REQUEST_TIMEOUT       = 60   # seconds per request
MAX_RETRIES_PER_MODEL = 2    # attempts before moving to next model
RETRY_DELAY           = 2    # seconds between retries
RATE_LIMIT_COOLDOWN   = 60   # seconds before retrying a rate-limited model

_rate_limited: dict[str, float] = {}
_account_limited_until: float   = 0.0   # set when the whole account 429s

class OpenRouterClient:

    def __init__(self) -> None:
        self.api_key  = _load_api_key()
        self._headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type":  "application/json",
            "HTTP-Referer":  "https://github.com/AmenAllahHemdani/Jarvis-Arvion-by-Amen",
            "X-Title":       "Jarvis Arvion",
        }

        

    def _text_pool(self) -> list[str]:
        return _get_pool("text", self.api_key)

    def _vision_pool(self) -> list[str]:
        return _get_pool("vision", self.api_key)

    def _is_rate_limited(self, model: str) -> bool:
        ts = _rate_limited.get(model)
        if ts is None:
            return False
        if time.time() - ts > RATE_LIMIT_COOLDOWN:
            del _rate_limited[model]
            return False
        return True

    def _mark_rate_limited(self, model: str) -> None:
        _rate_limited[model] = time.time()
        logger.warning(
            f"[OpenRouter] Rate limited: {model} — "
            f"cooling down for {RATE_LIMIT_COOLDOWN}s"
        )

    def _call(
        self,
        model: str,
        messages: list[dict],
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
        response_format: Optional[dict] = None,
    ) -> Optional[str]:
        payload: dict = {
            "model":       model,
            "messages":    messages,
            "max_tokens":  max_tokens,
            "temperature": temperature,
            # keep chain-of-thought out of the reply content (reasoning models)
            "reasoning":   {"exclude": True},
        }
        if response_format:
            payload["response_format"] = response_format

        for attempt in range(1, MAX_RETRIES_PER_MODEL + 1):
            try:
                resp = requests.post(
                    API_URL,
                    headers=self._headers,
                    json=payload,
                    timeout=REQUEST_TIMEOUT,
                )

                if resp.status_code == 429:
                    self._mark_rate_limited(model)
                    return None

                if resp.status_code == 200:
                    data    = resp.json()
                    content = (
                        data.get("choices", [{}])[0]
                            .get("message", {})
                            .get("content", "")
                    )
                    return content.strip() if content else None

                if resp.status_code in (403, 404):
                    # 404: model removed. 403: model needs an account-level
                    # data-policy opt-in — either way, retrying is pointless.
                    _dead_models.add(model)
                    logger.warning(f"[OpenRouter] {model} unavailable "
                                   f"({resp.status_code}) — dropped from pool")
                    return None

                logger.warning(
                    f"[OpenRouter] {model} → HTTP {resp.status_code} "
                    f"(attempt {attempt}/{MAX_RETRIES_PER_MODEL})"
                )

            except requests.exceptions.Timeout:
                logger.warning(
                    f"[OpenRouter] {model} → Timeout "
                    f"(attempt {attempt}/{MAX_RETRIES_PER_MODEL})"
                )
            except Exception as e:
                logger.error(f"[OpenRouter] {model} → Unexpected error: {e}")

            if attempt < MAX_RETRIES_PER_MODEL:
                time.sleep(RETRY_DELAY)

        return None

    def _call_with_fallback(
        self,
        pool: list[str],
        messages: list[dict],
        model: Optional[str] = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
        response_format: Optional[dict] = None,
    ) -> str:
        if model and not self._is_rate_limited(model):
            result = self._call(model, messages, max_tokens, temperature, response_format)
            if result:
                return result
            logger.info(
                f"[OpenRouter] Requested model failed, "
                f"falling back to pool: {model}"
            )

        global _account_limited_until
        if time.time() < _account_limited_until:
            raise RuntimeError(
                "[OpenRouter] Free-tier account limit active — backing off "
                f"for {int(_account_limited_until - time.time())}s more."
            )

        limited_streak = 0
        for m in pool:
            if self._is_rate_limited(m):
                continue
            logger.info(f"[OpenRouter] Trying: {m}")
            result = self._call(m, messages, max_tokens, temperature, response_format)
            if result:
                logger.info(f"[OpenRouter] ✓ Success: {m}")
                return result

            # distinguish "this model is busy" from "my whole account is
            # capped": several different models 429ing back-to-back means
            # the account hit OpenRouter's free-tier limit — stop hammering.
            if _rate_limited.get(m, 0) > time.time() - 2:
                limited_streak += 1
                if limited_streak >= 4:
                    _account_limited_until = time.time() + 180
                    raise RuntimeError(
                        "[OpenRouter] Account-wide free-tier rate limit hit "
                        "(~50 req/day without credits; a one-time $10 credit "
                        "purchase raises it to 1000/day). Backing off 3 min."
                    )
            else:
                limited_streak = 0

        raise RuntimeError(
            "[OpenRouter] All models failed or are rate-limited. "
            "Check your API key and network connection."
        )

    def chat(
        self,
        prompt: str,
        system: str = (
            "You are a component of JARVIS ARVION, an AI assistant inspired by JARVIS. "
            "Be concise, helpful, and precise."
        ),
        model: Optional[str] = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> str:
        messages = [
            {"role": "system", "content": system},
            {"role": "user",   "content": prompt},
        ]
        return self._call_with_fallback(
            self._text_pool(), messages, model, max_tokens, temperature
        )

    def chat_json(
        self,
        prompt: str,
        system: str = (
            "Return ONLY valid JSON. "
            "No markdown fences, no extra text, no explanation."
        ),
        model: Optional[str] = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> dict:
        messages = [
            {"role": "system", "content": system},
            {"role": "user",   "content": prompt},
        ]
        raw = self._call_with_fallback(
            self._text_pool(), messages, model, max_tokens, temperature=0.2
        )

        clean = raw.strip()
        if clean.startswith("```"):
            parts = clean.split("```")
            clean = parts[1] if len(parts) > 1 else clean
            if clean.startswith("json"):
                clean = clean[4:]
        clean = clean.strip().rstrip("`").strip()

        try:
            return json.loads(clean)
        except json.JSONDecodeError as e:
            logger.error(
                f"[OpenRouter] JSON parse failed: {e}\n"
                f"Raw response (first 300 chars): {raw[:300]}"
            )
            raise ValueError(
                f"Model returned unparseable JSON: {e}\n"
                f"Raw output: {raw[:200]}"
            )

    def vision(
        self,
        prompt: str,
        image_b64: str,
        mime: str = "image/png",
        system: str = "Analyze the image and describe what you see clearly and concisely.",
        model: Optional[str] = None,
        max_tokens: int = 1024,
    ) -> str:
        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime};base64,{image_b64}"
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            },
        ]
        return self._call_with_fallback(
            self._vision_pool(), messages, model, max_tokens, temperature=0.2
        )

    def vision_from_file(
        self,
        prompt: str,
        image_path: str,
        system: str = "Analyze the image and describe what you see clearly and concisely.",
        model: Optional[str] = None,
        max_tokens: int = 1024,
    ) -> str:
        path = Path(image_path)
        mime_map = {
            ".png":  "image/png",
            ".jpg":  "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".gif":  "image/gif",
        }
        mime = mime_map.get(path.suffix.lower(), "image/png")

        with open(path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode("utf-8")

        return self.vision(prompt, image_b64, mime, system, model, max_tokens)

    def multi_turn(
        self,
        messages: list[dict],
        model: Optional[str] = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> str:
    
        return self._call_with_fallback(
            self._text_pool(), messages, model, max_tokens, temperature
        )

    def available_models(self) -> dict:
        return {
            "text_models":   self._text_pool(),
            "vision_models": self._vision_pool(),
            "rate_limited":  list(_rate_limited.keys()),
            "total_text":    len(self._text_pool()),
            "total_vision":  len(self._vision_pool()),
        }

client = OpenRouterClient()

if __name__ == "__main__":
    print("=" * 55)
    print("  JARVIS ARVION — OpenRouter Client Self-Test")
    print("=" * 55)

    print("\n[TEST 1] Basic chat...")
    try:
        reply = client.chat("Introduce yourself in one sentence.")
        print(f"  Response : {reply}")
        print(f"  Status   : PASS ✓")
    except Exception as e:
        print(f"  Status   : FAIL ✗ — {e}")

    print("\n[TEST 2] JSON mode...")
    try:
        data = client.chat_json(
            'List 3 programming languages. Format: {"languages": ["a", "b", "c"]}',
            system="Return only valid JSON. No extra text."
        )
        print(f"  Response : {data}")
        print(f"  Status   : PASS ✓")
    except Exception as e:
        print(f"  Status   : FAIL ✗ — {e}")

    print("\n[TEST 3] Multi-turn conversation...")
    try:
        history = [
            {"role": "system",    "content": "You are a helpful assistant. Be brief."},
            {"role": "user",      "content": "My name is Tony."},
            {"role": "assistant", "content": "Hello Tony, how can I help you?"},
            {"role": "user",      "content": "What is my name?"},
        ]
        reply = client.multi_turn(history)
        print(f"  Response : {reply}")
        print(f"  Status   : PASS ✓")
    except Exception as e:
        print(f"  Status   : FAIL ✗ — {e}")

    print("\n[TEST 4] Model pool info...")
    info = client.available_models()
    print(f"  Text models   : {info['total_text']}")
    print(f"  Vision models : {info['total_vision']}")
    print(f"  Rate limited  : {info['rate_limited'] or 'none'}")
    print(f"  Status        : PASS ✓")

    print("\n" + "=" * 55)
    print("  All tests complete.")
    print("=" * 55)