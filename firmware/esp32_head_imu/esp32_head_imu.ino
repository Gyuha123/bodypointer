// MPU6500 / MPU9250 / MPU9255 -> ESP32 -> 블루투스(SPP) 무선 + USB 시리얼로 자이로/가속도 전송 (200Hz)
// 머리(헤어밴드, 안경다리 등)에 센서를 붙여 PC의 MediaPipe 커서 보정에 사용
//
// [BodyPointer 연동판 v2.0] 바뀐 점
//   1) 자이로 보정값(바이어스)을 ESP32 내부 저장소(NVS)에 저장. 켜자마자 저장된 값으로 바로 동작
//      -> BodyPointer를 실행해 블루투스가 연결되는 즉시 커서를 움직일 수 있음
//   2) 블루투스가 연결될 때 강제로 다시 보정하지 않음 (머리에 쓴 채로 연결해도 바로 사용).
//      저장된 값이 없을 때(처음 한 번)만 보정. 스위치/'c' 명령으로는 언제든 다시 보정
//   3) 자동 미세 보정: 머리가 2초 동안 가만히 있으면 바이어스를 조금씩 맞춤 (커서가 흘러가는 것 방지)
//   4) 식별 패킷 type 4 (펌웨어 버전): 연결 직후와 'i' 명령 때 전송. PC가 이 장치를 바로 알아봄
//
// 블루투스: PC에서 "HeadMouse-IMU"와 페어링하면 Windows에 COM 포트가 생깁니다.
//           USB로 연결해도 똑같은 데이터가 나옵니다 (둘 다 동시에 출력).
//
// 배선 (I2C):
//   VCC -> 3.3V, GND -> GND, SDA -> GPIO25, SCL -> GPIO13
//   AD0 -> GND (주소 0x68), NCS -> 3.3V, FSYNC -> GND
//   SCL을 GPIO12에 연결하면 안 됨: 센서 모듈의 풀업 때문에 부팅 시 HIGH가 되어
//   플래시 전압이 1.8V로 잡히고 업로드·부팅이 실패함
//
// RGB LED (R 26, G 14, B 27, 각 220~330Ω): 연결 상태 표시
//   빨강 깜빡임 = 센서 오류, 노랑 빠른 깜빡임 = 자이로 보정 중 (가만히)
//   파랑 숨쉬기 = 블루투스 연결 대기, 초록 = 연결됨 (머리를 움직이면 밝아짐)
//
// 패킷 형식 (리틀 엔디언):
//   [0xA5][0x5B][type][len][payload x len][checksum = (type + len + payload) & 0xFF]
//   type 1 (IMU, 28B): uint32 t_us, float gx gy gz (deg/s, 바이어스 제거), float ax ay az (g)
//   type 2 (상태, 2B): uint8 WHO_AM_I, uint8 상태 (0 보정 중, 1 동작, 2 센서 오류)
//   type 3 (진단, 20B, 1초마다): uint32 샘플 시도 수, uint32 I2C 읽기 실패 수, uint32 보낸 IMU 패킷 수,
//                               uint32 블루투스로 보낸 바이트 수, uint32 플래그 (아래 sendDiag 참고)
//   type 4 (식별, 4B): 'B', 'P', 펌웨어 주 버전, 부 버전
//
// PC -> ESP32 명령 (USB, 블루투스 둘 다):
//   'c' 자이로 바이어스 다시 보정 (센서를 가만히 둔 상태에서)
//   't' 텍스트 모드: 20Hz로 "gx:..,gy:..,..." 출력 (아무 시리얼 모니터/플로터로 보기)
//   'b' 바이너리 모드 (기본, PC 프로그램용)
//   'i' 식별 패킷(type 4) 요청
//
// 보정 스위치 (래치 스위치, GPIO12 <-> GND):
//   누를 때마다(켜짐/꺼짐 둘 다) 0.5초 뒤 자이로 바이어스 보정. 흔들리면 가만해질 때까지 자동 재시도
//
// 보드 내장 LED (GPIO2)도 같이 표시:
//   느린 깜빡임 = 블루투스 연결 대기, 빠른 깜빡임 = 자이로 보정 중, 켜짐 = 연결됨
//
// 저전압 리셋 표시: 직전 리셋 원인이 저전압(brownout)이면 켜질 때 RGB가 보라색으로 3번 깜빡임
//   -> 배터리 전원이 블루투스 송신 순간 전류를 못 버티는 것. 진단 패킷에도 리셋 원인/횟수가 실림
//
// 배터리 구동을 위한 전류 절약: 블루투스 송신 출력 최대 0dBm,
//   IMU 패킷 4개(20ms)씩 묶어서 전송, RGB LED 밝기 제한
//
// 보드: ESP32 Dev Module (블루투스 클래식이 있는 ESP32 / ESP32-WROOM 전용, S3/C3는 불가)

