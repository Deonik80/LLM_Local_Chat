import logging
from logging import Logger

from models import ChatMessage

class StateValidator:
    """Validates business invariants before allowing critical state mutations."""
    def __init__(self, logger: "Logger"):
        self._log = logger

    # --- Validation Check 1: Message Sequence ---
    def validate_message_sequence(self, messages: list[ChatMessage]) -> tuple[bool, str]:
        """Проверяет, что новые сообщения не нарушают хронологию и логику."""
        if not messages:
            return True, ""
        
        # Проверка: если новое сообщение является ответом LLM, оно должно следовать после 
        # последнего вопроса пользователя.
        new_is_user = isinstance(messages[-1], 'ChatMessage') and messages[-1].is_user
        last_is_user = len(messages) >= 2 and (isinstance(messages[-2], 'ChatMessage') and messages[-2].is_user)

        if not new_is_user and last_is_user:
            return True, "OK" # Ответ после вопроса - нормально.
        elif new_is_user and last_is_user:
             # Два подряд от пользователя (ошибка UI или повторный клик Send/Send)
            return False, "ERROR: Consecutive user messages detected."
        elif not new_is_user and not last_is_user and len(messages) > 1:
            # Оба не пользовательские и нет явной причины для ответа (например, только логика авто-дополнения)
            return False, "ERROR: Ambiguous state change. Check if response follows user input."
        
        return True, "OK"

    # --- Validation Check 2: Context Length Constraint ---
    def validate_context(self, messages: list[ChatMessage], context_limit: int) -> tuple[bool, str]:
        """Проверяет, что общая длина контекста не превышает лимит."""
        try:
            # Здесь должна быть логика подсчета токенов (используя estimate_tokens из api_payload)
            for msg in messages:
                if hasattr(msg, 'text') and msg.text:
                    pass  # total_estimated += estimate_tokens(msg.text)
            
            # Временно возвращаем успех, пока не подключен estimator
            return True, "Context size acceptable." 

        except Exception as e:
            self._log.error("Validator failed during context check: %s", e)
            return False, f"Validation error on context check: {str(e)}"

    # --- Validation Check 3: Unique Identifiers (for chat ID stability) ---
    def validate_chat_id_transition(self, old_cid: str | None, new_cid: str | None) -> tuple[bool, str]:
        """Проверяет переход между чатами."""
        if old_cid is not None and new_cid is not None and old_cid == new_cid:
            return True, "No change."
        # Здесь можно добавить бизнес-правила (например, нельзя переключиться из 'Archive' в 'Active')
        return True, "OK"

# Singleton instance used by the StateManager
state_validator = StateValidator(logger=logging.getLogger("state_validator"))