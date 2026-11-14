"""MediaPipe 모델 파일을 models/ 에 한 번 내려받는다. 이후로는 인터넷 없이 동작 (NFR-06)."""
import urllib.request
from pathlib import Path

BASE = "https://storage.googleapis.com/mediapipe-models"
MODELS = {
    "face_landmarker.task": f"{BASE}/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
    "pose_landmarker_lite.task": f"{BASE}/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task",
    "pose_landmarker_full.task": f"{BASE}/pose_landmarker/pose_landmarker_full/float16/1/pose_landmarker_full.task",
    "hand_landmarker.task": f"{BASE}/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task",
}

if __name__ == "__main__":
    out = Path(__file__).resolve().parent / "models"
    out.mkdir(exist_ok=True)
    for name, url in MODELS.items():
        dest = out / name
        if dest.exists() and dest.stat().st_size > 0:
            print(f"있음: {name}")
            continue
        print(f"내려받는 중: {name}")
        urllib.request.urlretrieve(url, dest)
    print("완료")