#include <BluetoothSerial.h>
#include <Preferences.h>
#include <esp_bt.h>
#include <esp_system.h>

#if !defined(CONFIG_BT_ENABLED) || !defined(CONFIG_BLUEDROID_ENABLED)
#error "블루투스 클래식이 꺼져 있습니다. 보드를 ESP32 Dev Module로 선택하세요."
#endif

#define BT_NAME "HeadMouse-IMU"
#define STATUS_LED 2             // 보드 내장 LED. 없으면 -1

// 전류 절약 (배터리 구동)
#define CPU_MHZ 240                      // 160으로 낮추면 블루투스 연결 중 I2C 읽기 실패가 2% -> 13%로 늘어서 240 유지
#define BT_TX_MIN ESP_PWR_LVL_N3         // 블루투스 송신 출력 범위 (배터리 실측):
#define BT_TX_MAX ESP_PWR_LVL_P3         //   최대 0dBm -> 송신 밀림·워치독 리셋, 최대 +9dBm -> 저전압 리셋 12회. 중간값
#define IMU_BATCH 8                      // IMU 패킷 몇 개씩 모아 보낼지 (8 = 40ms마다 한 번, 송신 횟수 줄이기)
#define LED_MAX_BRIGHTNESS 0.35f         // RGB LED 최대 밝기 (0~1)

// RGB LED
#define LED_R 26
#define LED_G 14
#define LED_B 27
#define LED_COMMON_ANODE 0       // 공통 애노드(공통 다리가 3.3V)면 1, 공통 캐소드(GND)면 0
#define LED_PWM_FREQ 5000
#define LED_PWM_BITS 8

#define I2C_SDA 25
#define I2C_SCL 13

// 래치 스위치 -> GND (내부 풀업 사용). GPIO12는 부팅 때 HIGH면 안 되지만,
// GND로만 연결되는 스위치는 HIGH를 만들지 않으므로 안전. 절대 3.3V 쪽에 연결하지 말 것
#define CALIB_SW 12
#define CALIB_SW_DELAY_MS 500    // 스위치를 누른 손이 떨어질 때까지 기다림
#define MPU_ADDR 0x68
#define SERIAL_BAUD 921600
#define SAMPLE_US 5000           // 200Hz
#define TEXT_EVERY 10            // 텍스트 모드: 10샘플마다 1줄 (20Hz)

// 레지스터
#define REG_SMPLRT_DIV    0x19
#define REG_CONFIG        0x1A
#define REG_GYRO_CONFIG   0x1B
#define REG_ACCEL_CONFIG  0x1C
#define REG_ACCEL_CONFIG2 0x1D
#define REG_ACCEL_XOUT_H  0x3B
#define REG_PWR_MGMT_1    0x6B
#define REG_PWR_MGMT_2    0x6C
#define REG_WHO_AM_I      0x75

#define GYRO_LSB_PER_DPS 65.5f   // ±500 dps
#define ACCEL_LSB_PER_G  8192.0f // ±4 g

#define CALIB_SAMPLES 400        // 2초
#define CALIB_MAX_STD 1.5f       // 이보다 흔들리면 다시 보정 (deg/s)

