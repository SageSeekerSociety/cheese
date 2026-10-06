"""Conversation execution and harness ownership.

``ask`` posts an agent's question into the room and holds no turn open.
``initial_admission`` owns a turn's first-input admission;
``queries.session_agent_in_room`` resolves existing room/session policy to
values for admission and deferred-message recovery.
"""
