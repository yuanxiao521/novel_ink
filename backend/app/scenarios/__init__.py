"""场景包：信任/背叛微场景。"""
from app.errors import SceneNotFoundError
from app.scenarios.betrayal_night import betrayal_night

SCENARIOS = {"betrayal_night": betrayal_night}


def load_scenario(name: str):
    fn = SCENARIOS.get(name)
    if not fn:
        raise SceneNotFoundError(f"未知场景: {name}，可选 {list(SCENARIOS)}")
    return fn()