// BodyPointer 연동판
#define FW_VERSION_MAJOR 2
#define FW_VERSION_MINOR 0
#define AUTO_SAMPLES 400         // 자동 미세 보정 구간 (2초)
#define AUTO_MAX_STD 0.4f        // 이 정도로 고요할 때만 (deg/s, 정지 노이즈는 약 0.06)
#define AUTO_MAX_OFFSET 1.0f     // 바이어스와 차이가 이보다 작을 때만 (천천히 돌리는 움직임을 바이어스로 착각하지 않게)
#define AUTO_GAIN 0.5f           // 한 번에 차이의 절반만 반영
#define BIAS_SAVE_MIN_MS 600000  // 자동 보정 결과 저장은 10분에 한 번까지 (플래시 수명)

enum State : uint8_t { STATE_CALIBRATING = 0, STATE_RUNNING = 1, STATE_ERROR = 2 };

BluetoothSerial SerialBT;
Preferences prefs;

static uint8_t whoAmI = 0;
static State state = STATE_CALIBRATING;
static float bias[3] = {0, 0, 0};
static bool haveBias = false;            // 쓸 수 있는 보정값이 있는지 (저장된 값 또는 이번에 보정한 값)
static uint32_t lastBiasSaveMs = 0;
static bool textMode = false;
static bool btConnected = false;
static uint32_t sampleCount = 0;

// 보정 누적값
static int calibCount = 0;
static double calibSum[3], calibSq[3];

static uint32_t nextSampleUs = 0;
static uint32_t lastStatusMs = 0;

void startCalibration();

// 리셋 원인 (저전압 진단). RTC 메모리는 리셋돼도 남고 전원이 완전히 꺼지면 사라짐
static esp_reset_reason_t resetReason = ESP_RST_UNKNOWN;
static RTC_NOINIT_ATTR uint32_t rtcMagic;
static RTC_NOINIT_ATTR uint32_t brownoutCount;

// ================= 출력 (USB + 블루투스) =================

// 무선 송신 횟수를 줄이려고 모아서 한 번에 보냄
static uint8_t txBuf[512];
static size_t txLen = 0;
static uint32_t diagBtBytes = 0;

static void flushOutput() {
  if (txLen == 0) return;
  Serial.write(txBuf, txLen);
  if (btConnected) diagBtBytes += SerialBT.write(txBuf, txLen);
  txLen = 0;
}

static void output(const uint8_t *buf, size_t len) {
  if (txLen + len > sizeof(txBuf)) flushOutput();
  memcpy(txBuf + txLen, buf, len);
  txLen += len;
}

static void sendPacket(uint8_t type, const uint8_t *payload, uint8_t len) {
  if (textMode) return;
  uint8_t buf[4 + 32 + 1];
  buf[0] = 0xA5;
  buf[1] = 0x5B;
  buf[2] = type;
  buf[3] = len;
  uint8_t sum = type + len;
  for (uint8_t i = 0; i < len; i++) {
    buf[4 + i] = payload[i];
    sum += payload[i];
  }
  buf[4 + len] = sum;
  output(buf, 5 + len);

  // IMU 패킷은 IMU_BATCH개 모일 때까지 모으고, 상태/진단 패킷은 바로 보냄
  static int batched = 0;
  if (type != 1 || ++batched >= IMU_BATCH) {
    flushOutput();
    batched = 0;
  }
}

static void sendStatus() {
  if (textMode) {
    static const char *names[] = {"calibrating", "running", "sensor error"};
    char line[64];
    int n = snprintf(line, sizeof(line), "# whoami=0x%02X state=%s reset=%d brownouts=%lu\n", whoAmI,
                     names[state], (int)resetReason, (unsigned long)brownoutCount);
    output((const uint8_t *)line, n);
    flushOutput();
    return;
  }
  uint8_t p[2] = {whoAmI, (uint8_t)state};
  sendPacket(2, p, 2);
}

