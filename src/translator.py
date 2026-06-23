"""
Translation engine — DeepSeek API (primary) + DeepL API (fallback).

Supports bidirectional translation:
- EN → ZH (loopback mode: game audio to Chinese subtitles)
- ZH → EN (microphone mode: user speech to English)

Includes an LRU cache to avoid re-translating repeated phrases.

Log policy: never logs full translation content — only character counts and latency.
"""
import logging
import os
import time
import threading
from dataclasses import dataclass, field
from typing import Optional
from collections import OrderedDict

import httpx

logger = logging.getLogger(__name__)


@dataclass
class TranslationResult:
    """Result of a translation request."""
    source_text: str
    translated_text: str
    source_lang: str = "EN"
    target_lang: str = "ZH"
    engine: str = "deepseek"
    latency_ms: float = 0.0
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.translated_text)

    @property
    def source_chars(self) -> int:
        """Character count for privacy-safe logging."""
        return len(self.source_text.strip()) if self.source_text else 0

    @property
    def translated_chars(self) -> int:
        """Character count for privacy-safe logging."""
        return len(self.translated_text.strip()) if self.translated_text else 0


class TranslationCache:
    """Thread-safe LRU cache for translations."""

    def __init__(self, capacity: int = 2000):
        self._cache = OrderedDict()
        self._capacity = capacity
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, text: str, source_lang: str, target_lang: str) -> Optional[str]:
        """Return cached translation, or None if not found."""
        key = f"{source_lang}:{target_lang}:{text}"
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                self._hits += 1
                return self._cache[key]
            self._misses += 1
            return None

    def put(self, text: str, source_lang: str, target_lang: str, translated: str) -> None:
        """Store a translation in the cache."""
        key = f"{source_lang}:{target_lang}:{text}"
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            else:
                if len(self._cache) >= self._capacity:
                    self._cache.popitem(last=False)  # evict oldest
                self._cache[key] = translated

    @property
    def hit_rate(self) -> float:
        total = self._hits + self._misses
        return self._hits / total if total > 0 else 0.0

    @property
    def stats(self) -> dict:
        return {
            "size": len(self._cache),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self.hit_rate,
        }

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0


def _clean_output(text: str, direction: str) -> str:
    """Strip AI chatter, language leaks, and formatting from translation output.

    Returns empty string if the output is invalid for the target language.
    """
    import re
    text = text.strip().strip('"').strip("'").strip()

    # Remove common AI chatter prefixes (case-insensitive)
    chatter_prefixes = [
        "翻译：", "翻译:", "译文：", "译文:", "中文：", "中文:",
        "Chinese:", "chinese:", "输出：", "输出:", "Output:", "output:",
        "Translation:", "translation:", "英文：", "英文:",
        "English:", "english:", "结果：", "结果:",
        "Here is the translation:", "The translation is:",
        "Sure", "Sure,",
    ]
    t_lower = text.lower()
    for prefix in chatter_prefixes:
        if t_lower.startswith(prefix.lower()):
            text = text[len(prefix):].strip()
            t_lower = text.lower()  # update after stripping
        elif text.startswith(prefix):
            text = text[len(prefix):].strip()
            t_lower = text.lower()

    # Remove trailing notes in parentheses
    text = re.sub(r'\s*\([^)]*(?:翻译|translation|note|注|说明)[^)]*\)\s*$', '', text, flags=re.IGNORECASE)

    if direction == "en2zh":
        # Must contain at least one Chinese character
        if not re.search(r'[一-鿿]', text):
            return ""
        # Remove isolated English words (2+ consecutive ASCII letters) that leaked through
        # But preserve numbers, punctuation, and spaces
        cleaned = re.sub(r'\b[a-zA-Z]{2,}\b', '', text)
        cleaned = re.sub(r'\s{2,}', ' ', cleaned).strip()
        # Must still have Chinese after cleaning
        return cleaned if re.search(r'[一-鿿]', cleaned) else ""

    elif direction == "zh2en":
        # Must NOT contain Chinese characters (source leakage)
        if re.search(r'[一-鿿]', text):
            return ""
        # Must contain at least some alphabetic content
        if not re.search(r'[a-zA-Z]{2,}', text):
            return ""
        # Remove any stray Chinese punctuation
        text = re.sub(r'[，。！？；：、」【】《》]', '', text)
        return text.strip()

    return text


