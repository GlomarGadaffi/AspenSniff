// aspen_sweep.ino
// Cold-start RF parameter discovery for goTenna Aspen Grove (902–928 MHz US ISM)
//
// Strategy:
//   1. Channel Activity Detection sweep across 902–928 MHz
//   2. On CAD hit: brute-force SF/BW probe combos with non-blocking Rx window
//   3. Log everything as JSON lines to Serial — pipe to file on host
//
// Decoding: we don't know the sync word, so most captures will CRC-fail.
// That's expected and useful. CRC failures still give us freq/SF/BW/RSSI/SNR —
// enough to narrow the search. A clean decode means we guessed the air interface.
//
// Host side: `python3 -m serial.tools.miniterm /dev/ttyUSB0 115200 | tee sweep.jsonl`
// Then: `grep '"e":"cad_hit"' sweep.jsonl | jq -r .freq | sort | uniq -c | sort -rn`

#include <RadioLib.h>

// LilyGo T3-S3-MVSRBoard — verify against your board's schematic
// These match the MVSRBOARD variant; T3-S3 without MVSR may differ
#define LORA_CS    7
#define LORA_DIO1  33
#define LORA_RST   8
#define LORA_BUSY  34

SX1268 radio = new Module(LORA_CS, LORA_DIO1, LORA_RST, LORA_BUSY);

// 902–928 MHz, 250 kHz steps for first pass
// Narrow to 125 kHz once we have CAD hotspots
const float FREQ_START = 902.0;
const float FREQ_END   = 928.0;
const float FREQ_STEP  = 0.25;

// SF/BW probe order — sorted by goTenna Mesh prior probability
// goTenna FCC filing (FCC ID 2AB7J-MESH01) hints at SF9-10, 125–250 kHz BW
// SF11/SF12 included as fallback — unlikely but possible for long-range links
struct AirIface {
  uint8_t sf;
  float   bw;      // kHz
  uint8_t cr;      // 4/N coding rate denominator
};

const AirIface PROBES[] = {
  { 10, 125.0, 5 },
  {  9, 125.0, 5 },
  { 10, 250.0, 5 },
  {  9, 250.0, 5 },
  {  8, 125.0, 5 },
  { 11, 125.0, 5 },
  {  7, 125.0, 5 },
  {  7, 250.0, 5 },
};
const int N_PROBES = sizeof(PROBES) / sizeof(PROBES[0]);

// Sync words to try per probe attempt
// 0x12 = LoRa private network (most common for proprietary mesh)
// 0x34 = LoRaWAN public (less likely for goTenna but worth checking)
const uint8_t SYNC_WORDS[] = { 0x12, 0x34 };
const int N_SYNC = sizeof(SYNC_WORDS) / sizeof(SYNC_WORDS[0]);

// CAD uses SF10/BW125 — widest detection window without knowing the air interface
// CAD fires on preamble energy, not on decoding — sync word is irrelevant here
#define CAD_SF  10
#define CAD_BW  125.0

// Rx dwell per probe: long enough to catch a packet if present, short enough to hop
// goTenna Mesh packets are ~100–300ms airtime at SF10/BW125 — 400ms gives headroom
#define RX_DWELL_MS 400

// CAD timeout: RadioLib scanChannel() blocks internally up to ~symbol time × 2
// At SF10/BW125 one symbol ≈ 10.24ms → CAD is fast even at fine granularity
// No explicit timeout needed — RadioLib returns RADIOLIB_CHANNEL_FREE or RADIOLIB_LORA_DETECTED

uint32_t sweepN = 0;

// ─── Logging ────────────────────────────────────────────────────────────────

void emitJson(const char* event, float freq, int sf, float bw, uint8_t cr,
              uint8_t syncWord, int rssi, float snr,
              bool crcOk, uint8_t* payload, int payloadLen) {
  Serial.print(F("{\"e\":\""));
  Serial.print(event);
  Serial.print(F("\",\"t\":"));
  Serial.print(millis());
  Serial.print(F(",\"sw\":"));
  Serial.print(sweepN);
  Serial.print(F(",\"freq\":"));
  Serial.print(freq, 3);
  if (sf > 0) {
    Serial.print(F(",\"sf\":"));   Serial.print(sf);
    Serial.print(F(",\"bw\":"));   Serial.print(bw, 1);
    Serial.print(F(",\"cr\":\"4/")); Serial.print(cr); Serial.print('"');
    Serial.print(F(",\"sync\":\"0x")); 
    if (syncWord < 0x10) Serial.print('0');
    Serial.print(syncWord, HEX); Serial.print('"');
  }
  if (rssi != 0) { Serial.print(F(",\"rssi\":")); Serial.print(rssi); }
  if (snr != 0)  { Serial.print(F(",\"snr\":"));  Serial.print(snr, 2); }
  Serial.print(F(",\"crc\":")); Serial.print(crcOk ? F("true") : F("false"));
  if (payloadLen > 0 && payload != nullptr) {
    Serial.print(F(",\"len\":")); Serial.print(payloadLen);
    Serial.print(F(",\"hex\":\""));
    for (int i = 0; i < payloadLen && i < 64; i++) {
      if (payload[i] < 0x10) Serial.print('0');
      Serial.print(payload[i], HEX);
    }
    Serial.print('"');
  }
  Serial.println('}');
}

