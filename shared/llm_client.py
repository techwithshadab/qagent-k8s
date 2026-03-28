"""
Shared LLM client — 3-tier fallback chain.

Tier 1 (primary):   VERTEX_API_KEY   → Gemini via generativelanguage.googleapis.com
Tier 2 (fallback):  DEEPSEEK_API_KEY → DeepSeek v3 via Ollama Cloud (/api/chat over httpx)
Tier 3 (final):     GCP_SA_KEY_PATH  → Gemini via Vertex AI service account (paid)

Fallback logic:
  • ResourceExhausted (quota) on primary → try DeepSeek (Ollama Cloud)
  • Any error on DeepSeek               → try SA Vertex AI

Uses LangChain's native with_fallbacks() — returned object is a proper Runnable,
compatible with both direct .invoke() and prompt | llm pipe chains.
"""

import logging
import os
import time

import httpx
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from prometheus_client import Counter, Histogram

logger = logging.getLogger(__name__)

# ── LLM metrics ───────────────────────────────────────────────────

LLM_REQUESTS = Counter(
    "qagent_llm_requests_total",
    "Total LLM calls per tier",
    ["tier"],  # "primary", "deepseek", "sa"
)
LLM_DURATION = Histogram(
    "qagent_llm_duration_seconds",
    "LLM call latency per tier",
    ["tier"],
    buckets=[0.5, 1, 2, 5, 10, 30, 60, 120],
)
LLM_FALLBACKS = Counter(
    "qagent_llm_fallbacks_total",
    "Times a fallback tier was triggered",
    ["to_tier"],  # "deepseek", "sa"
)


# ── Ollama Cloud wrapper (uses httpx, no extra packages needed) ───────────────

class _OllamaCloudLLM(BaseChatModel):
    """
    Minimal LangChain chat model for Ollama Cloud.
    Calls POST /api/chat with Bearer auth using httpx (already a dependency).
    Supports both sync and async invoke — compatible with prompt | llm chains.
    """

    model: str
    api_key: str
    base_url: str = "https://ollama.com"
    temperature: float = 0.2

    @property
    def _llm_type(self) -> str:
        return "ollama-cloud"

    def _to_ollama_messages(self, messages: list[BaseMessage]) -> list[dict]:
        role_map = {"human": "user", "ai": "assistant", "system": "system"}
        return [{"role": role_map.get(m.type, m.type), "content": m.content}
                for m in messages]

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        t0 = time.perf_counter()
        LLM_FALLBACKS.labels(to_tier="deepseek").inc()
        try:
            resp = httpx.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "messages": self._to_ollama_messages(messages),
                    "stream": False,
                    "options": {"temperature": self.temperature},
                },
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=120.0,
            )
            resp.raise_for_status()
            content = resp.json()["message"]["content"]
        finally:
            LLM_REQUESTS.labels(tier="deepseek").inc()
            LLM_DURATION.labels(tier="deepseek").observe(time.perf_counter() - t0)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        t0 = time.perf_counter()
        LLM_FALLBACKS.labels(to_tier="deepseek").inc()
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    f"{self.base_url}/api/chat",
                    json={
                        "model": self.model,
                        "messages": self._to_ollama_messages(messages),
                        "stream": False,
                        "options": {"temperature": self.temperature},
                    },
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                resp.raise_for_status()
                content = resp.json()["message"]["content"]
        finally:
            LLM_REQUESTS.labels(tier="deepseek").inc()
            LLM_DURATION.labels(tier="deepseek").observe(time.perf_counter() - t0)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])


# ── tier builders ─────────────────────────────────────────────────────────────

def _primary_llm(temperature: float):
    """Gemini via Vertex Express API key."""
    from langchain_google_genai import ChatGoogleGenerativeAI
    return ChatGoogleGenerativeAI(
        model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite"),
        google_api_key=os.environ["VERTEX_API_KEY"],
        temperature=temperature,
        client_options={"api_endpoint": "generativelanguage.googleapis.com"},
    )


def _deepseek_llm(temperature: float) -> _OllamaCloudLLM:
    """DeepSeek v3 via Ollama Cloud."""
    return _OllamaCloudLLM(
        model=os.environ.get("DEEPSEEK_MODEL", "deepseek-v3"),
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://ollama.com"),
        temperature=temperature,
    )


def _sa_llm(temperature: float):
    """Gemini via GCP service account on Vertex AI (paid, no quota limits)."""
    from langchain_google_vertexai import ChatVertexAI
    from google.oauth2 import service_account

    sa_path = os.environ.get("GCP_SA_KEY_PATH", "/app/secrets/sa-key.json")
    creds = service_account.Credentials.from_service_account_file(
        sa_path, scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    return ChatVertexAI(
        model_name=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite"),
        credentials=creds,
        project=os.environ.get("GOOGLE_CLOUD_PROJECT", "datacouch-vertexai"),
        location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
        temperature=temperature,
    )


# ── public API ────────────────────────────────────────────────────────────────

def get_llm(temperature: float = 0.2):
    """
    Return a Runnable with 3-tier automatic fallback:
      1. Vertex API key (Gemini)          — primary
      2. Ollama Cloud / DeepSeek v3       — on ResourceExhausted from primary
      3. GCP service account (Vertex AI)  — on any error from DeepSeek
    Tiers 2 and 3 are skipped if their credentials are absent.
    """
    from google.api_core.exceptions import ResourceExhausted

    primary = _primary_llm(temperature)

    sa_path = os.environ.get("GCP_SA_KEY_PATH", "/app/secrets/sa-key.json")
    has_sa = os.path.exists(sa_path)
    has_deepseek = bool(os.environ.get("DEEPSEEK_API_KEY"))

    if has_deepseek:
        deepseek = _deepseek_llm(temperature)
        logger.info("DeepSeek fallback enabled (Ollama Cloud %s, model=%s)",
                    os.environ.get("DEEPSEEK_BASE_URL", "https://ollama.com"),
                    os.environ.get("DEEPSEEK_MODEL", "deepseek-v3"))

        if has_sa:
            # Any error from DeepSeek (auth, timeout, etc.) → fall through to SA
            deepseek = deepseek.with_fallbacks([_sa_llm(temperature)])
            logger.info("SA final fallback enabled (Vertex AI project=%s)",
                        os.environ.get("GOOGLE_CLOUD_PROJECT", "datacouch-vertexai"))

        # Quota error from primary → DeepSeek (which itself has SA backing it)
        return primary.with_fallbacks(
            [deepseek],
            exceptions_to_handle=(ResourceExhausted,),
        )

    if has_sa:
        logger.info("SA fallback enabled (Vertex AI, no DeepSeek key set)")
        return primary.with_fallbacks(
            [_sa_llm(temperature)],
            exceptions_to_handle=(ResourceExhausted,),
        )

    logger.warning("No fallback configured — running on primary API key only")
    return primary


def chat(llm, system_prompt: str, user_message: str) -> str:
    """Single-turn chat. Fallback is handled inside the LLM wrapper."""
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_message),
    ]
    t0 = time.perf_counter()
    LLM_REQUESTS.labels(tier="primary").inc()
    try:
        return llm.invoke(messages).content
    finally:
        LLM_DURATION.labels(tier="primary").observe(time.perf_counter() - t0)


__all__ = ["get_llm", "chat"]