class DeepSeekTranslator:
    """Translator using DeepSeek API (OpenAI-compatible chat completion).

    Supports bidirectional translation (EN↔ZH) with enhanced system prompts
    that include few-shot examples and strict output formatting rules.

    Usage:
        translator = DeepSeekTranslator(api_key="sk-xxx", direction="en2zh")
        result = await translator.translate("Hello world")
        # result.translated_text -> "你好世界"
    """

    ENDPOINT = "https://api.deepseek.com/v1/chat/completions"
    MODEL = "deepseek-chat"

    # Enhanced system prompts with few-shot examples and strict rules.
    # These prompts are designed to:
    #   - Produce natural, contextually accurate translations
    #   - NEVER output source-language words in the translation
    #   - NEVER output notes, explanations, or meta-commentary
    #   - Handle game/streaming context naturally
    SYSTEM_PROMPTS = {
        "en2zh": (
            "You are a high-quality English-to-Chinese translator for live streaming/gaming content. "
            "Translate the user's English speech into natural, conversational Chinese (口语化中文).\n\n"
            "CRITICAL RULES:\n"
            "1. Output ONLY the Chinese translation. Nothing else.\n"
            "2. NEVER include English words, pinyin, notes, or explanations.\n"
            "3. If the input is clearly noise/gibberish/isolated word with no meaning, output a single space.\n"
            "4. Use natural Chinese expressions, not literal translations.\n"
            "5. Keep it concise — match the original's length.\n\n"
            "Examples:\n"
            "Input: \"I need to find the key\" → \"我得找到钥匙\"\n"
            "Input: \"Watch out behind you\" → \"小心身后\"\n"
            "Input: \"Good game everyone\" → \"大家打得不错\"\n"
            "Input: \"the\" → \" \"\n"
            "Input: \"Help me with this boss\" → \"帮我打这个boss\"\n"
        ),
        "zh2en": (
            "You are a high-quality Chinese-to-English translator for live streaming/gaming content. "
            "Translate the user's Chinese speech into natural, conversational English (use contractions).\n\n"
            "CRITICAL RULES:\n"
            "1. Output ONLY the English translation. Nothing else.\n"
            "2. NEVER include Chinese characters, pinyin, notes, or explanations.\n"
            "3. If the input is clearly noise/gibberish/isolated character with no meaning, output a single space.\n"
            "4. Use natural English expressions with contractions (don't, can't, I'm, etc.).\n"
            "5. Keep it concise — match the original's length.\n\n"
            "Examples:\n"
            "Input: \"我需要帮助\" → \"I need help\"\n"
            "Input: \"小心后面\" → \"Watch out behind you\"\n"
            "Input: \"打得不错\" → \"Well played\"\n"
            "Input: \"的\" → \" \"\n"
            "Input: \"这个boss好难打\" → \"This boss is so hard\"\n"
        ),
    }

    def __init__(
        self,
        api_key: str = "",
        direction: str = "en2zh",
        timeout_sec: float = 8.0,
        max_retries: int = 2,
        cache_size: int = 2000,
        game_context: str = "",
    ):
        # API key from argument, env var, or .env file
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "DeepSeek API key required.\n"
                "  Set DEEPSEEK_API_KEY in .env file or environment variable.\n"
                "  Get one at: https://platform.deepseek.com/api_keys"
            )

        if direction not in self.SYSTEM_PROMPTS:
            raise ValueError(f"Invalid direction: {direction}. Must be 'en2zh' or 'zh2en'")
        self.direction = direction

        # Parse source/target from direction
        if direction == "en2zh":
            self.source_lang = "EN"
            self.target_lang = "ZH"
        else:
            self.source_lang = "ZH"
            self.target_lang = "EN"

        self.timeout = timeout_sec
        self.max_retries = max_retries
        self.game_context = game_context
        self.cache = TranslationCache(capacity=cache_size)
        self._client: Optional[httpx.AsyncClient] = None

        # Build system prompt with optional game context
        self._system_prompt = self.SYSTEM_PROMPTS[direction]
        if game_context:
            self._system_prompt += (
                f"\n\nGAME CONTEXT: The user is playing {game_context}. "
                "Use terminology appropriate for this game."
            )

    async def start(self) -> None:
        """Create the async HTTP client."""
        self._client = httpx.AsyncClient(
            timeout=self.timeout,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )

    async def stop(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def translate(self, text: str) -> TranslationResult:
        """Translate text using DeepSeek chat API.

        Args:
            text: Text to translate (English or Chinese depending on direction).

        Returns:
            TranslationResult with translated text.
        """
        if not text or not text.strip():
            return TranslationResult(
                source_text=text, translated_text="",
                source_lang=self.source_lang, target_lang=self.target_lang,
            )

        # Check cache first
        cached = self.cache.get(text, self.source_lang, self.target_lang)
        if cached is not None:
            return TranslationResult(
                source_text=text,
                translated_text=cached,
                source_lang=self.source_lang,
                target_lang=self.target_lang,
                engine="deepseek-cache",
                latency_ms=0.0,
            )

        if not self._client:
            await self.start()

        t0 = time.monotonic()

        for attempt in range(self.max_retries):
            try:
                response = await self._client.post(
                    self.ENDPOINT,
                    json={
                        "model": self.MODEL,
                        "messages": [
                            {"role": "system", "content": self._system_prompt},
                            {"role": "user", "content": text},
                        ],
                        "temperature": 0.1,      # very low temp = deterministic, accurate
                        "max_tokens": 256,       # translations should be concise
                    },
                )
                if response.status_code == 200:
                    data = response.json()
                    raw = data["choices"][0]["message"]["content"].strip()
                    translated = _clean_output(raw, self.direction)
                    latency = (time.monotonic() - t0) * 1000

                    # Reject garbage output (empty after cleaning or still has wrong language)
                    if not translated:
                        logger.warning(f"DeepSeek output rejected (cleaned empty): "
                                      f"raw={raw[:80]!r}")
                        return TranslationResult(
                            source_text=text,
                            translated_text="",
                            source_lang=self.source_lang,
                            target_lang=self.target_lang,
                            engine="deepseek",
                            latency_ms=latency,
                        )

                    # Cache the successful result
                    self.cache.put(text, self.source_lang, self.target_lang, translated)

                    # Privacy-safe logging: only metadata, no content
                    logger.info(
                        f"DeepSeek [{latency:.0f}ms] {self.source_lang}→{self.target_lang} "
                        f"src_chars={len(text.strip())} dst_chars={len(translated)}"
                    )
                    return TranslationResult(
                        source_text=text,
                        translated_text=translated,
                        source_lang=self.source_lang,
                        target_lang=self.target_lang,
                        engine="deepseek",
                        latency_ms=latency,
                    )

                elif response.status_code == 429:
                    wait = 1.5 * (attempt + 1)
                    logger.warning(f"DeepSeek rate limited, waiting {wait}s (attempt {attempt+1}/{self.max_retries})")
                    await _async_sleep(wait)

                elif response.status_code == 401:
                    logger.error("DeepSeek API key invalid")
                    return TranslationResult(
                        source_text=text,
                        translated_text="",
                        source_lang=self.source_lang,
                        target_lang=self.target_lang,
                        error="API key invalid (HTTP 401)",
                    )

                elif response.status_code == 402:
                    logger.error("DeepSeek: insufficient balance")
                    return TranslationResult(
                        source_text=text,
                        translated_text="",
                        source_lang=self.source_lang,
                        target_lang=self.target_lang,
                        error="Insufficient balance (HTTP 402)",
                    )

                else:
                    logger.warning(f"DeepSeek HTTP {response.status_code} (attempt {attempt+1}/{self.max_retries})")
                    if attempt < self.max_retries - 1:
                        await _async_sleep(1.0 * (attempt + 1))

            except httpx.TimeoutException:
                logger.warning(f"DeepSeek timeout (attempt {attempt+1}/{self.max_retries})")
                if attempt < self.max_retries - 1:
                    await _async_sleep(1.0)

            except Exception as e:
                logger.error(f"DeepSeek error: {e} (attempt {attempt+1}/{self.max_retries})")
                if attempt < self.max_retries - 1:
                    await _async_sleep(1.0)

        latency = (time.monotonic() - t0) * 1000
        return TranslationResult(
            source_text=text,
            translated_text="",
            source_lang=self.source_lang,
            target_lang=self.target_lang,
            error="All retries exhausted",
            latency_ms=latency,
        )


class DeeplTranslator:
    """Translator using DeepL API (free or pro tier) — fallback engine.

    Usage:
        translator = DeeplTranslator(api_key="your-key", direction="en2zh")
        result = await translator.translate("Hello world")
    """

    FREE_ENDPOINT = "https://api-free.deepl.com/v2/translate"
    PRO_ENDPOINT = "https://api.deepl.com/v2/translate"

    def __init__(
        self,
        api_key: str = "",
        direction: str = "en2zh",
        use_pro: bool = False,
        timeout_sec: float = 5.0,
        max_retries: int = 2,
        cache_size: int = 2000,
    ):
        # API key from argument, env var, or .env file
        self.api_key = api_key or os.environ.get("DEEPL_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "DeepL API key required.\n"
                "  Set DEEPL_API_KEY in .env file or environment variable.\n"
                "  Get one at: https://www.deepl.com/pro-api"
            )
        self.endpoint = self.PRO_ENDPOINT if use_pro else self.FREE_ENDPOINT
        self.timeout = timeout_sec
        self.max_retries = max_retries

        if direction == "en2zh":
            self.source_lang = "EN"
            self.target_lang = "ZH"
        elif direction == "zh2en":
            self.source_lang = "ZH"
            self.target_lang = "EN-US"
        else:
            raise ValueError(f"Invalid direction: {direction}")

        self.cache = TranslationCache(capacity=cache_size)
        self._client: Optional[httpx.AsyncClient] = None

    async def start(self) -> None:
        """Create the async HTTP client."""
        self._client = httpx.AsyncClient(timeout=self.timeout)

    async def stop(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def translate(self, text: str) -> TranslationResult:
        """Translate text via DeepL API."""
        if not text or not text.strip():
            return TranslationResult(
                source_text=text, translated_text="",
                source_lang=self.source_lang, target_lang=self.target_lang,
            )

        # Check cache first
        cached = self.cache.get(text, self.source_lang, self.target_lang)
        if cached is not None:
            return TranslationResult(
                source_text=text,
                translated_text=cached,
                source_lang=self.source_lang,
                target_lang=self.target_lang,
                engine="deepl-cache",
                latency_ms=0.0,
            )

        if not self._client:
            await self.start()

        t0 = time.monotonic()

        for attempt in range(self.max_retries):
            try:
                response = await self._client.post(
                    self.endpoint,
                    headers={
                        "Authorization": f"DeepL-Auth-Key {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "text": [text],
                        "source_lang": self.source_lang,
                        "target_lang": self.target_lang,
                    },
                )
                if response.status_code == 200:
                    data = response.json()
                    translated = data["translations"][0]["text"]
                    latency = (time.monotonic() - t0) * 1000

                    # Cache the result
                    self.cache.put(text, self.source_lang, self.target_lang, translated)

                    # Privacy-safe logging
                    logger.info(f"DeepL [{latency:.0f}ms] {self.source_lang}→{self.target_lang} "
                               f"src_chars={len(text.strip())} dst_chars={len(translated)}")
                    return TranslationResult(
                        source_text=text,
                        translated_text=translated,
                        source_lang=self.source_lang,
                        target_lang=self.target_lang,
                        engine="deepl",
                        latency_ms=latency,
                    )

                elif response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", "1"))
                    logger.warning(f"DeepL rate limited, waiting {retry_after}s (attempt {attempt+1})")
                    await _async_sleep(retry_after)

                elif response.status_code == 403:
                    logger.error("DeepL API key invalid or insufficient permissions")
                    return TranslationResult(
                        source_text=text,
                        translated_text="",
                        source_lang=self.source_lang,
                        target_lang=self.target_lang,
                        error="API key invalid (HTTP 403)",
                    )

                elif response.status_code == 456:
                    logger.error("DeepL quota exceeded")
                    return TranslationResult(
                        source_text=text,
                        translated_text="",
                        source_lang=self.source_lang,
                        target_lang=self.target_lang,
                        error="Quota exceeded (HTTP 456)",
                    )

                else:
                    logger.warning(f"DeepL HTTP {response.status_code} (attempt {attempt+1})")
                    if attempt < self.max_retries - 1:
                        await _async_sleep(1.0 * (attempt + 1))

            except httpx.TimeoutException:
                logger.warning(f"DeepL timeout (attempt {attempt+1})")
                if attempt < self.max_retries - 1:
                    await _async_sleep(1.0 * (attempt + 1))

            except Exception as e:
                logger.error(f"DeepL error: {e} (attempt {attempt+1})")
                if attempt < self.max_retries - 1:
                    await _async_sleep(1.0)

        latency = (time.monotonic() - t0) * 1000
        return TranslationResult(
            source_text=text,
            translated_text="",
            source_lang=self.source_lang,
            target_lang=self.target_lang,
            error="All retries exhausted",
            latency_ms=latency,
        )


def create_translator(config: dict, direction: str = "en2zh") -> DeepSeekTranslator | DeeplTranslator:
    """Factory: create the appropriate translator based on config and available API keys.

    Priority:
    1. config.json `translation.engine` setting
    2. Available API key (DeepSeek preferred in China, then DeepL)

    Args:
        config: Translation configuration dictionary.
        direction: "en2zh" for loopback mode, "zh2en" for microphone mode.
    """
    engine = config.get("engine", "auto")
    trans_cfg = config.copy()

    if engine == "deepseek":
        return DeepSeekTranslator(
            direction=direction,
            timeout_sec=trans_cfg.get("timeout_sec", 8.0),
            max_retries=trans_cfg.get("max_retries", 2),
            game_context=trans_cfg.get("game_context", ""),
        )
    elif engine == "deepl":
        return DeeplTranslator(
            direction=direction,
            use_pro=trans_cfg.get("use_pro", False),
            timeout_sec=trans_cfg.get("timeout_sec", 5.0),
            max_retries=trans_cfg.get("max_retries", 2),
        )
    else:
        # auto: try DeepSeek first (better China access), fallback to DeepL
        deepseek_key = os.environ.get("DEEPSEEK_API_KEY")
        deepl_key = os.environ.get("DEEPL_API_KEY")

        if deepseek_key:
            logger.info(f"Auto-selected DeepSeek (direction={direction})")
            return DeepSeekTranslator(
                direction=direction,
                timeout_sec=trans_cfg.get("timeout_sec", 8.0),
                max_retries=trans_cfg.get("max_retries", 2),
                game_context=trans_cfg.get("game_context", ""),
            )
        elif deepl_key:
            logger.info(f"Auto-selected DeepL (direction={direction})")
            return DeeplTranslator(
                direction=direction,
                use_pro=trans_cfg.get("use_pro", False),
                timeout_sec=trans_cfg.get("timeout_sec", 5.0),
                max_retries=trans_cfg.get("max_retries", 2),
            )
        else:
            raise ValueError(
                "No translation API key found.\n"
                "  Set DEEPSEEK_API_KEY or DEEPL_API_KEY in .env file.\n"
                "  DeepSeek: https://platform.deepseek.com/api_keys\n"
                "  DeepL:    https://www.deepl.com/pro-api"
            )


async def _async_sleep(seconds: float) -> None:
    """Simple async sleep."""
    import asyncio
    await asyncio.sleep(seconds)
