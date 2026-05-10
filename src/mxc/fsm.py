import time


class State:
    def __init__(self):
        self.state: str = ""


class _StatesGroupMeta(type):
    def __new__(mcs, name, bases, namespace):
        cls = super().__new__(mcs, name, bases, namespace)
        for key, val in namespace.items():
            if isinstance(val, State):
                val.state = f"{name}:{key}"
        return cls


class StatesGroup(metaclass=_StatesGroupMeta):
    pass


class FSM:
    def __init__(self):
        self._states = {}
        self._processed_events: set = set()

    def _get_key(self, event) -> str:
        return f"{event.room_id}:{event.sender}"

    def mark_processed(self, event) -> None:
        eid = getattr(event, "event_id", None)
        if eid:
            self._processed_events.add(eid)

    def set_state(self, event, state_obj, ttl: int = 0) -> None:
        key = self._get_key(event)
        s = state_obj.state if hasattr(state_obj, 'state') else state_obj
        if key not in self._states:
            self._states[key] = {"data": {}}
        self._states[key]["state"] = s
        if ttl > 0:
            self._states[key]["_expires_at"] = time.monotonic() + ttl
        else:
            self._states[key].pop("_expires_at", None)

    def get_state(self, event, ignore_ids: set | None = None):
        eid = getattr(event, "event_id", None)
        if eid and eid in self._processed_events:
            return None
        if ignore_ids and eid in ignore_ids:
            return None
        entry = self._states.get(self._get_key(event))
        if not entry:
            return None
        expires_at = entry.get("_expires_at")
        if expires_at is not None and time.monotonic() >= expires_at:
            self.finish(event)
            return None
        return entry.get("state")

    def get_data(self, event) -> dict:
        return self._states.get(self._get_key(event), {}).get("data", {})

    def update_data(self, event, **data) -> None:
        key = self._get_key(event)
        if key in self._states:
            self._states[key]["data"].update(data)

    def finish(self, event) -> None:
        self._states.pop(self._get_key(event), None)


class FSMContext:
    def __init__(self, manager: FSM, event):
        self._manager = manager
        self._event = event

    async def set_state(self, state, ttl: int = 0) -> None:
        self._manager.set_state(self._event, state, ttl=ttl)

    async def update_data(self, **kwargs) -> None:
        self._manager.update_data(self._event, **kwargs)

    async def get_data(self) -> dict:
        return self._manager.get_data(self._event)

    async def clear(self) -> None:
        self._manager.finish(self._event)
