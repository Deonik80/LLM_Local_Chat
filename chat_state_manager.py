import base64
import mimetypes
from pathlib import Path

def _encode_image(path: Path | str) -> tuple[str, str]:
    """Локальный энкодер картинок (без зависимости от app.py, чтобы не было цикла)."""
    import mimetypes as _mt

    p = Path(path)
    mime, _ = _mt.guess_type(str(p))
    mime = mime or "image/png"
    return (base64.b64encode(p.read_bytes()).decode("ascii"), mime)


from event_bus import EventBus
from models import ChatMessage, Attachment  # noqa: F401  (Attachment используется в send_message)
from repositories import ChatRepository as _ChatRepository, SettingsRepository as _SettingsRepository  # noqa: F401
from api_payload import APIPayloadBuilder as _APIPayloadBuilder  # noqa: F401

# Placeholder for external dependencies (need to be properly imported in the full refactor)
# For now, we define placeholders and assume they will be fixed by patching app.py later.
class ChatStateManager:
    """Manages application state and business logic flow."""
    def __init__(self, chat_repo: '_ChatRepository', settings_repo: '_SettingsRepository', 
                 payload: '_APIPayloadBuilder'):
        
        self.chat_repo = chat_repo
        self.settings_repo = settings_repo
        self.payload = payload

        # State containers (Must match original structure)
        self.state: dict = {"loaded_model": None, "model_touched": False}
        self.current_chat_id: str | None = None

    @property
    def settings(self) -> dict:
        return self.settings_repo.load()

    # --- State & Context Management Methods ---

    async def set_chat_id(self, cid: str):
        """Updates the active chat context and notifies subscribers."""
        self.current_chat_id = cid
        await EventBus.publish("state:chat_id_changed", cid)

    def get_active_chat(self) -> list[ChatMessage]:
        """Loads messages for the currently active chat ID (Synchronous read)."""
        cid = self.current_chat_id or next((c['id'] for c in self.chat_repo.load_index()), None)
        if not cid: return []
        return list(self.chat_repo.load_chat(cid))

    # --- Core Actions (Refactored from global functions) ---

    async def open_chat(self, cid: str):
        """Loads a chat and resets the view state."""
        await self.set_chat_id(cid)
        messages = self.get_active_chat()
        await EventBus.publish("state:chat_loaded", messages, cid)

    async def set_folder(self, cid: str, name: str | None):
        """Changes the folder assignment for a chat."""
        success = await self.settings_repo.set_folder(cid, name)
        if success:
            await EventBus.publish("chat:folder_changed", cid, new_name=name)

    async def toggle_pin(self, cid: str):
        """Toggles the pinned status of a chat."""
        success = await self.settings_repo.toggle_pin(cid)
        if success:
            await EventBus.publish("chat:pinned_status_changed", cid, pinned=True)

    async def send_message(self, user_text: str, files: list['Path']) -> tuple[ChatMessage, dict]:
        """Processes and sends the message, handling state update."""
        if not self.current_chat_id: raise Exception("No active chat.")

        # 1. State preparation (Message creation)
        user_msg = ChatMessage(text=user_text, is_user=True)
        for p in files:
            mime, _ = mimetypes.guess_type(p)
            if mime and mime.startswith("image/"):
                b64, mt = _encode_image(p)
                user_msg.attachments.append(Attachment(path=p, mime=mt, b64=b64))
            else: user_msg.attachments.append(Attachment(path=p)) 
        
        new_messages = self.get_active_chat() + [user_msg]
        await EventBus.publish("state:messages_added", new_messages, user_msg)

        # 2. LLM Interaction (Generation happens in the UI layer but uses this state manager's logic/API)
        return user_msg, {"task": "generation"} # Placeholder return for now


# Global instance of the ChatStateManager will be initialized in app.py
chat_state_manager: ChatStateManager | None = None