// 진단 (type 3, 1초마다): uint32 샘플 시도 수, uint32 I2C 읽기 실패 수, uint32 보낸 IMU 패킷 수,
//   uint32 블루투스로 보낸 바이트 수,
//   uint32 플래그: bit0 블루투스 연결됨, bit8~15 직전 리셋 원인(esp_reset_reason, 9 = 저전압),
//                  bit16~31 전원을 켠 뒤 저전압 리셋 횟수
static uint32_t diagSlots = 0, diagReadFails = 0, diagSent = 0;

static void sendDiag() {
  uint8_t p[20];
  uint32_t bo = brownoutCount > 0xFFFF ? 0xFFFF : brownoutCount;
  uint32_t flags = (btConnected ? 1 : 0) | (((uint32_t)resetReason & 0xFF) << 8) | (bo << 16);
  memcpy(p, &diagSlots, 4);
  memcpy(p + 4, &diagReadFails, 4);
  memcpy(p + 8, &diagSent, 4);
  memcpy(p + 12, &diagBtBytes, 4);
  memcpy(p + 16, &flags, 4);
  sendPacket(3, p, 20);
  diagSlots = diagReadFails = diagSent = diagBtBytes = 0;
}

// 식별 (type 4): PC가 이 장치를 바로 알아보게
static void sendInfo() {
  uint8_t p[4] = {'B', 'P', FW_VERSION_MAJOR, FW_VERSION_MINOR};
  sendPacket(4, p, 4);
}

// ---- 보정값 저장 (NVS) ----
static bool loadBias() {
  prefs.begin("headmouse", true);
  bool ok = prefs.getUChar("ok", 0) == 1 && prefs.getBytes("bias", bias, sizeof(bias)) == sizeof(bias);
  prefs.end();
  if (ok) {
    for (int i = 0; i < 3; i++) {
      if (!isfinite(bias[i]) || fabsf(bias[i]) > 20.0f) ok = false;   // 이상한 값이면 버림
    }
  }
  if (!ok) bias[0] = bias[1] = bias[2] = 0;
  return ok;
}

static void saveBias() {
  prefs.begin("headmouse", false);
  prefs.putBytes("bias", bias, sizeof(bias));
  prefs.putUChar("ok", 1);
  prefs.end();
  lastBiasSaveMs = millis();
}

static void handleCommand(int c) {
  if (c == 'c' && state != STATE_ERROR) {
    startCalibration();
  } else if (c == 't') {
    textMode = true;
  } else if (c == 'b') {
    textMode = false;
  } else if (c == 'i') {
    sendInfo();
    sendStatus();
  }
}

// ================= MPU =================

// ---- 소프트웨어(비트뱅) I2C ----
// Arduino 3.x의 Wire 드라이버는 블루투스가 연결되는 순간 전송 중이면 영영 돌아오지 않는 경우가 있어서
// (ESP32 전체가 멈춤) 핀을 직접 움직이는 방식으로 대체. 약 200kHz, 14바이트 읽기 약 1ms
#define I2C_HALF_US 2

static inline void sdaRelease() { digitalWrite(I2C_SDA, HIGH); }   // 오픈 드레인: HIGH = 놓기
static inline void sdaLow() { digitalWrite(I2C_SDA, LOW); }
static inline void sclLow() { digitalWrite(I2C_SCL, LOW); }
static inline void i2cWait() { delayMicroseconds(I2C_HALF_US); }

static inline void sclRelease() {
  digitalWrite(I2C_SCL, HIGH);
  for (int i = 0; i < 100 && digitalRead(I2C_SCL) == LOW; i++) delayMicroseconds(1);   // 클럭 스트레칭 대비
}

static void i2cStart() {
  sdaRelease();
  sclRelease();
  i2cWait();
  sdaLow();
  i2cWait();
  sclLow();
  i2cWait();
}

static void i2cStop() {
  sdaLow();
  i2cWait();
  sclRelease();
  i2cWait();
  sdaRelease();
  i2cWait();
}

