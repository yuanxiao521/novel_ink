"""场景包：信任/背叛微场景。"""
from app.scenarios.betrayal_night import betrayal_night

SCENARIOS = {"betrayal_night": betrayal_night}


def load_scenario(name: str):
    fn = SCENARIOS.get(name)
    if not fn:
        raise KeyError(f"未知场景: {name}，可选 {list(SCENARIOS)}")
    return fn()