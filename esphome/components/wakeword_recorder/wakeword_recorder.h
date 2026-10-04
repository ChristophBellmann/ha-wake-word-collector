#pragma once

#include "esphome/components/http_request/http_request.h"
#include "esphome/components/microphone/microphone_source.h"
#include "esphome/core/automation.h"
#include "esphome/core/component.h"
#include "esphome/core/helpers.h"

#include <cstdint>
#include <string>
#include <vector>

namespace esphome::wakeword_recorder {

class WakewordRecorder : public Component {
 public:
  void setup() override;
  void loop() override;
  void dump_config() override;

  void set_microphone_source(microphone::MicrophoneSource *source) { this->microphone_source_ = source; }
  /// Second, **active** handle on the same microphone. It has no callback and
  /// only keeps the driver running. The I2S driver counts its listeners
  /// (active_listeners_semaphore_), so holding gets in nobody's way:
  /// micro_wake_word may still start and stop as it likes.
  void set_hold_source(microphone::MicrophoneSource *source) { this->hold_source_ = source; }
  void set_http_request(http_request::HttpRequestComponent *request) { this->http_request_ = request; }
  void set_url(const std::string &url) { this->url_ = url; }
  void set_token(const std::string &token) { this->token_ = token; }
  void set_device(const std::string &device) { this->device_ = device; }
  void set_max_duration_ms(uint32_t duration_ms) { this->max_duration_ms_ = duration_ms; }
  void set_manual_duration_ms(uint32_t duration_ms) { this->manual_duration_ms_ = duration_ms; }

  /// Hold the microphone. Without it the ring buffer only receives audio
  /// while someone else keeps the microphone running.
  void hold_microphone();
  void release_microphone();
  bool is_holding() { return this->holding_; }

  /// Level of the latest block, in counts (0 to 32767).
  float level();
  /// Envelope over the level; speech is decided on it.
  float envelope();
  /// Tracked noise floor. Falls at once, rises very slowly.
  float noise_floor();
  /// Was there any speech since the capture started?
  bool speech_started();
  /// Milliseconds since the last block with speech; very large without speech.
  uint32_t ms_since_speech();
  /// Milliseconds since the running capture started.
  uint32_t ms_since_capture();
  /// Reset speech detection to now without touching the capture: "has
  /// spoken" then means "since this moment". Used when waiting for an
  /// answer in the middle of a running capture.
  void reset_speech();

  bool start_capture(bool manual = false);
  /// kind: empty or "utterance" for a spoken example, "trigger" for the audio
  /// right before the wake word engine fired (sent as X-Wakeword-Kind).
  bool finish_capture(const std::string &transcript, const std::string &kind = "");
  bool upload_capture();
  void discard_capture();
  bool is_busy();

 protected:
  void receive_audio_(const std::vector<uint8_t> &data);
  void track_level_(const std::vector<uint8_t> &data);
  void upload_(const std::string &transcript, const std::string &kind);

  microphone::MicrophoneSource *microphone_source_{nullptr};
  microphone::MicrophoneSource *hold_source_{nullptr};
  http_request::HttpRequestComponent *http_request_{nullptr};
  std::string url_;
  std::string token_;
  std::string device_;
  uint32_t max_duration_ms_{8000};
  uint32_t manual_duration_ms_{30000};
  uint32_t capture_duration_ms_{8000};
  bool manual_capture_{false};
  uint32_t source_rate_{16000};
  uint32_t decimation_{1};
  uint32_t source_sample_index_{0};
  size_t write_position_{0};
  bool buffer_full_{false};
  Mutex mutex_;
  bool recording_{false};
  bool upload_pending_{false};
  bool uploading_{false};
  bool capture_after_upload_{false};
  bool holding_{false};
  float level_{0.0f};
  float envelope_{0.0f};
  float noise_floor_{0.0f};
  bool speech_started_{false};
  uint32_t speech_since_{0};
  uint32_t last_speech_ms_{0};
  uint32_t capture_started_ms_{0};
  std::string transcript_;
  std::string kind_;
  std::vector<uint8_t, RAMAllocator<uint8_t>> pcm_{RAMAllocator<uint8_t>(RAMAllocator<uint8_t>::ALLOC_EXTERNAL)};
};

template<typename... Ts> class HoldAction : public Action<Ts...> {
 public:
  void set_parent(WakewordRecorder *parent) { this->parent_ = parent; }
  void play(const Ts &...x) override { this->parent_->hold_microphone(); }

 protected:
  WakewordRecorder *parent_{nullptr};
};

template<typename... Ts> class ReleaseAction : public Action<Ts...> {
 public:
  void set_parent(WakewordRecorder *parent) { this->parent_ = parent; }
  void play(const Ts &...x) override { this->parent_->release_microphone(); }

 protected:
  WakewordRecorder *parent_{nullptr};
};

template<typename... Ts> class CaptureAction : public Action<Ts...> {
 public:
  void set_parent(WakewordRecorder *parent) { this->parent_ = parent; }
  void play(const Ts &...x) override { this->parent_->start_capture(); }

 protected:
  WakewordRecorder *parent_{nullptr};
};

template<typename... Ts> class FinishAction : public Action<Ts...> {
 public:
  void set_parent(WakewordRecorder *parent) { this->parent_ = parent; }
  TEMPLATABLE_VALUE(std::string, transcript)
  TEMPLATABLE_VALUE(std::string, kind)
  void play(const Ts &...x) override {
    this->parent_->finish_capture(this->transcript_.value(x...),
                                  this->kind_.has_value() ? this->kind_.value(x...) : std::string());
  }

 protected:
  WakewordRecorder *parent_{nullptr};
};

template<typename... Ts> class DiscardAction : public Action<Ts...> {
 public:
  void set_parent(WakewordRecorder *parent) { this->parent_ = parent; }
  void play(const Ts &...x) override { this->parent_->discard_capture(); }

 protected:
  WakewordRecorder *parent_{nullptr};
};

template<typename... Ts> class UploadAction : public Action<Ts...> {
 public:
  void set_parent(WakewordRecorder *parent) { this->parent_ = parent; }
  void play(const Ts &...x) override { this->parent_->upload_capture(); }

 protected:
  WakewordRecorder *parent_{nullptr};
};

}  // namespace esphome::wakeword_recorder
