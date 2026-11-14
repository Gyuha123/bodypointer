# 사용 라이브러리 라이선스 (NFR-11)

| 구성 요소 | 라이선스 | 배포 시 유의점 |
|---|---|---|
| MediaPipe Tasks Vision (웹, `bodypointer/web/vendor`) v1.2.3 | Apache 2.0 | 라이선스 고지 포함 |
| MediaPipe 모델 (`models/*.task`) | Apache 2.0 (모델 카드 확인 필요) | 각 모델 카드의 사용 조건 확인 |
| PySide6 / Qt 6 (QtWebEngine 포함) | LGPL v3 | 동적 링크 유지, 사용자가 Qt 라이브러리를 교체할 수 있어야 함. QtWebEngine은 Chromium 구성요소 고지 필요 |
| OpenCV (contrib) | Apache 2.0 | 라이선스 고지 포함 |
| NumPy | BSD-3-Clause | 라이선스 고지 포함 |

오픈소스로 배포하는 데 걸림돌은 없습니다. PyInstaller로 묶어 배포할 때는 Qt를 정적으로 링크하지 말고, LGPL 고지와 소스 입수 방법을 함께 제공해야 합니다.
