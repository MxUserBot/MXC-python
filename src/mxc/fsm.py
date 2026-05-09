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
        self._outgoing: set = set()

    def _get_key(self, event) -> str:
        return f"{event.room_id}:{event.sender}"

    def mark_outgoing(self, event_id: str) -> None:
        self._outgoing.add(event_id)

    def set_state(self, event, state_obj) -> None:
        key = self._get_key(event)
        s = state_obj.state if hasattr(state_obj, 'state') else state_obj
        if key not in self._states:
            self._states[key] = {"data": {}}
        self._states[key]["state"] = s

    def get_state(self, event, ignore_ids: set | None = None):
        eid = getattr(event, "event_id", None)
        if eid and eid in self._outgoing:
            return None
        if ignore_ids and eid in ignore_ids:
            return None
        return self._states.get(self._get_key(event), {}).get("state")

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

    async def set_state(self, state) -> None:
        self._manager.set_state(self._event, state)

    async def update_data(self, **kwargs) -> None:
        self._manager.update_data(self._event, **kwargs)

    async def get_data(self) -> dict:
        return self._manager.get_data(self._event)

    async def clear(self) -> None:
        self._manager.finish(self._event)
