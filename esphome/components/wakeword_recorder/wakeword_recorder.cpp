#include "wakeword_recorder.h"

#include "esphome/core/application.h"
#include "esphome/core/log.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstring>

#ifdef USE_ESP_IDF
#include <esp_crt_bundle.h>
#include <esp_http_client.h>
#endif

namespace esphome::wakeword_recorder {

static const char *const TAG = "wakeword_recorder";
static constexpr uint32_t OUTPUT_SAMPLE_RATE = 16000;

// Speech detection from the level. Deliberately simple: it only tells *that*
// someone speaks and when they stop, not what was said; that is up to the
// speech recognition in Home Assistant.
//
// Decided on an **envelope**, not the momentary level: fast up, slow down.
// Without it the level drops to the noise floor in every pause between
// syllables, detection flickers and an end-of-speech rule fires mid-sentence
// (measured: cut after 2.9 s of 3.7 s of speech).
static constexpr float ENVELOPE_RELEASE_S = 0.15f;
// Factor 2.0 is about 6 dB above the noise floor. Measured on a codec with
// automatic level control: silence at ratio 1.1 to 1.2, speech at 2.0 to 2.2.
static constexpr float SPEECH_FACTOR = 2.0f;
// Absolute floor against digital silence: otherwise a noise floor near zero
// would turn every click into speech.
static constexpr float SPEECH_FLOOR_MIN = 60.0f;
// The level must stay up this long to count as speech.
static constexpr uint32_t SPEECH_MIN_MS = 150;

void WakewordRecorder::setup() {
  const auto info = this->microphone_source_->get_audio_stream_info();
  this->source_rate_ = info.get_sample_rate();
  if (this->source_rate_ < OUTPUT_SAMPLE_RATE || this->source_rate_ % OUTPUT_SAMPLE_RATE != 0) {
    ESP_LOGE(TAG, "Unsupported microphone sample rate: %u Hz", this->source_rate_);
    this->mark_failed();
    return;
  }
  this->decimation_ = this->source_rate_ / OUTPUT_SAMPLE_RATE;
  this->microphone_source_->add_data_callback(
      [this](const std::vector<uint8_t> &data) { this->receive_audio_(data); });
}

void WakewordRecorder::dump_config() {
  ESP_LOGCONFIG(TAG, "Wakeword recorder:");
  ESP_LOGCONFIG(TAG, "  Device: %s", this->device_.c_str());
  ESP_LOGCONFIG(TAG, "  Source sample rate: %u Hz", this->source_rate_);
  ESP_LOGCONFIG(TAG, "  Output sample rate: %u Hz", OUTPUT_SAMPLE_RATE);
  ESP_LOGCONFIG(TAG, "  Maximum duration: %u ms", this->max_duration_ms_);
  ESP_LOGCONFIG(TAG, "  Upload URL: %s", this->url_.c_str());
  ESP_LOGCONFIG(TAG, "  Can hold microphone: %s", YESNO(this->hold_source_ != nullptr));
}

void WakewordRecorder::hold_microphone() {
  if (this->hold_source_ == nullptr || this->holding_)
    return;
  this->hold_source_->start();
  this->holding_ = true;
  ESP_LOGI(TAG, "Microphone held by recorder");
}

void WakewordRecorder::release_microphone() {
  if (this->hold_source_ == nullptr || !this->holding_)
    return;
  this->hold_source_->stop();
  this->holding_ = false;
  ESP_LOGI(TAG, "Microphone released by recorder");
}

float WakewordRecorder::level() {
  LockGuard guard{this->mutex_};
  return this->level_;
}

float WakewordRecorder::envelope() {
  LockGuard guard{this->mutex_};
  return this->envelope_;
}

float WakewordRecorder::noise_floor() {
  LockGuard guard{this->mutex_};
  return this->noise_floor_;
}

bool WakewordRecorder::speech_started() {
  LockGuard guard{this->mutex_};
  return this->speech_started_;
}

uint32_t WakewordRecorder::ms_since_speech() {
  LockGuard guard{this->mutex_};
  if (this->last_speech_ms_ == 0)
    return UINT32_MAX;
  return millis() - this->last_speech_ms_;
}

uint32_t WakewordRecorder::ms_since_capture() {
  LockGuard guard{this->mutex_};
  if (this->capture_started_ms_ == 0)
    return UINT32_MAX;
  return millis() - this->capture_started_ms_;
}

void WakewordRecorder::reset_speech() {
  LockGuard guard{this->mutex_};
  this->speech_started_ = false;
  this->speech_since_ = 0;
  this->last_speech_ms_ = 0;
  this->capture_started_ms_ = millis();
}

bool WakewordRecorder::start_capture(bool manual) {
  LockGuard guard{this->mutex_};
  if (this->upload_pending_ || this->uploading_) {
    this->capture_after_upload_ = true;
    ESP_LOGI(TAG, "Next capture queued while upload is in progress");
    return true;
  }
  if (this->is_failed() || this->recording_) {
    ESP_LOGW(TAG, "Capture ignored: recorder is unavailable or busy");
    return false;
  }
  this->manual_capture_ = manual;
  this->capture_duration_ms_ = manual ? this->manual_duration_ms_ : this->max_duration_ms_;
  const size_t target_bytes = static_cast<size_t>(OUTPUT_SAMPLE_RATE) * this->capture_duration_ms_ / 1000 * 2;
  this->pcm_.clear();
  this->pcm_.reserve(target_bytes);
  if (this->pcm_.capacity() < target_bytes) {
    ESP_LOGE(TAG, "Could not reserve %u bytes for audio", static_cast<unsigned>(target_bytes));
    return false;
  }
  this->source_sample_index_ = 0;
  this->write_position_ = 0;
  this->buffer_full_ = false;
  this->recording_ = true;
  // The noise floor stays: it describes the room, not the capture.
  // Everything else starts fresh per capture.
  this->speech_started_ = false;
  this->speech_since_ = 0;
  this->last_speech_ms_ = 0;
  this->capture_started_ms_ = millis();
  ESP_LOGI(TAG, "Buffering wake-word audio for %s", this->device_.c_str());
  return true;
}

bool WakewordRecorder::finish_capture(const std::string &transcript, const std::string &kind) {
  LockGuard guard{this->mutex_};
  if (!this->recording_ || this->pcm_.empty()) {
    ESP_LOGW(TAG, "No active capture to finish");
    return false;
  }
  this->recording_ = false;
  this->transcript_ = transcript;
  this->kind_ = kind;
  this->upload_pending_ = true;
  return true;
}

void WakewordRecorder::mark_no_input() {
  LockGuard guard{this->mutex_};
  if (this->upload_pending_ && this->kind_ == "trigger")
    this->kind_ = "trigger_no_input";
}

void WakewordRecorder::discard_capture() {
  LockGuard guard{this->mutex_};
  this->recording_ = false;
  this->capture_after_upload_ = false;
  if (!this->upload_pending_ && !this->uploading_) {
    this->transcript_.clear();
    this->kind_.clear();
    this->pcm_.clear();
    this->write_position_ = 0;
    this->buffer_full_ = false;
  }
}

bool WakewordRecorder::is_busy() {
  LockGuard guard{this->mutex_};
  return this->recording_ || this->upload_pending_ || this->uploading_;
}

void WakewordRecorder::track_level_(const std::vector<uint8_t> &data) {
  uint64_t sum = 0;
  size_t count = 0;
  for (size_t offset = 0; offset + 1 < data.size(); offset += 2) {
    const int16_t sample = static_cast<int16_t>(static_cast<uint16_t>(data[offset]) |
                                               (static_cast<uint16_t>(data[offset + 1]) << 8));
    sum += static_cast<uint32_t>(static_cast<int32_t>(sample) * sample);
    count++;
  }
  if (count == 0)
    return;
  const float rms = std::sqrt(static_cast<float>(sum) / static_cast<float>(count));
  this->level_ = rms;

  // Envelope: follows every rise at once and decays with about 150 ms. It
  // bridges pauses between syllables without delaying the end of speech.
  const float dt = static_cast<float>(count) / static_cast<float>(this->source_rate_);
  this->envelope_ = std::max(rms, this->envelope_ * std::exp(-dt / ENVELOPE_RELEASE_S));

  // The noise floor falls at once and rises very slowly: it follows the
  // room's noise and does not chase speech.
  if (this->noise_floor_ <= 0.0f || rms < this->noise_floor_) {
    this->noise_floor_ = rms;
  } else {
    this->noise_floor_ += (rms - this->noise_floor_) * 0.002f;
  }

  const float threshold = std::max(this->noise_floor_ * SPEECH_FACTOR, SPEECH_FLOOR_MIN);
  const uint32_t now = millis();
  if (this->envelope_ > threshold) {
    if (this->speech_since_ == 0)
      this->speech_since_ = now;
    if (now - this->speech_since_ >= SPEECH_MIN_MS) {
      this->speech_started_ = true;
      this->last_speech_ms_ = now;
    }
  } else {
    this->speech_since_ = 0;
  }
}

void WakewordRecorder::receive_audio_(const std::vector<uint8_t> &data) {
  LockGuard guard{this->mutex_};
  // The level is always tracked, also without a capture, so the noise floor
  // has settled when a capture begins.
  this->track_level_(data);
  if (!this->recording_)
    return;
  const size_t target_bytes = static_cast<size_t>(OUTPUT_SAMPLE_RATE) * this->capture_duration_ms_ / 1000 * 2;
  for (size_t offset = 0; offset + 1 < data.size(); offset += 2) {
    if ((this->source_sample_index_++ % this->decimation_) != 0)
      continue;
    if (!this->buffer_full_) {
      this->pcm_.push_back(data[offset]);
      this->pcm_.push_back(data[offset + 1]);
      this->write_position_ = this->pcm_.size();
      if (this->pcm_.size() >= target_bytes) {
        this->write_position_ = 0;
        this->buffer_full_ = true;
      }
    } else if (!this->manual_capture_) {
      this->pcm_[this->write_position_] = data[offset];
      this->pcm_[this->write_position_ + 1] = data[offset + 1];
      this->write_position_ = (this->write_position_ + 2) % target_bytes;
    }
  }
  // Once full, retain the most recent max_duration seconds. This includes the
  // wake word that was heard before the voice assistant entered listening.
}

void WakewordRecorder::loop() {
}

bool WakewordRecorder::upload_capture() {
  std::string transcript;
  std::string kind;
  bool capture_after_upload = false;
  {
    LockGuard guard{this->mutex_};
    if (!this->upload_pending_)
      return false;
    transcript = this->transcript_;
    kind = this->kind_;
    this->upload_pending_ = false;
    this->uploading_ = true;
  }
  this->upload_(transcript, kind);
  {
    LockGuard guard{this->mutex_};
    this->pcm_.clear();
    this->write_position_ = 0;
    this->buffer_full_ = false;
    this->transcript_.clear();
    this->kind_.clear();
    this->uploading_ = false;
    capture_after_upload = this->capture_after_upload_;
    this->capture_after_upload_ = false;
  }
  if (capture_after_upload)
    this->start_capture();
  return true;
}

static void put_u16(char *out, uint16_t value) {
  out[0] = static_cast<char>(value & 0xFF);
  out[1] = static_cast<char>((value >> 8) & 0xFF);
}

static void put_u32(char *out, uint32_t value) {
  put_u16(out, value & 0xFFFF);
  put_u16(out + 2, value >> 16);
}

static std::array<char, 44> build_wav_header(size_t pcm_size) {
  std::array<char, 44> header{};
  memcpy(header.data(), "RIFF", 4);
  put_u32(header.data() + 4, 36 + pcm_size);
  memcpy(header.data() + 8, "WAVEfmt ", 8);
  put_u32(header.data() + 16, 16);
  put_u16(header.data() + 20, 1);
  put_u16(header.data() + 22, 1);
  put_u32(header.data() + 24, OUTPUT_SAMPLE_RATE);
  put_u32(header.data() + 28, OUTPUT_SAMPLE_RATE * 2);
  put_u16(header.data() + 32, 2);
  put_u16(header.data() + 34, 16);
  memcpy(header.data() + 36, "data", 4);
  put_u32(header.data() + 40, pcm_size);
  return header;
}

#ifdef USE_ESP_IDF
static bool write_all(esp_http_client_handle_t client, const char *data, size_t size) {
  while (size > 0) {
    const int written = esp_http_client_write(client, data, static_cast<int>(size));
    if (written <= 0)
      return false;
    data += written;
    size -= written;
  }
  return true;
}
#endif

void WakewordRecorder::upload_(const std::string &transcript, const std::string &kind) {
  std::string safe_transcript = transcript;
  std::replace(safe_transcript.begin(), safe_transcript.end(), '\r', ' ');
  std::replace(safe_transcript.begin(), safe_transcript.end(), '\n', ' ');
  const auto header = build_wav_header(this->pcm_.size());
  const size_t first_offset = this->buffer_full_ ? this->write_position_ : 0;
  const size_t first_size = this->pcm_.size() - first_offset;
  const size_t second_size = first_offset;
#ifdef USE_ESP_IDF
  esp_http_client_config_t config{};
  config.url = this->url_.c_str();
  config.method = HTTP_METHOD_POST;
  config.timeout_ms = 10000;
#if CONFIG_MBEDTLS_CERTIFICATE_BUNDLE
  config.crt_bundle_attach = esp_crt_bundle_attach;
#endif
  esp_http_client_handle_t client = esp_http_client_init(&config);
  if (client == nullptr) {
    ESP_LOGE(TAG, "Could not initialize upload for %s", this->device_.c_str());
    return;
  }
  esp_http_client_set_header(client, "Content-Type", "audio/wav");
  esp_http_client_set_header(client, "X-Wakeword-Token", this->token_.c_str());
  esp_http_client_set_header(client, "X-Wakeword-Device", this->device_.c_str());
  // The ESPHome node name, so Home Assistant can find this satellite's entities.
  const std::string node = App.get_name().c_str();
  esp_http_client_set_header(client, "X-Wakeword-Node", node.c_str());
  esp_http_client_set_header(client, "X-Wakeword-Transcript", safe_transcript.c_str());
  if (!kind.empty())
    esp_http_client_set_header(client, "X-Wakeword-Kind", kind.c_str());
  const int body_size = static_cast<int>(header.size() + this->pcm_.size());
  esp_err_t error = esp_http_client_open(client, body_size);
  bool wrote_body = error == ESP_OK && write_all(client, header.data(), header.size()) &&
                    write_all(client, reinterpret_cast<const char *>(this->pcm_.data() + first_offset), first_size) &&
                    (second_size == 0 ||
                     write_all(client, reinterpret_cast<const char *>(this->pcm_.data()), second_size));
  if (!wrote_body) {
    ESP_LOGE(TAG, "Upload write failed for %s (%s)", this->device_.c_str(), esp_err_to_name(error));
    esp_http_client_close(client);
    esp_http_client_cleanup(client);
    return;
  }
  esp_http_client_fetch_headers(client);
  const int status = esp_http_client_get_status_code(client);
  ESP_LOGI(TAG, "Uploaded %u bytes for %s (HTTP %d)", static_cast<unsigned>(body_size), this->device_.c_str(), status);
  esp_http_client_close(client);
  esp_http_client_cleanup(client);
#else
  std::string wav(header.data(), header.size());
  wav.append(reinterpret_cast<const char *>(this->pcm_.data() + first_offset), first_size);
  wav.append(reinterpret_cast<const char *>(this->pcm_.data()), second_size);
  std::vector<http_request::Header> headers{
      {"Content-Type", "audio/wav"}, {"X-Wakeword-Token", this->token_}, {"X-Wakeword-Device", this->device_}};
  headers.push_back({"X-Wakeword-Transcript", safe_transcript});
  headers.push_back({"X-Wakeword-Node", std::string(App.get_name().c_str())});
  if (!kind.empty())
    headers.push_back({"X-Wakeword-Kind", kind});
  auto response = this->http_request_->post(this->url_, wav, headers);
  if (response == nullptr) {
    ESP_LOGE(TAG, "Upload failed for %s", this->device_.c_str());
    return;
  }
  ESP_LOGI(TAG, "Uploaded %u bytes for %s (HTTP %d)", static_cast<unsigned>(wav.size()), this->device_.c_str(),
           response->status_code);
  response->end();
#endif
}

}  // namespace esphome::wakeword_recorder
