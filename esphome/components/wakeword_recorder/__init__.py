"""ESPHome component for collecting short, labelled wake-word WAV clips."""

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.automation import maybe_simple_id
from esphome.const import CONF_ID, CONF_MICROPHONE, CONF_URL

from esphome import automation
from esphome.components import http_request, microphone

DEPENDENCIES = ["http_request", "microphone"]
CODEOWNERS = []

CONF_DEVICE = "device"
CONF_HTTP_REQUEST_ID = "http_request_id"
CONF_TOKEN = "token"
CONF_MAX_DURATION = "max_duration"

recorder_ns = cg.esphome_ns.namespace("wakeword_recorder")
WakewordRecorder = recorder_ns.class_("WakewordRecorder", cg.Component)
CaptureAction = recorder_ns.class_("CaptureAction", automation.Action)
FinishAction = recorder_ns.class_("FinishAction", automation.Action)
DiscardAction = recorder_ns.class_("DiscardAction", automation.Action)
UploadAction = recorder_ns.class_("UploadAction", automation.Action)
HoldAction = recorder_ns.class_("HoldAction", automation.Action)
ReleaseAction = recorder_ns.class_("ReleaseAction", automation.Action)

CONF_HOLD_SOURCE_ID = "hold_source_id"

CONFIG_SCHEMA = cv.Schema(
    {
        cv.GenerateID(): cv.declare_id(WakewordRecorder),
        cv.Required(CONF_MICROPHONE): microphone.microphone_source_schema(
            min_bits_per_sample=16,
            max_bits_per_sample=16,
            min_channels=1,
            max_channels=1,
        ),
        # Second, active handle on the same microphone. It has no callback
        # and only keeps the driver running, see set_hold_source().
        cv.GenerateID(CONF_HOLD_SOURCE_ID): cv.declare_id(microphone.MicrophoneSource),
        cv.GenerateID(CONF_HTTP_REQUEST_ID): cv.use_id(http_request.HttpRequestComponent),
        cv.Required(CONF_URL): cv.url,
        cv.Required(CONF_TOKEN): cv.string_strict,
        cv.Required(CONF_DEVICE): cv.All(cv.string_strict, cv.Length(min=1, max=64)),
        # Up to 120 s for longer recordings than wake words. The buffer lives
        # in PSRAM and is reserved when a capture starts; if that fails,
        # start_capture() logs it and aborts cleanly. 8 s suit wake words.
        cv.Optional(CONF_MAX_DURATION, default="8s"): cv.All(
            cv.positive_time_period_milliseconds,
            cv.Range(min=cv.TimePeriod(seconds=2), max=cv.TimePeriod(seconds=120)),
        ),
    }
).extend(cv.COMPONENT_SCHEMA)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    mic_source = await microphone.microphone_source_to_code(config[CONF_MICROPHONE], passive=True)
    # The second, active handle: start() and stop() take effect. The I2S
    # driver counts its listeners, so holding does not disturb micro_wake_word.
    mic = await cg.get_variable(config[CONF_MICROPHONE][CONF_MICROPHONE])
    hold_source = cg.new_Pvariable(config[CONF_HOLD_SOURCE_ID], mic, 16, 1, False)
    cg.add(hold_source.add_channel(0))
    request = await cg.get_variable(config[CONF_HTTP_REQUEST_ID])
    cg.add(var.set_microphone_source(mic_source))
    cg.add(var.set_hold_source(hold_source))
    cg.add(var.set_http_request(request))
    cg.add(var.set_url(config[CONF_URL]))
    cg.add(var.set_token(config[CONF_TOKEN]))
    cg.add(var.set_device(config[CONF_DEVICE]))
    cg.add(var.set_max_duration_ms(config[CONF_MAX_DURATION].total_milliseconds))


@automation.register_action(
    "wakeword_recorder.capture",
    CaptureAction,
    maybe_simple_id({cv.GenerateID(): cv.use_id(WakewordRecorder)}),
    synchronous=True,
)
async def capture_action_to_code(config, action_id, template_arg, args):
    var = cg.new_Pvariable(action_id, template_arg)
    recorder = await cg.get_variable(config[CONF_ID])
    cg.add(var.set_parent(recorder))
    return var


@automation.register_action(
    "wakeword_recorder.finish",
    FinishAction,
    cv.Schema(
        {
            cv.GenerateID(): cv.use_id(WakewordRecorder),
            cv.Required("transcript"): cv.templatable(cv.string_strict),
        }
    ),
    synchronous=True,
)
async def finish_action_to_code(config, action_id, template_arg, args):
    var = cg.new_Pvariable(action_id, template_arg)
    recorder = await cg.get_variable(config[CONF_ID])
    cg.add(var.set_parent(recorder))
    template_ = await cg.templatable(config["transcript"], args, cg.std_string)
    cg.add(var.set_transcript(template_))
    return var


@automation.register_action(
    "wakeword_recorder.discard",
    DiscardAction,
    maybe_simple_id({cv.GenerateID(): cv.use_id(WakewordRecorder)}),
    synchronous=True,
)
async def discard_action_to_code(config, action_id, template_arg, args):
    var = cg.new_Pvariable(action_id, template_arg)
    recorder = await cg.get_variable(config[CONF_ID])
    cg.add(var.set_parent(recorder))
    return var


@automation.register_action(
    "wakeword_recorder.upload",
    UploadAction,
    maybe_simple_id({cv.GenerateID(): cv.use_id(WakewordRecorder)}),
    synchronous=True,
)
async def upload_action_to_code(config, action_id, template_arg, args):
    var = cg.new_Pvariable(action_id, template_arg)
    recorder = await cg.get_variable(config[CONF_ID])
    cg.add(var.set_parent(recorder))
    return var


@automation.register_action(
    "wakeword_recorder.hold",
    HoldAction,
    maybe_simple_id({cv.GenerateID(): cv.use_id(WakewordRecorder)}),
    synchronous=True,
)
async def hold_action_to_code(config, action_id, template_arg, args):
    var = cg.new_Pvariable(action_id, template_arg)
    recorder = await cg.get_variable(config[CONF_ID])
    cg.add(var.set_parent(recorder))
    return var


@automation.register_action(
    "wakeword_recorder.release",
    ReleaseAction,
    maybe_simple_id({cv.GenerateID(): cv.use_id(WakewordRecorder)}),
    synchronous=True,
)
async def release_action_to_code(config, action_id, template_arg, args):
    var = cg.new_Pvariable(action_id, template_arg)
    recorder = await cg.get_variable(config[CONF_ID])
    cg.add(var.set_parent(recorder))
    return var
