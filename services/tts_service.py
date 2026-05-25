from adapters.tts.edge_tts import EdgeTTS


class TTSService:

    def __init__(self):
        self.tts = EdgeTTS()

    async def create(self, text: str) -> str:
        """将文本转为语音，返回 WAV 文件绝对路径"""
        return await self.tts.generate(text)
