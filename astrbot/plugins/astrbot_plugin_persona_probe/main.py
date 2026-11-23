"""Inspect the built-in agent's messages before context compression and provider serialization."""

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Star, register
from astrbot.core.astr_agent_context import AgentContextWrapper
from astrbot.core.astr_main_agent_resources import CHATUI_SPECIAL_DEFAULT_PERSONA_PROMPT


@register("astrbot_plugin_persona_probe", "local", "人设系统提示词探针", "1.0.0")
class PersonaProbePlugin(Star):
    @filter.on_agent_begin(priority=-1000)
    async def inspect_persona(
        self, event: AstrMessageEvent, run_context: AgentContextWrapper
    ) -> None:
        """Check the exact persona block in a system-role message.

        Args:
            event: Event carrying the persona ID resolved by the built-in agent.
            run_context: Initial agent messages after request hooks and runner reset.
        """
        persona_id = event.get_extra("_trace_persona_id")
        if not isinstance(persona_id, str) or not persona_id:
            self.logger.info("Persona probe status=no_persona_trace")
            return

        expected_blocks: list[tuple[str, str]] = []
        persona = self.context.persona_manager.get_persona_v3_by_id(persona_id)
        if persona and (prompt := persona["prompt"]):
            expected_blocks.append(
                ("persona", f"\n# Persona Instructions\n\n{prompt}\n")
            )

        if (
            persona_id == "_chatui_default_"
            and event.get_platform_name() == "webchat"
            and event.get_extra("enable_default_system_prompt") is not False
        ):
            expected_blocks.append(
                ("webchat_default", CHATUI_SPECIAL_DEFAULT_PERSONA_PROMPT)
            )

        if not expected_blocks:
            self.logger.info(
                "Persona probe status=no_expected_prompt persona_id=%r", persona_id
            )
            return

        for index, message in enumerate(run_context.messages):
            if message.role != "system" or not isinstance(message.content, str):
                continue
            for source, block in expected_blocks:
                if block in message.content:
                    self.logger.info(
                        "Persona probe status=matched role=system "
                        "persona_id=%r source=%s message_index=%d",
                        persona_id,
                        source,
                        index,
                    )
                    return

        self.logger.info(
            "Persona probe status=missing role=system persona_id=%r", persona_id
        )
