import numpy as np
from const import SERVER_DEVICE_SAMPLE_RATES

from queue import Queue
import logging
from voice_changer.VoiceChangerSettings import VoiceChangerSettings
from voice_changer.Local.AudioDeviceList import checkSamplingRate, list_audio_device
import time
import sounddevice as sd
import librosa

from voice_changer.utils.VoiceChangerModel import AudioInOut
from typing import Protocol
from typing import Union

logger = logging.getLogger(__name__)

ERR_SAMPLE_RATE_NOT_SUPPORTED = """Specified sample rate is not supported by all selected audio devices.
Available sample rates:
  [Input]: %s
  [Output]: %s
  [Monitor]: %s"""
ERR_GENERIC_SERVER_AUDIO_ERROR = "A server audio error occurred."

class ServerDeviceCallbacks(Protocol):
    def on_request(self, unpackedData: AudioInOut) -> tuple[AudioInOut, list[Union[int, float]]]:
        ...

    def emitTo(self, volume: float, performance: list[float], err: tuple[str, str] | None):
        ...


class ServerDevice:
    def __init__(self, serverDeviceCallbacks: ServerDeviceCallbacks, settings: VoiceChangerSettings):
        self.settings = settings
        self.serverDeviceCallbacks = serverDeviceCallbacks
        self.mon_wav = None
        self.serverAudioInputDevices = None
        self.serverAudioOutputDevices = None
        self.monQueue = Queue()
        self.performance = [0, 0, 0]

        self.control_loop = False
        self.stream_loop = False

    def getServerInputAudioDevice(self, index: int):
        audioinput, _audiooutput = list_audio_device()
        serverAudioDevice = [x for x in audioinput if x.index == index]
        if len(serverAudioDevice) > 0:
            return serverAudioDevice[0]
        else:
            return None

    def getServerOutputAudioDevice(self, index: int):
        _audioinput, audiooutput = list_audio_device()
        serverAudioDevice = [x for x in audiooutput if x.index == index]
        if len(serverAudioDevice) > 0:
            return serverAudioDevice[0]
        else:
            return None

    ###########################################
    # Callback Section
    ###########################################

    def _processData(self, indata: np.ndarray):
        indata = indata * self.settings.serverInputAudioGain
        unpackedData = librosa.to_mono(indata.T)
        return self.serverDeviceCallbacks.on_request(unpackedData)

    def _processDataWithTime(self, indata: np.ndarray):
        out_wav, vol, perf, err = self._processData(indata)
        self.performance = perf
        self.serverDeviceCallbacks.emitTo(vol, self.performance, err)
        return out_wav

    def audio_stream_callback(self, indata: np.ndarray, outdata: np.ndarray, frames, times, status):
        try:
            out_wav = self._processDataWithTime(indata)
            outputChannels = outdata.shape[1]
            outdata[:] = (np.repeat(out_wav, outputChannels).reshape(-1, outputChannels) * self.settings.serverOutputAudioGain)
        except Exception as e:
            self.serverDeviceCallbacks.emitTo(0, self.performance, ('ERR_GENERIC_SERVER_AUDIO_ERROR', ERR_GENERIC_SERVER_AUDIO_ERROR))
            logger.exception(e)

    def audio_stream_callback_mon_queue(self, indata: np.ndarray, outdata: np.ndarray, frames, times, status):
        try:
            out_wav = self._processDataWithTime(indata)
            self.monQueue.put(out_wav)
            outputChannels = outdata.shape[1]
            outdata[:] = (np.repeat(out_wav, outputChannels).reshape(-1, outputChannels) * self.settings.serverOutputAudioGain)
        except Exception as e:
            self.serverDeviceCallbacks.emitTo(0, self.performance, ('ERR_GENERIC_SERVER_AUDIO_ERROR', ERR_GENERIC_SERVER_AUDIO_ERROR))
            logger.exception(e)

    def audio_monitor_callback(self, outdata: np.ndarray, frames, times, status):
        try:
            mon_wav = self.monQueue.get()
            while self.monQueue.qsize() > 0:
                self.monQueue.get()
            outputChannels = outdata.shape[1]
            outdata[:] = (np.repeat(mon_wav, outputChannels).reshape(-1, outputChannels) * self.settings.serverMonitorAudioGain)
        except Exception as e:
            self.serverDeviceCallbacks.emitTo(0, self.performance, ('ERR_GENERIC_SERVER_AUDIO_ERROR', ERR_GENERIC_SERVER_AUDIO_ERROR))
            logger.exception(e)

    ###########################################
    # Main Loop Section
    ###########################################
    def run_no_monitor(self, block_frame: int, inputMaxChannel: int, outputMaxChannel: int, inputExtraSetting, outputExtraSetting):
        with (
            sd.Stream(callback=self.audio_stream_callback, latency='low', dtype="float32", device=(self.settings.serverInputDeviceId, self.settings.serverOutputDeviceId), blocksize=block_frame, samplerate=self.settings.serverInputAudioSampleRate, channels=(inputMaxChannel, outputMaxChannel), extra_settings=(inputExtraSetting, outputExtraSetting)),
        ):
            while self.stream_loop:
                time.sleep(1)

    def run_with_monitor(self, block_frame: int, inputMaxChannel: int, outputMaxChannel: int, monitorMaxChannel: int, inputExtraSetting, outputExtraSetting, monitorExtraSetting):
        with (
            sd.Stream(callback=self.audio_stream_callback_mon_queue, latency='low', dtype="float32", device=(self.settings.serverInputDeviceId, self.settings.serverOutputDeviceId), blocksize=block_frame, samplerate=self.settings.serverInputAudioSampleRate, channels=(inputMaxChannel, outputMaxChannel), extra_settings=(inputExtraSetting, outputExtraSetting)),
            sd.OutputStream(callback=self.audio_monitor_callback, dtype="float32", device=self.settings.serverMonitorDeviceId, blocksize=block_frame, samplerate=self.settings.serverMonitorAudioSampleRate, channels=monitorMaxChannel, extra_settings=monitorExtraSetting),
        ):
            while self.stream_loop:
                time.sleep(1)

    ###########################################
    # Start Section
    ###########################################
    def start(self):
        while True:
            if not self.control_loop:
                time.sleep(1)
                continue

            sd._terminate()
            sd._initialize()

            # Device 特定
            serverInputAudioDevice = self.getServerInputAudioDevice(self.settings.serverInputDeviceId)
            serverOutputAudioDevice = self.getServerOutputAudioDevice(self.settings.serverOutputDeviceId)
            serverMonitorAudioDevice = self.getServerOutputAudioDevice(self.settings.serverMonitorDeviceId)

            # Generate ExtraSetting
            inputExtraSetting = None
            outputExtraSetting = None
            if serverInputAudioDevice and "WASAPI" in serverInputAudioDevice.hostAPI:
                inputExtraSetting = sd.WasapiSettings(exclusive=bool(self.settings.exclusiveMode))
            if serverOutputAudioDevice and "WASAPI" in serverOutputAudioDevice.hostAPI:
                outputExtraSetting = sd.WasapiSettings(exclusive=bool(self.settings.exclusiveMode))

            monitorExtraSetting = None
            if serverMonitorAudioDevice and "WASAPI" in serverMonitorAudioDevice.hostAPI:
                monitorExtraSetting = sd.WasapiSettings(exclusive=bool(self.settings.exclusiveMode))

            logger.info("Devices:")
            logger.info(f"  [Input]: {serverInputAudioDevice} {inputExtraSetting}")
            logger.info(f"  [Output]: {serverOutputAudioDevice}, {outputExtraSetting}")
            logger.info(f"  [Monitor]: {serverMonitorAudioDevice}, {monitorExtraSetting}")

            # Deviceがなかったらいったんスリープ
            if serverInputAudioDevice is None or serverOutputAudioDevice is None:
                logger.error("Input or output device is not selected.")
                self.serverDeviceCallbacks.emitTo(0, self.performance, ('ERR_GENERIC_SERVER_AUDIO_ERROR', ERR_GENERIC_SERVER_AUDIO_ERROR))
                time.sleep(2)
                continue

            # サンプリングレート
            # 同一サンプリングレートに統一（変換時にサンプルが不足する場合があるため。パディング方法が明らかになれば、それぞれ設定できるかも）
            self.settings.serverInputAudioSampleRate = self.settings.serverAudioSampleRate
            self.settings.serverOutputAudioSampleRate = self.settings.serverAudioSampleRate
            self.settings.serverMonitorAudioSampleRate = self.settings.serverAudioSampleRate

            # Sample Rate Check
            inputAudioSampleRateAvailable = checkSamplingRate(self.settings.serverInputDeviceId, self.settings.serverInputAudioSampleRate, "input")
            outputAudioSampleRateAvailable = checkSamplingRate(self.settings.serverOutputDeviceId, self.settings.serverOutputAudioSampleRate, "output")
            monitorAudioSampleRateAvailable = checkSamplingRate(self.settings.serverMonitorDeviceId, self.settings.serverMonitorAudioSampleRate, "output") if serverMonitorAudioDevice else True

            logger.info("Sample Rate:")
            logger.info(f"  [Input]: {self.settings.serverInputAudioSampleRate} -> {inputAudioSampleRateAvailable}")
            logger.info(f"  [Output]: {self.settings.serverOutputAudioSampleRate} -> {outputAudioSampleRateAvailable}")
            if serverMonitorAudioDevice is not None:
                logger.info(f"  [Monitor]: {self.settings.serverMonitorAudioSampleRate} -> {monitorAudioSampleRateAvailable}")

            # FIXME: Ideally, there are two options:
            # 1. UI must be provided with all sample rates and select only valid combinations of sample rates.
            # 2. Server must pick the default device sample rate automatically so UI doesn't have to bother.
            # This must be removed once it's done.
            if not inputAudioSampleRateAvailable or not outputAudioSampleRateAvailable or not monitorAudioSampleRateAvailable:
                logger.info("Checking Available Sample Rate:")
                availableInputSampleRate = []
                availableOutputSampleRate = []
                availableMonitorSampleRate = []
                for sr in SERVER_DEVICE_SAMPLE_RATES:
                    if checkSamplingRate(self.settings.serverInputDeviceId, sr, "input"):
                        availableInputSampleRate.append(sr)
                    if checkSamplingRate(self.settings.serverOutputDeviceId, sr, "output"):
                        availableOutputSampleRate.append(sr)
                    if serverMonitorAudioDevice is not None:
                        if checkSamplingRate(self.settings.serverMonitorDeviceId, sr, "output"):
                            availableMonitorSampleRate.append(sr)
                err = ERR_SAMPLE_RATE_NOT_SUPPORTED % (availableInputSampleRate, availableOutputSampleRate, availableMonitorSampleRate)
                self.serverDeviceCallbacks.emitTo(
                    0,
                    self.performance,
                    ('ERR_SAMPLE_RATE_NOT_SUPPORTED', err)
                )
                logger.error(err)
                time.sleep(2)
                continue

            # FIXME: In UI, block size is calculated based on 48kHz so we convert from 48kHz to input device sample rate.
            block_frame = int((self.settings.serverReadChunkSize * 128 / 48000) * self.settings.serverInputAudioSampleRate)

            try:
                self.stream_loop = True
                if serverMonitorAudioDevice is None:
                    self.run_no_monitor(block_frame, serverInputAudioDevice.maxInputChannels, serverOutputAudioDevice.maxOutputChannels, inputExtraSetting, outputExtraSetting)
                else:
                    self.run_with_monitor(block_frame, serverInputAudioDevice.maxInputChannels, serverOutputAudioDevice.maxOutputChannels, serverMonitorAudioDevice.maxOutputChannels, inputExtraSetting, outputExtraSetting, monitorExtraSetting)
            except Exception as e:
                self.serverDeviceCallbacks.emitTo(0, self.performance, ('ERR_GENERIC_SERVER_AUDIO_ERROR', ERR_GENERIC_SERVER_AUDIO_ERROR))
                logger.exception(e)
                time.sleep(2)

    ###########################################
    # Info Section
    ###########################################
    def get_info(self):
        data = {}
        try:
            audioinput, audiooutput = list_audio_device()
            self.serverAudioInputDevices = audioinput
            self.serverAudioOutputDevices = audiooutput
        except Exception as e:
            self.serverDeviceCallbacks.emitTo(0, self.performance, ('ERR_GENERIC_SERVER_AUDIO_ERROR', ERR_GENERIC_SERVER_AUDIO_ERROR))
            logger.exception(e)

        data["serverAudioInputDevices"] = self.serverAudioInputDevices
        data["serverAudioOutputDevices"] = self.serverAudioOutputDevices
        return data

    def update_settings(self, key: str, val, old_val):
        if key == 'serverAudioStated':
            # Toggle control loop
            self.control_loop = val
        if key in { 'serverAudioStated', 'serverInputDeviceId', 'serverOutputDeviceId', 'serverMonitorDeviceId', 'serverReadChunkSize', 'serverAudioSampleRate' }:
            # Break stream loop to reconfigure or turn server audio off
            self.stream_loop = False