static bool i2cWriteByte(uint8_t b) {
  for (int i = 7; i >= 0; i--) {
    if (b & (1 << i)) sdaRelease();
    else sdaLow();
    i2cWait();
    sclRelease();
    i2cWait();
    sclLow();
  }
  sdaRelease();                       // ACK 받기
  i2cWait();
  sclRelease();
  i2cWait();
  bool ack = digitalRead(I2C_SDA) == LOW;
  sclLow();
  i2cWait();
  return ack;
}

static uint8_t i2cReadByte(bool ack) {
  uint8_t b = 0;
  sdaRelease();
  for (int i = 0; i < 8; i++) {
    i2cWait();
    sclRelease();
    i2cWait();
    b = (b << 1) | (digitalRead(I2C_SDA) ? 1 : 0);
    sclLow();
  }
  if (ack) sdaLow();                  // 더 읽을 거면 ACK, 마지막이면 NACK
  else sdaRelease();
  i2cWait();
  sclRelease();
  i2cWait();
  sclLow();
  sdaRelease();
  i2cWait();
  return b;
}

static bool writeReg(uint8_t reg, uint8_t val) {
  i2cStart();
  bool ok = i2cWriteByte(MPU_ADDR << 1) && i2cWriteByte(reg) && i2cWriteByte(val);
  i2cStop();
  return ok;
}

static bool readRegs(uint8_t reg, uint8_t *buf, uint8_t len) {
  i2cStart();
  if (!i2cWriteByte(MPU_ADDR << 1) || !i2cWriteByte(reg)) {
    i2cStop();
    return false;
  }
  i2cStart();                         // 반복 시작
  if (!i2cWriteByte((MPU_ADDR << 1) | 1)) {
    i2cStop();
    return false;
  }
  for (uint8_t i = 0; i < len; i++) buf[i] = i2cReadByte(i + 1 < len);
  i2cStop();
  return true;
}

// ESP32가 I2C 전송 도중에 리셋되면(업로드, USB 재연결) 센서가 SDA를 LOW로 잡고 있음.
// SCL을 몇 번 흔들어 센서가 놓게 한 뒤 STOP
static void startI2C() {
  pinMode(I2C_SDA, OUTPUT_OPEN_DRAIN | PULLUP);
  pinMode(I2C_SCL, OUTPUT_OPEN_DRAIN | PULLUP);
  sdaRelease();
  sclRelease();
  delayMicroseconds(5);
  for (int i = 0; i < 9 && digitalRead(I2C_SDA) == LOW; i++) {
    sclLow();
    delayMicroseconds(5);
    sclRelease();
    delayMicroseconds(5);
  }
  i2cStop();
}

static bool setupMpu() {
  bool found = false;
  for (int i = 0; i < 5 && !found; i++) {
    found = readRegs(REG_WHO_AM_I, &whoAmI, 1);
    if (!found) delay(20);
  }
  if (!found) return false;
  // 0x70 MPU6500, 0x71 MPU9250, 0x73 MPU9255 (그 외 호환 칩도 일단 진행)
  writeReg(REG_PWR_MGMT_1, 0x80);    // 리셋
  delay(100);
  writeReg(REG_PWR_MGMT_1, 0x01);    // 자이로 PLL 클럭
  writeReg(REG_PWR_MGMT_2, 0x00);    // 모든 축 켜기
  writeReg(REG_CONFIG, 0x03);        // 자이로 DLPF 41Hz
  writeReg(REG_SMPLRT_DIV, 4);       // 1kHz / (1+4) = 200Hz
  writeReg(REG_GYRO_CONFIG, 0x08);   // ±500 dps
  writeReg(REG_ACCEL_CONFIG, 0x08);  // ±4 g
  writeReg(REG_ACCEL_CONFIG2, 0x03); // 가속도 DLPF 41Hz
  delay(50);
  return true;
}

