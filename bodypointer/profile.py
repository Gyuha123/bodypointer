"""사용자 프로필 저장/복원 (FR-13). 저장 위치: %APPDATA%/BodyPointer

저장하는 것: 설정값, 캘리브레이션 범위 좌표뿐 (영상·스냅샷·랜드마크 시계열은 저장하지 않음, §6).
"""
import json
import os
import re
import shutil
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "BodyPointer"
PROFILE_DIR = APP_DIR / "profiles"
APP_STATE = APP_DIR / "app.json"


@dataclass
class Settings:
    part_id: str = "nose"
    favorites: list = field(default_factory=lambda: ["nose", "r_index", "r_shoulder"])
    mode: str = "absolute"           # absolute | relative
    filter_strength: float = 60      # 0~100
    sensitivity: float = 1.0         # 절대 모드
    rel_gain: float = 1.5            # 상대 모드
    accel: float = 0.5               # 상대 모드
    dwell_time: float = 1.0          # 0.5~3.0
    dwell_radius: float = 35         # px
    dwell_popup: bool = True         # Dwell 시 클릭 종류 메뉴 표시 (FR-31)
    click_type: str = "left"         # 팝업을 끈 경우 기본 동작
    edge_scroll: bool = False        # 화면 위/아래 가장자리 스크롤 (FR-33)
    camera_index: int = 0
    high_contrast: bool = False
    large_text: bool = True
    voice_alert: bool = True
    calibrations: dict = field(default_factory=dict)   # part_id -> [min_x, max_x, min_y, max_y]
    roi_box: list | None = None      # 사용자 지정 영역 [x, y, w, h] 정규화
    # 키보드 바
    kb_dwell_time: float = 0.8       # 키 위에서 머무는 시간
    learn_words: bool = True         # 입력한 단어를 자동완성에 학습
    # 개인화
    gestures: dict = field(default_factory=dict)        # 제스처 id -> 동작 id
    gesture_sensitivity: float = 1.0
    gesture_thresholds: dict = field(default_factory=dict)   # '내 표정으로 맞추기' 결과
    toolbar_collapsed: bool = False
    quick_actions: list = field(default_factory=list)   # 직접 고정한 빠른 실행
    auto_quick: bool = True          # 자주 쓴 기능을 툴바에 자동 추가
    dismissed_recs: list = field(default_factory=list)
    break_reminder: bool = False     # 40분마다 휴식 알림
    fatigue_alert: bool = True       # 움직임이 크게 줄면 휴식 제안

    @classmethod
    def from_dict(cls, d: dict) -> "Settings":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


def _safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip() or "기본"


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(path)


def app_state() -> dict:
    return _read_json(APP_STATE, {"consent": False, "last_profile": "기본", "wizard_done": False})


def save_app_state(state: dict):
    _write_json(APP_STATE, state)


def list_profiles() -> list[str]:
    if not PROFILE_DIR.exists():
        return ["기본"]
    names = sorted(p.stem for p in PROFILE_DIR.glob("*.json"))
    return names or ["기본"]


def load_profile(name: str) -> Settings:
    return Settings.from_dict(_read_json(PROFILE_DIR / f"{_safe(name)}.json", {}))


def save_profile(name: str, settings: Settings):
    _write_json(PROFILE_DIR / f"{_safe(name)}.json", asdict(settings))


def delete_all_data():
    """모든 기록을 한 번에 삭제 (§6)."""
    shutil.rmtree(APP_DIR, ignore_errors=True)
