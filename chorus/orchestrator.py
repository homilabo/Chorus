"""Orchestrator - core logic for broadcast, cross-send, debate, synthesis."""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from chorus.config import get_config, get_provider_config
from chorus.memory import Memory
from chorus.models import LLMResponse, Message
from chorus.providers.base import get_available_providers, get_provider

logger = logging.getLogger(__name__)


class Orchestrator:
    """Routes messages to providers and manages multi-model interactions."""

    def __init__(self, memory: Memory, session_id: str, cwd: Optional[str] = None):
        self.memory = memory
        self.session_id = session_id
        self.cwd = cwd

    def _get_model_for(self, provider_name: str) -> str:
        config = get_provider_config(provider_name) or {}
        return config.get("model", "default")

    def _get_display_name(self, provider_name: str) -> str:
        config = get_provider_config(provider_name) or {}
        return config.get("display_name", provider_name)

    def _build_context_prompt(self, user_prompt: str, context: str = "") -> str:
        """Build prompt with conversation context injected."""
        if not context:
            return user_prompt
        return f"""Here is the recent conversation context with multiple AI models:

{context}

Now, the user asks:
{user_prompt}

Respond considering the conversation context above. If other models have shared perspectives, you may reference, agree with, or challenge them."""

    def ask(self, provider_name: str, prompt: str, inject_context: bool = True) -> LLMResponse:
        """Send a prompt to a single provider."""
        provider = get_provider(provider_name)
        if not provider:
            return LLMResponse(text="", model="", provider=provider_name, error=f"Provider '{provider_name}' not found")

        model = self._get_model_for(provider_name)
        cli_session = self.memory.get_provider_session(self.session_id, provider_name)

        # Inject context so the model knows what others said
        if inject_context:
            context = self.memory.get_recent_context(self.session_id)
            full_prompt = self._build_context_prompt(prompt, context)
        else:
            full_prompt = prompt

        # Save user message
        self.memory.save_message(self.session_id, Message(role="user", content=prompt, provider=provider_name))

        response = provider.generate(full_prompt, model, session_id=cli_session, cwd=self.cwd)

        # Save response and update CLI session
        if response.text and not response.error:
            self.memory.save_message(self.session_id, Message(
                role="assistant", content=response.text, provider=provider_name,
                model=response.model, duration_ms=response.duration_ms,
            ))
        if response.session_id:
            self.memory.update_provider_session(self.session_id, provider_name, response.session_id)

        return response

    def broadcast(self, prompt: str) -> dict[str, LLMResponse]:
        """Send the same prompt to all available providers in parallel."""
        available = get_available_providers()
        if not available:
            return {}

        # Save user message once
        self.memory.save_message(self.session_id, Message(role="user", content=prompt))

        # Build shared context
        context = self.memory.get_recent_context(self.session_id)
        full_prompt = self._build_context_prompt(prompt, context)

        results: dict[str, LLMResponse] = {}

        def _call(name: str):
            provider = available[name]
            model = self._get_model_for(name)
            cli_session = self.memory.get_provider_session(self.session_id, name)
            return name, provider.generate(full_prompt, model, session_id=cli_session, cwd=self.cwd)

        with ThreadPoolExecutor(max_workers=len(available)) as pool:
            futures = {pool.submit(_call, name): name for name in available}
            for future in as_completed(futures):
                try:
                    name, response = future.result()
                    results[name] = response
                    if response.text and not response.error:
                        self.memory.save_message(self.session_id, Message(
                            role="assistant", content=response.text, provider=name,
                            model=response.model, duration_ms=response.duration_ms,
                        ))
                    if response.session_id:
                        self.memory.update_provider_session(self.session_id, name, response.session_id)
                except Exception as e:
                    provider_name = futures[future]
                    results[provider_name] = LLMResponse(text="", model="", provider=provider_name, error=str(e))

        return results

    def cross_send(self, from_provider: str, to_provider: str, comment: str = "") -> LLMResponse:
        """Send one provider's last response to another for critique."""
        # Find last message from source provider
        messages = self.memory.get_session_messages(self.session_id, limit=50)
        source_msg = None
        for m in reversed(messages):
            if m.provider == from_provider and m.role == "assistant":
                source_msg = m
                break

        if not source_msg:
            return LLMResponse(text="", model="", provider=to_provider, error=f"No recent response from {from_provider}")

        from_display = self._get_display_name(from_provider)
        to_display = self._get_display_name(to_provider)

        cross_prompt = f"""{from_display} was asked the same question and responded:

---
{source_msg.content}
---

{comment if comment else f"As {to_display}, what is your perspective? Do you agree, disagree, or want to add nuance? Be specific about where you see things differently."}"""

        provider = get_provider(to_provider)
        if not provider:
            return LLMResponse(text="", model="", provider=to_provider, error=f"Provider '{to_provider}' not found")

        model = self._get_model_for(to_provider)
        cli_session = self.memory.get_provider_session(self.session_id, to_provider)

        response = provider.generate(cross_prompt, model, session_id=cli_session, cwd=self.cwd)

        if response.text and not response.error:
            self.memory.save_message(self.session_id, Message(
                role="assistant", content=response.text, provider=to_provider,
                model=response.model, duration_ms=response.duration_ms,
                cross_from=from_provider,
            ))
        if response.session_id:
            self.memory.update_provider_session(self.session_id, to_provider, response.session_id)

        return response

    def debate(self, prompt: str, rounds: int = 2) -> list[dict[str, LLMResponse]]:
        """Multi-round debate: all providers answer, then respond to each other."""
        config = get_config().get("debate", {})
        max_rounds = config.get("max_rounds", 5)
        rounds = min(rounds, max_rounds)

        available = list(get_available_providers().keys())
        if len(available) < 2:
            return []

        all_rounds = []

        # Round 0: Initial responses
        initial = self.broadcast(prompt)
        all_rounds.append(initial)

        # Subsequent rounds: each model responds to all others
        for round_num in range(1, rounds + 1):
            round_results: dict[str, LLMResponse] = {}
            prev_round = all_rounds[-1]

            # Build summary of previous round
            summary_parts = []
            for name, resp in prev_round.items():
                if resp.text and not resp.error:
                    display = self._get_display_name(name)
                    summary_parts.append(f"{display}: {resp.text[:1000]}")
            round_summary = "\n\n---\n\n".join(summary_parts)

            debate_prompt = f"""This is round {round_num + 1} of a multi-model debate on: "{prompt}"

Previous round responses:

{round_summary}

Now it's your turn again. Consider what the other models said. Where do you agree? Where do you disagree? Have you changed your mind on anything? Be specific and constructive."""

            # Save the debate prompt as system context
            self.memory.save_message(self.session_id, Message(role="system", content=f"[Debate round {round_num + 1}]"))

            for name in available:
                provider = get_provider(name)
                if not provider:
                    continue
                model = self._get_model_for(name)
                cli_session = self.memory.get_provider_session(self.session_id, name)

                response = provider.generate(debate_prompt, model, session_id=cli_session, cwd=self.cwd)
                round_results[name] = response

                if response.text and not response.error:
                    self.memory.save_message(self.session_id, Message(
                        role="assistant", content=response.text, provider=name,
                        model=response.model, duration_ms=response.duration_ms,
                    ))
                if response.session_id:
                    self.memory.update_provider_session(self.session_id, name, response.session_id)

            all_rounds.append(round_results)

        return all_rounds

    def synthesize(self, synthesizer: Optional[str] = None) -> LLMResponse:
        """Have one model synthesize all perspectives from the conversation."""
        available = list(get_available_providers().keys())
        if not available:
            return LLMResponse(text="", model="", provider="", error="No providers available")

        # Pick synthesizer: prefer claude, fallback to first available
        if synthesizer and synthesizer in available:
            synth_name = synthesizer
        elif "claude" in available:
            synth_name = "claude"
        else:
            synth_name = available[0]

        # Gather all assistant messages
        messages = self.memory.get_session_messages(self.session_id, limit=100)
        parts = []
        for m in messages:
            if m.role == "assistant" and m.provider:
                display = self._get_display_name(m.provider)
                parts.append(f"**{display}** ({m.model}): {m.content[:1500]}")

        if not parts:
            return LLMResponse(text="", model="", provider=synth_name, error="No responses to synthesize")

        synth_prompt = f"""You are synthesizing a multi-model discussion. Here are all the perspectives shared:

{chr(10).join(parts)}

Please provide a structured synthesis:
1. **Consensus**: Where do all models agree?
2. **Disagreements**: Where do they differ and why?
3. **Key Insights**: What are the most valuable points raised?
4. **Recommendation**: Based on all perspectives, what is the best path forward?

Be concise but thorough."""

        provider = get_provider(synth_name)
        model = self._get_model_for(synth_name)
        cli_session = self.memory.get_provider_session(self.session_id, synth_name)

        response = provider.generate(synth_prompt, model, session_id=cli_session, cwd=self.cwd)

        if response.text and not response.error:
            self.memory.save_message(self.session_id, Message(
                role="assistant", content=response.text, provider=synth_name,
                model=response.model, duration_ms=response.duration_ms,
            ))

        return response