void startCalibration() {
  state = STATE_CALIBRATING;
  calibCount = 0;
  for (int i = 0; i < 3; i++) calibSum[i] = calibSq[i] = 0;
  sendStatus();
}

// 정지 상태에서 자이로 평균 = 바이어스. 흔들렸으면 처음부터 다시
static void feedCalibration(const float g[3]) {
  for (int i = 0; i < 3; i++) {
    calibSum[i] += g[i];
    calibSq[i] += (double)g[i] * g[i];
  }
  if (++calibCount < CALIB_SAMPLES) return;

  bool still = true;
  float mean[3];
  for (int i = 0; i < 3; i++) {
    mean[i] = calibSum[i] / calibCount;
    float var = calibSq[i] / calibCount - (double)mean[i] * mean[i];
    if (var > CALIB_MAX_STD * CALIB_MAX_STD) still = false;
  }
  if (!still) {
    startCalibration();
    return;
  }
  for (int i = 0; i < 3; i++) bias[i] = mean[i];
  haveBias = true;
  saveBias();                         // 다음에 켤 때 바로 쓰도록 저장
  state = STATE_RUNNING;
  sendStatus();
}

// 자동 미세 보정: 동작 중 머리가 2초 동안 아주 고요하면 바이어스를 조금 맞춘다 (온도 변화 등으로 생기는 흐름 방지)
static int autoCount = 0;
static double autoSum[3], autoSq[3];

static void feedAutoCalibration(const float g[3]) {
  for (int i = 0; i < 3; i++) {
    autoSum[i] += g[i];
    autoSq[i] += (double)g[i] * g[i];
  }
  if (++autoCount < AUTO_SAMPLES) return;
  bool quiet = true;
  float mean[3];
  for (int i = 0; i < 3; i++) {
    mean[i] = autoSum[i] / autoCount;
    float var = autoSq[i] / autoCount - (double)mean[i] * mean[i];
    if (var > AUTO_MAX_STD * AUTO_MAX_STD || fabsf(mean[i] - bias[i]) > AUTO_MAX_OFFSET) quiet = false;
  }
  autoCount = 0;
  for (int i = 0; i < 3; i++) autoSum[i] = autoSq[i] = 0;
  if (!quiet) return;
  for (int i = 0; i < 3; i++) bias[i] += AUTO_GAIN * (mean[i] - bias[i]);
  if (millis() - lastBiasSaveMs >= BIAS_SAVE_MIN_MS) saveBias();
}

// ================= 상태 LED =================

static float motionLevel = 0.0f;         // 0~1, 머리 회전 속도 (초록 밝기용)

static void rgbAttach(uint8_t pin, uint8_t ch) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcAttach(pin, LED_PWM_FREQ, LED_PWM_BITS);
#else
  ledcSetup(ch, LED_PWM_FREQ, LED_PWM_BITS);
  ledcAttachPin(pin, ch);
#endif
}

static void rgbWrite(uint8_t pin, uint8_t ch, float v) {
  if (v < 0.0f) v = 0.0f;
  if (v > 1.0f) v = 1.0f;
  v *= LED_MAX_BRIGHTNESS;            // 전류 절약
  int duty = (int)(v * v * 255.0f);   // 감마 보정
  if (LED_COMMON_ANODE) duty = 255 - duty;
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcWrite(pin, duty);
#else
  ledcWrite(ch, duty);
#endif
}

static void setRgb(float r, float g, float b) {
  rgbWrite(LED_R, 0, r);
  rgbWrite(LED_G, 1, g);
  rgbWrite(LED_B, 2, b);
}

static void setupLeds() {
  if (STATUS_LED >= 0) pinMode(STATUS_LED, OUTPUT);
  rgbAttach(LED_R, 0);
  rgbAttach(LED_G, 1);
  rgbAttach(LED_B, 2);
  setRgb(0, 0, 0);
}