// ─── CAD ────────────────────────────────────────────────────────────────────

bool cadCheck(float freq) {
  radio.setFrequency(freq);
  radio.setSpreadingFactor(CAD_SF);
  radio.setBandwidth(CAD_BW);
  radio.setCodingRate(5);
  // CAD doesn't decode — sync word is irrelevant, leave at whatever it was
  int state = radio.scanChannel();
  return (state == RADIOLIB_LORA_DETECTED);
}

// ─── Probe ──────────────────────────────────────────────────────────────────

void probeChannel(float freq) {
  uint8_t buf[256];

  for (int p = 0; p < N_PROBES; p++) {
    for (int s = 0; s < N_SYNC; s++) {
      radio.setFrequency(freq);
      radio.setSpreadingFactor(PROBES[p].sf);
      radio.setBandwidth(PROBES[p].bw);
      radio.setCodingRate(PROBES[p].cr);
      radio.setSyncWord(SYNC_WORDS[s]);
      // Disable CRC enforcement — we want CRC-fail captures too
      // CRC-fail still reveals SF/BW match via header decode success
      radio.setCRC(false);

      radio.startReceive();
      unsigned long t0 = millis();
      bool got = false;

      while ((millis() - t0) < RX_DWELL_MS) {
        if (radio.available()) {
          got = true;
          break;
        }
        delay(1);
      }

      radio.standby();

      int   rssi = (int)radio.getRSSI();
      float snr  = radio.getSNR();

      if (got) {
        int len   = radio.getPacketLength();
        int state = radio.readData(buf, len);
        bool crcOk = (state == RADIOLIB_ERR_NONE);

        emitJson(crcOk ? "rx_ok" : "rx_fail",
                 freq, PROBES[p].sf, PROBES[p].bw, PROBES[p].cr,
                 SYNC_WORDS[s], rssi, snr, crcOk,
                 buf, len);

        if (crcOk) {
          // Clean decode — we have the air interface. Stop probing this channel.
          // The host-side analysis can now lock these params for a targeted listener.
          return;
        }
        // CRC fail: header was decoded (SF/BW match) but payload was corrupt or
        // sync word was wrong. Still useful — narrows the parameter space.
        // Continue probing other SF/BW/sync combos.
      }
      // No packet in dwell window — channel went quiet or CAD was a false positive.
      // Don't log no-shows; it would drown the useful signal.
    }
  }
}

// ─── Setup / Loop ───────────────────────────────────────────────────────────

void setup() {
  Serial.begin(115200);
  while (!Serial) delay(10);

  // Halt on radio failure — partial init is worse than no init
  int state = radio.begin(902.0, CAD_BW, CAD_SF, 5, 0x12, 14);
  if (state != RADIOLIB_ERR_NONE) {
    Serial.print(F("{\"e\":\"fatal\",\"msg\":\"radio init\",\"code\":"));
    Serial.print(state);
    Serial.println('}');
    while (true) delay(1000);
  }

  // Power: 14 dBm is sufficient for passive listening; we're Rx-only in practice
  // but the library requires a Tx power at init. Not transmitting.

  Serial.println(F("{\"e\":\"ready\",\"target\":\"aspen_grove\","
                   "\"freq_start\":902.0,\"freq_end\":928.0,\"freq_step\":0.25,"
                   "\"cad_sf\":10,\"cad_bw\":125.0}"));
}

void loop() {
  for (float freq = FREQ_START; freq <= FREQ_END; freq += FREQ_STEP) {
    if (cadCheck(freq)) {
      // Log CAD hit with ambient RSSI before we disturb the config
      emitJson("cad_hit", freq, 0, 0, 0, 0,
               (int)radio.getRSSI(), 0, false, nullptr, 0);
      probeChannel(freq);
    }
  }

  sweepN++;
  Serial.print(F("{\"e\":\"sweep_done\",\"sw\":"));
  Serial.print(sweepN);
  Serial.print(F(",\"t\":"));
  Serial.print(millis());
  Serial.println('}');
}
