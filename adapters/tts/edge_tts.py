import edge_tts
import uuid
import os
import wave
import miniaudio

# 计算插件根目录（本文件位于 adapters/tts/，上两级即为插件根）
_ADAPTER_DIR = os.path.dirname(os.path.abspath(__file__))   # adapters/tts/
_ADAPTERS_DIR = os.path.dirname(_ADAPTER_DIR)               # adapters/
_PLUGIN_DIR = os.path.dirname(_ADAPTERS_DIR)                # 插件根目录
TEMP_DIR = os.path.join(_PLUGIN_DIR, "temp")

# edge_tts 固定输出 24kHz 单声道 MP3
_SAMPLE_RATE = 24000
_CHANNELS = 1
_SAMPLE_WIDTH = 2  # 16-bit


class EdgeTTS:

    async def generate(self, text: str, voice: str = "zh-CN-XiaoxiaoNeural") -> str:
        """
        将文字转为语音，返回 WAV 文件的绝对路径。
        edge_tts 输出 MP3，使用 miniaudio 解码后写成 WAV
        （QQ 语音只接受 WAV 格式，且 miniaudio 不依赖 ffmpeg）。
        """
        os.makedirs(TEMP_DIR, exist_ok=True)

        uid = str(uuid.uuid4())
        mp3_path = os.path.join(TEMP_DIR, f"{uid}.mp3")
        wav_path = os.path.join(TEMP_DIR, f"{uid}.wav")

        # 1. 生成 MP3
        communicate = edge_tts.Communicate(text=text, voice=voice)
        await communicate.save(mp3_path)

        # 2. 用 miniaudio 解码 MP3 → 原始 PCM（无需 ffmpeg）
        decoded = miniaudio.decode_file(
            mp3_path,
            output_format=miniaudio.SampleFormat.SIGNED16,
            nchannels=_CHANNELS,
            sample_rate=_SAMPLE_RATE,
        )

        # 3. 写入标准 WAV 文件
        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(_CHANNELS)
            wf.setsampwidth(_SAMPLE_WIDTH)
            wf.setframerate(_SAMPLE_RATE)
            wf.writeframes(bytes(decoded.samples))

        # 4. 删除临时 MP3
        try:
            os.remove(mp3_path)
        except OSError:
            pass

        return wav_path
