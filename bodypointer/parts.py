"""추적 가능한 신체 부위 목록 (FR-10).

source:
  face - MediaPipe Face Landmarker (478점, 홍채 포함)
  pose - MediaPipe Pose Landmarker (33점)
  hand - MediaPipe Hand Landmarker (21점), side = "Left"/"Right"/None(아무 손)
  roi  - 사용자가 화면에서 지정한 영역 (FR-12, CSRT 추적)

points: 여러 랜드마크의 가중 평균을 기준점으로 쓴다. 하체처럼 한 점이 흔들리는 부위를 안정시키기 위함.
min_conf: 이 신뢰도(가시성) 미만이면 그 프레임은 버리고 잠시 직전 위치를 유지한다.
smooth: 0~1, 클수록 신뢰도가 낮은 프레임을 더 강하게 눌러 준다 (하체용).
full_model: 더 정확한 포즈 모델(pose_landmarker_full)을 쓴다.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Part:
    id: str
    name: str
    source: str
    points: tuple = ((0, 1.0),)
    side: str | None = None
    group: str = ""
    min_conf: float = 0.5
    smooth: float = 0.0
    full_model: bool = False

    @property
    def index(self) -> int:
        return self.points[0][0]


def _p(*idx_w):
    return tuple((i, w) for i, w in idx_w)


def _leg(id_, name, *idx_w):
    return Part(id_, name, "pose", _p(*idx_w), group="다리", min_conf=0.3, smooth=0.55, full_model=True)


PARTS: list[Part] = [
    # 얼굴
    Part("nose", "코끝", "face", _p((1, 1)), group="얼굴"),
    Part("between_eyes", "미간", "face", _p((168, 1)), group="얼굴"),
    Part("forehead", "이마", "face", _p((10, 1)), group="얼굴"),
    Part("chin", "턱끝", "face", _p((152, 1)), group="얼굴"),
    Part("upper_lip", "윗입술", "face", _p((13, 1)), group="얼굴"),
    Part("left_eye", "왼쪽 눈", "face", _p((473, 1)), group="얼굴"),
    Part("right_eye", "오른쪽 눈", "face", _p((468, 1)), group="얼굴"),
    # 손
    Part("r_index", "오른손 검지", "hand", _p((8, 1)), "Right", "손"),
    Part("l_index", "왼손 검지", "hand", _p((8, 1)), "Left", "손"),
    Part("r_thumb", "오른손 엄지", "hand", _p((4, 1)), "Right", "손"),
    Part("l_thumb", "왼손 엄지", "hand", _p((4, 1)), "Left", "손"),
    Part("r_palm", "오른손 손바닥", "hand", _p((0, 1), (5, 1), (9, 1), (17, 1)), "Right", "손"),
    Part("l_palm", "왼손 손바닥", "hand", _p((0, 1), (5, 1), (9, 1), (17, 1)), "Left", "손"),
    Part("any_index", "아무 손 검지", "hand", _p((8, 1)), None, "손"),
    # 상체
    Part("l_shoulder", "왼쪽 어깨", "pose", _p((11, 1)), group="상체"),
    Part("r_shoulder", "오른쪽 어깨", "pose", _p((12, 1)), group="상체"),
    Part("l_elbow", "왼쪽 팔꿈치", "pose", _p((13, 1)), group="상체", min_conf=0.4, smooth=0.3),
    Part("r_elbow", "오른쪽 팔꿈치", "pose", _p((14, 1)), group="상체", min_conf=0.4, smooth=0.3),
    Part("l_wrist", "왼쪽 손목", "pose", _p((15, 1)), group="상체", min_conf=0.4, smooth=0.3),
    Part("r_wrist", "오른쪽 손목", "pose", _p((16, 1)), group="상체", min_conf=0.4, smooth=0.3),
    # 다리: 여러 점을 묶어 흔들림을 줄인다
    _leg("pelvis", "골반 가운데", (23, 1), (24, 1)),
    _leg("l_hip", "왼쪽 엉덩이", (23, 3), (24, 1)),
    _leg("r_hip", "오른쪽 엉덩이", (24, 3), (23, 1)),
    _leg("l_knee", "왼쪽 무릎", (25, 1)),
    _leg("r_knee", "오른쪽 무릎", (26, 1)),
    _leg("l_ankle", "왼쪽 발목", (27, 2), (29, 1)),
    _leg("r_ankle", "오른쪽 발목", (28, 2), (30, 1)),
    _leg("l_foot", "왼발", (31, 2), (29, 1), (27, 1)),
    _leg("r_foot", "오른발", (32, 2), (30, 1), (28, 1)),
    # 사용자 지정
    Part("roi", "직접 지정한 영역", "roi", group="직접 지정"),
]

PARTS_BY_ID = {p.id: p for p in PARTS}
GROUP_ICONS = {"얼굴": "emoji", "손": "touch", "상체": "person", "다리": "person", "직접 지정": "select_all"}


def get_part(part_id: str) -> Part:
    return PARTS_BY_ID.get(part_id, PARTS_BY_ID["nose"])