// 20ms마다 갱신
static void updateLed() {
  static uint32_t lastMs = 0;
  uint32_t ms = millis();
  if (ms - lastMs < 20) return;
  lastMs = ms;

  bool on;
  if (state == STATE_ERROR) {
    on = (ms / 250) % 2;
    setRgb(on ? 1.0f : 0.0f, 0.0f, 0.0f);                     // 빨강 깜빡임
  } else if (state == STATE_CALIBRATING) {
    on = (ms / 100) % 2;
    setRgb(on ? 1.0f : 0.0f, on ? 0.6f : 0.0f, 0.0f);         // 노랑 빠른 깜빡임
  } else if (!btConnected) {
    on = (ms / 500) % 2;
    float breath = 0.15f + 0.6f * (0.5f + 0.5f * sinf(ms * 0.003f));
    setRgb(0.0f, 0.0f, breath);                               // 파랑 숨쉬기
  } else {
    on = true;
    setRgb(0.0f, 0.3f + 0.7f * motionLevel, 0.0f);            // 초록, 움직이면 밝아짐
  }
  if (STATUS_LED >= 0) digitalWrite(STATUS_LED, on ? HIGH : LOW);
}

// ================= 보정 스위치 =================

// 래치 스위치는 눌린 상태로 남으므로, 상태가 바뀔 때마다(켜짐/꺼짐 모두) 보정
static void pollCalibSwitch() {
  static bool lastRaw = HIGH;
  static bool stable = HIGH;
  static bool initialized = false;
  static uint32_t changedAt = 0;
  static uint32_t calibAt = 0;

  uint32_t ms = millis();
  bool raw = digitalRead(CALIB_SW);
  if (!initialized) {                 // 부팅 때 스위치 위치는 무시
    lastRaw = stable = raw;
    initialized = true;
  }
  if (raw != lastRaw) {
    lastRaw = raw;
    changedAt = ms;
  } else if (raw != stable && ms - changedAt >= 30) {   // 30ms 디바운스
    stable = raw;
    calibAt = ms + CALIB_SW_DELAY_MS;
  }
  if (calibAt && (int32_t)(ms - calibAt) >= 0) {
    calibAt = 0;
    if (state != STATE_ERROR) startCalibration();
  }
}

// ================= 메인 =================

void setup() {
  setCpuFrequencyMhz(CPU_MHZ);

  // 리셋 원인 기록 (전원을 새로 켰으면 저전압 횟수 초기화)
  resetReason = esp_reset_reason();
  if (rtcMagic != 0xB0B0CAFE || resetReason == ESP_RST_POWERON) {
    rtcMagic = 0xB0B0CAFE;
    brownoutCount = 0;
  }
  if (resetReason == ESP_RST_BROWNOUT) brownoutCount++;

  Serial.begin(SERIAL_BAUD);
  setupLeds();
  if (resetReason == ESP_RST_BROWNOUT) {   // 저전압 리셋이었으면 보라색 3번
    for (int i = 0; i < 3; i++) {
      setRgb(1.0f, 0.0f, 1.0f);
      delay(250);
      setRgb(0.0f, 0.0f, 0.0f);
      delay(250);
    }
  }

  SerialBT.begin(BT_NAME);
  esp_bredr_tx_power_set(BT_TX_MIN, BT_TX_MAX);
  pinMode(CALIB_SW, INPUT_PULLUP);

  startI2C();

  if (!setupMpu()) {
    state = STATE_ERROR;
  } else if (loadBias()) {
    haveBias = true;                  // 저장된 보정값으로 바로 동작 (보정 기다림 없음)
    state = STATE_RUNNING;
    sendStatus();
  } else {
    startCalibration();               // 처음 한 번만
  }
  nextSampleUs = micros();
}

