// BodyPointer 인식 엔진 (Qt WebEngine 안에서 실행)
// - 카메라 영상을 받아 MediaPipe(WASM, CPU)로 얼굴/포즈/손 랜드마크를 추출한다.
// - 좌표는 거울 모드(x → 1-x)로 바꿔 Python(QWebChannel)으로 보낸다.
// - 영상은 저장하거나 외부로 보내지 않는다. 미리보기·영역 추적이 필요할 때만 작은 JPEG를 같은 PC의 Python에 넘긴다.
import { createDetectors, detectAll } from "./detect.js";

const video = document.getElementById("video");

let bridge = null;
let cfg = { sources: ["face"], camera: 0, wantFrames: true, frameW: 320 };
let detectors = {};
let stream = null;
let cameraIndex = -1;
let opening = false;
let lastVideoTime = -1;
let lastTs = 0;
let frames = 0, fpsT0 = performance.now(), fps = 0;
let lowLight = false;

const frameCanvas = document.createElement("canvas");
const frameCtx = frameCanvas.getContext("2d", { willReadFrequently: false });
const lumaCanvas = document.createElement("canvas");
lumaCanvas.width = 32; lumaCanvas.height = 24;
const lumaCtx = lumaCanvas.getContext("2d", { willReadFrequently: true });

const status = (msg) => bridge && bridge.onStatus(msg);

async function init() {
  detectors = await createDetectors();
  status("모델 준비 완료");
}

async function openCamera(index) {
  if (opening) return;
  opening = true;
  try {
    if (stream) stream.getTracks().forEach((t) => t.stop());
    stream = null;
    let devices = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "videoinput");
    const constraints = { width: { ideal: 640 }, height: { ideal: 480 }, frameRate: { ideal: 30 } };
    if (devices[index] && devices[index].deviceId) constraints.deviceId = { exact: devices[index].deviceId };
    stream = await navigator.mediaDevices.getUserMedia({ video: constraints, audio: false });
    devices = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "videoinput");
    bridge.onCameras(JSON.stringify(devices.map((d, i) => d.label || `카메라 ${i}`)));
    const track = stream.getVideoTracks()[0];
    track.addEventListener("ended", () => {          // 카메라 분리 (NFR-07)
      status("카메라 연결이 끊겼습니다. 다시 연결을 시도합니다…");
      bridge.onResult(JSON.stringify({ cameraOk: false }));
      stream = null;
      cameraIndex = -1;
    });
    video.srcObject = stream;
    await video.play();
    cameraIndex = index;
    status("카메라 연결됨");
  } catch (e) {
    cameraIndex = -1;
    stream = null;
    status("카메라를 찾을 수 없습니다. 연결을 확인해주세요. 다시 연결을 시도합니다… (" + e.name + ")");
    bridge.onResult(JSON.stringify({ cameraOk: false }));
  } finally {
    opening = false;
  }
}

function measureLight() {
  lumaCtx.drawImage(video, 0, 0, 32, 24);
  const d = lumaCtx.getImageData(0, 0, 32, 24).data;
  let sum = 0;
  for (let i = 0; i < d.length; i += 4) sum += 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
  lowLight = sum / (d.length / 4) < 55;            // NFR-08 저조도 경고
}

function grabFrame() {
  const w = cfg.frameW, h = Math.round((w * video.videoHeight) / video.videoWidth) || Math.round(w * 0.75);
  if (frameCanvas.width !== w) { frameCanvas.width = w; frameCanvas.height = h; }
  frameCtx.setTransform(-1, 0, 0, 1, w, 0);         // 거울 모드
  frameCtx.drawImage(video, 0, 0, w, h);
  return frameCanvas.toDataURL("image/jpeg", 0.7);
}

async function step() {
  if (!stream || video.readyState < 2 || video.currentTime === lastVideoTime) return;
  lastVideoTime = video.currentTime;
  const ts = Math.max(performance.now(), lastTs + 1);
  lastTs = ts;
  let out = { face: null, pose: null, hands: [] };
  let bmp = null;
  try {
    // 숨김 페이지에서는 <video>를 직접 넘기는 것보다 ImageBitmap이 안정적이다
    bmp = await createImageBitmap(video);
    out = detectAll(detectors, bmp, ts, new Set(cfg.sources), cfg.poseModel || "lite");
  } catch (e) {
    status("인식 오류: " + e.message);
  } finally {
    if (bmp) bmp.close();
  }
  out.cameraOk = true;
  frames++;
  const now = performance.now();
  if (now - fpsT0 >= 1000) {
    fps = (frames * 1000) / (now - fpsT0);
    frames = 0; fpsT0 = now;
    measureLight();
  }
  out.fps = Math.round(fps * 10) / 10;
  out.lowLight = lowLight;
  if (cfg.wantFrames) out.jpg = grabFrame();
  bridge.onResult(JSON.stringify(out));
}

async function loop() {
  if (cameraIndex !== cfg.camera && !opening) {
    // 카메라 미연결/변경: 2초 간격으로 재시도
    if (!loop.nextTry || performance.now() > loop.nextTry) {
      loop.nextTry = performance.now() + 2000;
      openCamera(cfg.camera);
    }
  } else {
    await step();
  }
  setTimeout(loop, 4);
}

new QWebChannel(qt.webChannelTransport, async (channel) => {
  bridge = channel.objects.bridge;
  bridge.config.connect((s) => { cfg = { ...cfg, ...JSON.parse(s) }; });
  try {
    await init();
  } catch (e) {
    status("인식 모델을 불러오지 못했습니다: " + e.message);
    return;
  }
  bridge.ready();
  loop();
});
