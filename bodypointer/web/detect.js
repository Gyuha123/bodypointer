// MediaPipe 검출기 생성과 한 프레임 검출 (vision.js와 테스트에서 공용)
import { FilesetResolver, FaceLandmarker, PoseLandmarker, HandLandmarker } from "./vendor/vision_bundle.mjs";

const MODELS = {
  face: "/models/face_landmarker.task",
  pose: "/models/pose_landmarker_lite.task",
  poseFull: "/models/pose_landmarker_full.task",
  hand: "/models/hand_landmarker.task",
};
// 표정 제스처에 쓰는 블렌드셰이프
const BLEND_KEYS = ["jawOpen", "browInnerUp", "mouthSmileLeft", "mouthSmileRight", "eyeBlinkLeft", "eyeBlinkRight",
  "mouthPucker", "cheekPuff"];
const r4 = (v) => Math.round(v * 10000) / 10000;
let fileset = null;

function opts(model) {
  return { baseOptions: { modelAssetPath: MODELS[model], delegate: "CPU" }, runningMode: "VIDEO" };
}

function createPose(model) {
  // 하체는 가려지거나 화면 끝에 걸치기 쉬워 추적 유지 기준을 낮춘다
  return PoseLandmarker.createFromOptions(fileset, { ...opts(model), numPoses: 1,
    minPoseDetectionConfidence: 0.5, minPosePresenceConfidence: 0.4, minTrackingConfidence: 0.3 });
}

export async function createDetectors() {
  fileset = await FilesetResolver.forVisionTasks("./vendor/wasm");
  const [face, pose, hand] = await Promise.all([
    FaceLandmarker.createFromOptions(fileset, { ...opts("face"), numFaces: 1, outputFaceBlendshapes: true,
      minFaceDetectionConfidence: 0.5, minTrackingConfidence: 0.5 }),
    createPose("pose"),
    HandLandmarker.createFromOptions(fileset, { ...opts("hand"), numHands: 2,
      minHandDetectionConfidence: 0.5, minTrackingConfidence: 0.5 }),
  ]);
  return { face, pose, hand, poseFull: null, poseFullLoading: null };
}

// 더 정확한 포즈 모델은 하체 부위를 고를 때만 불러온다
export function ensureFullPose(det) {
  if (det.poseFull || det.poseFullLoading) return;
  det.poseFullLoading = createPose("poseFull").then((m) => { det.poseFull = m; });
}

// source: ImageBitmap 등. 결과 좌표는 거울 모드(x → 1-x).
export function detectAll(det, source, ts, sources, poseModel = "lite") {
  const out = { face: null, pose: null, hands: [], blend: null };
  if (sources.has("face")) {
    const r = det.face.detectForVideo(source, ts);
    if (r.faceLandmarks && r.faceLandmarks.length) {
      out.face = r.faceLandmarks[0].map((p) => [r4(1 - p.x), r4(p.y)]);
      const cats = r.faceBlendshapes && r.faceBlendshapes[0] ? r.faceBlendshapes[0].categories : [];
      const b = {};
      for (const c of cats) if (BLEND_KEYS.includes(c.categoryName)) b[c.categoryName] = r4(c.score);
      out.blend = b;
    }
  }
  if (sources.has("pose")) {
    let model = det.pose;
    if (poseModel === "full") {
      ensureFullPose(det);
      if (det.poseFull) model = det.poseFull;
    }
    const r = model.detectForVideo(source, ts);
    if (r.landmarks && r.landmarks.length)
      out.pose = r.landmarks[0].map((p) => {
        // 가시성과 존재 확률 중 낮은 쪽을 신뢰도로 쓴다. 화면 밖 좌표는 신뢰도를 깎는다.
        let c = Math.min(p.visibility ?? 1, p.presence ?? 1);
        if (p.x < -0.02 || p.x > 1.02 || p.y < -0.02 || p.y > 1.02) c *= 0.3;
        return [r4(1 - p.x), r4(p.y), r4(c)];
      });
  }
  if (sources.has("hand")) {
    const r = det.hand.detectForVideo(source, ts);
    const handed = r.handedness || r.handednesses || [];
    (r.landmarks || []).forEach((lms, i) => {
      // 비반전 카메라 영상 기준으로 실제 손의 좌우가 나온다 (MediaPipe 테스트 이미지 right_hands.jpg로 확인)
      const side = handed[i] && handed[i][0] ? handed[i][0].categoryName : "Right";
      out.hands.push([side, lms.map((p) => [r4(1 - p.x), r4(p.y)])]);
    });
  }
  return out;
}