void loop() {
  // 블루투스 연결/해제 감지 (BodyPointer가 COM 포트를 열면 연결됨)
  // 연결 직후 바로 쓰면 SerialBT.write가 영영 멈추는 경우가 있어서, 0.5초 뒤부터 보냄
  // v2: 연결될 때 강제로 다시 보정하지 않음 -> 머리에 쓴 채로 연결해도 바로 커서 사용.
  //     보정값이 아직 없을 때(처음 한 번)만 보정
  static uint32_t btSeenAt = 0;
  if (!SerialBT.hasClient()) {
    btSeenAt = 0;
    btConnected = false;
  } else if (!btConnected) {
    if (btSeenAt == 0) {
      btSeenAt = millis() | 1;
    } else if (millis() - btSeenAt >= 500) {
      btConnected = true;
      sendInfo();                                   // PC가 바로 알아보도록 식별 + 현재 상태
      if (state != STATE_ERROR && !haveBias) startCalibration();
      else sendStatus();
    }
  }

  while (Serial.available()) handleCommand(Serial.read());
  while (SerialBT.available()) handleCommand(SerialBT.read());
  pollCalibSwitch();
  updateLed();

  uint32_t nowMs = millis();
  if (nowMs - lastStatusMs >= 1000) {
    lastStatusMs = nowMs;
    if (state == STATE_ERROR) {                 // 센서 재연결 시도
      startI2C();
      if (setupMpu()) startCalibration();
    }
    sendStatus();
    sendDiag();
  }

  if ((int32_t)(micros() - nextSampleUs) < 0) return;
  nextSampleUs += SAMPLE_US;
  if ((int32_t)(micros() - nextSampleUs) > SAMPLE_US * 4) nextSampleUs = micros();   // 밀리면 따라잡지 않음

  if (state == STATE_ERROR) return;

  uint8_t raw[14];
  static int readFails = 0;
  diagSlots++;
  // 가끔 한 번씩 실패하는 읽기는 바로 다시 시도 (Serial.setTxBufferSize를 쓰면 실패가 크게 늘어서 쓰지 않음)
  if (!readRegs(REG_ACCEL_XOUT_H, raw, 14) && !readRegs(REG_ACCEL_XOUT_H, raw, 14)) {
    diagReadFails++;
    if (++readFails >= 40) {                    // 0.2초 연속 실패해야 오류로 판단
      readFails = 0;
      state = STATE_ERROR;
      sendStatus();
    }
    return;
  }
  readFails = 0;

  float a[3], g[3];
  for (int i = 0; i < 3; i++) {
    int16_t av = (int16_t)((raw[i * 2] << 8) | raw[i * 2 + 1]);
    int16_t gv = (int16_t)((raw[8 + i * 2] << 8) | raw[8 + i * 2 + 1]);
    a[i] = av / ACCEL_LSB_PER_G;
    g[i] = gv / GYRO_LSB_PER_DPS;
  }

  if (state == STATE_CALIBRATING) {
    feedCalibration(g);
    return;
  }

  feedAutoCalibration(g);
  float gc[3] = {g[0] - bias[0], g[1] - bias[1], g[2] - bias[2]};
  sampleCount++;

  // 머리 회전 속도 -> LED 밝기 (100 deg/s에서 최대, 천천히 감소)
  float speed = sqrtf(gc[0] * gc[0] + gc[1] * gc[1] + gc[2] * gc[2]) / 100.0f;
  if (speed > 1.0f) speed = 1.0f;
  motionLevel = (speed > motionLevel) ? speed : motionLevel * 0.97f;

  if (textMode) {
    if (sampleCount % TEXT_EVERY == 0) {
      char line[128];
      int n = snprintf(line, sizeof(line), "gx:%.2f,gy:%.2f,gz:%.2f,ax:%.3f,ay:%.3f,az:%.3f\n",
                       gc[0], gc[1], gc[2], a[0], a[1], a[2]);
      output((const uint8_t *)line, n);
      flushOutput();
    }
    return;
  }

  uint8_t p[28];
  uint32_t t = micros();
  memcpy(p, &t, 4);
  for (int i = 0; i < 3; i++) {
    memcpy(p + 4 + i * 4, &gc[i], 4);
    memcpy(p + 16 + i * 4, &a[i], 4);
  }
  sendPacket(1, p, 28);
  diagSent++;
}
